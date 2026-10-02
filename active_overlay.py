"""
active_overlay.py
"Active" rain mode: a click-through, non-activating rain video overlay that sits
on top of the live desktop.  No screen capture, blur, or dimming.

  ActiveFrameProcessor  -- forwards frames to a worker thread that luminance-keys
                           each one into a tinted ARGB image (black -> transparent),
                           shared by all screens. The GUI thread never keys.
  ActiveRainOverlay     -- one input-transparent top-most window per screen
  FullscreenWatcher     -- polls for a full-screen foreground app per monitor
"""

import ctypes
import logging
import os
import threading
import time
from ctypes import wintypes

from PyQt6.QtCore import (
    Qt, QObject, QTimer, QElapsedTimer, QPropertyAnimation, QEasingCurve,
    pyqtSignal, QThread,
)
from PyQt6.QtGui import QImage, QPainter, QColor, QPaintDevice
from PyQt6.QtMultimedia import QVideoFrame
from PyQt6.QtWidgets import QWidget, QApplication

log = logging.getLogger("RainDelay.active")

BRIGHTNESS_PASSES = {"normal": 0, "bright": 1, "extra": 2}  # each pass doubles

# ── Win32 ───────────────────────────────────────────────────────────────
GWL_EXSTYLE = -20
WS_EX_LAYERED = 0x00080000
WS_EX_TRANSPARENT = 0x00000020
WS_EX_NOACTIVATE = 0x08000000
WS_EX_TOOLWINDOW = 0x00000080
WDA_NONE = 0x0
WDA_EXCLUDEFROMCAPTURE = 0x11
MONITOR_DEFAULTTONULL = 0

_SHELL_CLASSES = {"Progman", "WorkerW", "Shell_TrayWnd", "Shell_SecondaryTrayWnd"}

try:
    _user32 = ctypes.WinDLL("user32", use_last_error=True)
    _user32.GetWindowLongPtrW.restype = ctypes.c_ssize_t
    _user32.GetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int]
    _user32.SetWindowLongPtrW.restype = ctypes.c_ssize_t
    _user32.SetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_ssize_t]
    _user32.SetWindowDisplayAffinity.restype = wintypes.BOOL
    _user32.SetWindowDisplayAffinity.argtypes = [wintypes.HWND, wintypes.DWORD]
    _user32.GetForegroundWindow.restype = wintypes.HWND
    _user32.MonitorFromWindow.restype = wintypes.HMONITOR
    _user32.MonitorFromWindow.argtypes = [wintypes.HWND, wintypes.DWORD]
    _user32.GetMonitorInfoW.argtypes = [wintypes.HMONITOR, ctypes.c_void_p]
    _user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
    _user32.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    _user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
except (AttributeError, OSError):
    _user32 = None


class _MONITORINFO(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.DWORD), ("rcMonitor", wintypes.RECT),
                ("rcWork", wintypes.RECT), ("dwFlags", wintypes.DWORD)]


def _monitor_of(hwnd: int) -> int:
    if not _user32 or not hwnd:
        return 0
    return _user32.MonitorFromWindow(hwnd, MONITOR_DEFAULTTONULL) or 0


# ======================================================================= #
#  Frame processing
# ======================================================================= #

def _key_image(img: QImage, settings: dict) -> QImage:
    """Luminance-key a rain frame. Safe to call off the GUI thread."""
    img = img.convertToFormat(QImage.Format.Format_RGB32)

    cutoff = int(max(0, min(60, settings.get("active_black_level", 15))) * 2.55)
    if cutoff > 0:
        # invert, saturating-add, invert == max(0, x - cutoff)
        img.invertPixels()
        p = QPainter(img)
        p.setCompositionMode(QPainter.CompositionMode.CompositionMode_Plus)
        p.fillRect(img.rect(), QColor(cutoff, cutoff, cutoff))
        p.end()
        img.invertPixels()

    for _ in range(BRIGHTNESS_PASSES.get(settings.get("active_brightness", "normal"), 0)):
        src = img.copy()
        p = QPainter(img)
        p.setCompositionMode(QPainter.CompositionMode.CompositionMode_Plus)
        p.drawImage(0, 0, src)
        p.end()

    gray = img.convertToFormat(QImage.Format.Format_Grayscale8)
    out = QImage(img.size(), QImage.Format.Format_ARGB32_Premultiplied)
    tint = QColor(settings.get("active_tint", "#FFFFFF"))
    out.fill(tint if tint.isValid() else QColor(Qt.GlobalColor.white))
    out.setAlphaChannel(gray)
    return out


class _FrameKeyer(QThread):
    """Keys frames away from the GUI thread. Keeps only the newest submission."""

    keyed = pyqtSignal(QImage)

    def __init__(self, settings: dict, parent=None):
        super().__init__(parent)
        self.setObjectName("ActiveFrameKeyer")
        self._settings = dict(settings)
        self._lock = threading.Lock()
        self._pending: QImage | None = None
        self._wake = threading.Event()
        self._running = True
        self._perf_count = 0
        self._perf_accum = 0.0

    def submit(self, img: QImage):
        with self._lock:
            self._pending = img
        self._wake.set()

    def update_settings(self, settings: dict):
        with self._lock:
            self._settings = dict(settings)

    def shutdown(self):
        with self._lock:
            self._running = False
            self._pending = None
        self._wake.set()
        if not self.wait(3000):
            log.warning("Active frame keyer thread did not stop")

    def run(self):
        log.info("Active frame keyer thread started")
        while True:
            self._wake.wait()
            with self._lock:
                if not self._running:
                    return
                img = self._pending
                self._pending = None
                settings = self._settings
                self._wake.clear()
            if img is None or img.isNull():
                if img is not None:
                    self.keyed.emit(QImage())
                continue
            try:
                t0 = time.perf_counter()
                out = _key_image(img, settings)
                self._perf_accum += (time.perf_counter() - t0) * 1000
                self._perf_count += 1
                if self._perf_count >= 60:
                    log.debug("[PERF] active keying avg %.1fms/frame (%dx%d, background)",
                              self._perf_accum / self._perf_count, out.width(), out.height())
                    self._perf_count = 0
                    self._perf_accum = 0.0
                self.keyed.emit(out)
            except Exception:
                log.exception("Active frame keying failed")
                self.keyed.emit(QImage())


class ActiveFrameProcessor(QObject):
    """Turns black-background rain frames into tinted, per-pixel-alpha images.

    The video callback copies the frame on the GUI thread (hardware frames
    crash if mapped elsewhere) and drops work while a key is in flight.
    Keying itself runs on _FrameKeyer.
    """

    processed = pyqtSignal(QImage)

    def __init__(self, settings: dict, parent=None):
        super().__init__(parent)
        self._settings = settings
        self._clock = QElapsedTimer()
        self._clock.start()
        self._last_ms = -1_000_000
        self._busy = False
        self._in_frame = False
        self._stopped = False
        self._copy_n = 0
        self._copy_ms = 0.0
        self._keyer = _FrameKeyer(settings, self)
        self._keyer.keyed.connect(self._on_keyed)
        self._keyer.start()

    def update_settings(self, settings: dict):
        self._settings = settings
        self._keyer.update_settings(settings)

    def shutdown(self):
        """Stop the keyer thread. Call from the GUI thread before deletion."""
        if self._stopped:
            return
        self._stopped = True
        self._keyer.shutdown()

    def on_video_frame(self, frame: QVideoFrame):
        if self._stopped or self._busy or self._in_frame:
            return
        fps = max(1, int(self._settings.get("active_fps", 24)))
        now = self._clock.elapsed()
        # small slack so a 30fps source isn't decimated to 15 by timer jitter
        if now - self._last_ms < 1000.0 / fps - 4:
            return
        if not frame.isValid():
            return
        # Hold the gate across toImage(). That call can pump the event loop,
        # and a nested frame must not start a second copy.
        self._in_frame = True
        self._busy = True
        self._last_ms = now
        try:
            t0 = time.perf_counter()
            img = frame.toImage()
            if img.isNull():
                self._busy = False
                return
            # Deep copy before the player recycles the frame buffer.
            # toImage() on a hardware frame is not safe off this thread.
            if img.format() != QImage.Format.Format_RGB32:
                img = img.convertToFormat(QImage.Format.Format_RGB32)
            else:
                img = img.copy()
            self._copy_ms += (time.perf_counter() - t0) * 1000
            self._copy_n += 1
            if self._copy_n >= 60:
                log.debug("[PERF] active frame copy avg %.1fms (GUI)",
                          self._copy_ms / self._copy_n)
                self._copy_n = 0
                self._copy_ms = 0.0
            self._busy = True
            self._keyer.submit(img)
        except Exception:
            log.exception("Active frame copy failed")
            self._busy = False
        finally:
            self._in_frame = False

    def _on_keyed(self, img: QImage):
        self._busy = False
        if self._stopped or img.isNull():
            return
        self.processed.emit(img)


# ======================================================================= #
#  Overlay window
# ======================================================================= #

class ActiveRainOverlay(QWidget):
    """Click-through, non-activating, per-pixel-alpha rain window for one screen."""

    def __init__(self, settings: dict, target_screen=None):
        super().__init__(None, Qt.WindowType.FramelessWindowHint
                         | Qt.WindowType.WindowStaysOnTopHint
                         | Qt.WindowType.Tool
                         | Qt.WindowType.WindowTransparentForInput
                         | Qt.WindowType.WindowDoesNotAcceptFocus)
        self._settings = settings
        self._target_screen = target_screen
        self._frame: QImage | None = None
        self._suspended = False
        self._closing = False
        self._hmonitor = 0
        self._intensity = 1.0     # rain opacity multiplier (Rain Break ramp)
        self._flash_level = 0.0   # lightning white level 0..1
        self._fade_anim: QPropertyAnimation | None = None
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)

    def metric(self, metric):
        # Paint at logical resolution. The screen DPR (1.5 here) otherwise
        # builds a 3840x2400 backing store, and filling that from the GUI
        # thread costs ~30ms every frame on top of the frame copy.
        if metric == QPaintDevice.PaintDeviceMetric.PdmDevicePixelRatioScaled:
            return int(QPaintDevice.devicePixelRatioFScale())
        if metric == QPaintDevice.PaintDeviceMetric.PdmDevicePixelRatio:
            return 1
        return super().metric(metric)

    # ------------------------------------------------------------------ #
    def activate(self):
        screen = self._target_screen or QApplication.primaryScreen()
        if not screen:
            return
        geom = screen.geometry()
        self.setScreen(screen)
        self.setGeometry(geom)
        fade = self._settings.get("active_fade", True)
        self.setWindowOpacity(0.0 if fade else 1.0)
        # show() rather than showFullScreen() so Windows doesn't treat us as a full-screen app
        self.show()
        self._apply_win32_styles()
        self._hmonitor = _monitor_of(int(self.winId()))
        if fade:
            self._animate_opacity(0.0, 1.0, 800)
        log.info("Active overlay on %s (%dx%d)", screen.name(), geom.width(), geom.height())

    def deactivate(self, fade: bool = True):
        """Hide and schedule deletion; fades out first if enabled."""
        self._closing = True
        if fade and self._settings.get("active_fade", True) and self.isVisible():
            anim = self._animate_opacity(self.windowOpacity(), 0.0, 600)
            anim.finished.connect(self._finish_close)
        else:
            self._finish_close()

    def suspend(self):
        if not self._suspended:
            self._suspended = True
            self.hide()

    def resume(self):
        if self._suspended and not self._closing:
            self._suspended = False
            self.show()
            self._apply_win32_styles()

    def is_suspended(self) -> bool:
        return self._suspended

    def update_settings(self, settings: dict):
        self._settings = settings
        if self.isVisible():
            self._apply_capture_affinity()
            self.update()

    def monitor_handle(self) -> int:
        return self._hmonitor

    def set_intensity(self, level: float):
        self._intensity = max(0.0, min(1.0, level))
        self.update()

    def set_flash(self, level: float):
        self._flash_level = level
        if self.isVisible():
            self.update()

    # ------------------------------------------------------------------ #
    def on_processed(self, img: QImage):
        if self._suspended or self._closing or not self.isVisible():
            return
        # Keep the keyed frame at source size. Scaling it up to the physical
        # framebuffer here (e.g. 1920x1080 → 3840x2400) runs on the GUI thread
        # and, together with keying, was starving the event loop.
        self._frame = img
        self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setCompositionMode(QPainter.CompositionMode.CompositionMode_Source)
        p.fillRect(self.rect(), Qt.GlobalColor.transparent)
        if self._frame is not None and not self._frame.isNull():
            p.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceOver)
            p.setOpacity(max(0, min(100, self._settings.get("active_rain_opacity", 35))) / 100.0
                         * self._intensity)
            p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, False)
            p.drawImage(self.rect(), self._frame)
        if self._flash_level > 0:
            p.setOpacity(1.0)
            p.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceOver)
            p.fillRect(self.rect(), QColor(255, 255, 255, int(235 * self._flash_level)))
        p.end()

    # ------------------------------------------------------------------ #
    def _animate_opacity(self, start: float, end: float, ms: int) -> QPropertyAnimation:
        if self._fade_anim:
            self._fade_anim.stop()
        anim = QPropertyAnimation(self, b"windowOpacity", self)
        anim.setDuration(ms)
        anim.setStartValue(start)
        anim.setEndValue(end)
        anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        anim.start()
        self._fade_anim = anim
        return anim

    def _finish_close(self):
        if self._fade_anim:
            self._fade_anim.stop()
            self._fade_anim = None
        self._frame = None
        self.hide()
        self.deleteLater()

    def _apply_win32_styles(self):
        if not _user32:
            return
        hwnd = int(self.winId())
        ex = _user32.GetWindowLongPtrW(hwnd, GWL_EXSTYLE)
        ex |= WS_EX_LAYERED | WS_EX_TRANSPARENT | WS_EX_NOACTIVATE | WS_EX_TOOLWINDOW
        _user32.SetWindowLongPtrW(hwnd, GWL_EXSTYLE, ex)
        self._apply_capture_affinity()

    def _apply_capture_affinity(self):
        if not _user32:
            return
        mode = (WDA_EXCLUDEFROMCAPTURE if self._settings.get("active_hide_from_capture", True)
                else WDA_NONE)
        if not _user32.SetWindowDisplayAffinity(int(self.winId()), mode):
            log.info("SetWindowDisplayAffinity(0x%X) failed (err %d) — needs Win10 2004+",
                     mode, ctypes.get_last_error())


# ======================================================================= #
#  Full-screen app detection
# ======================================================================= #

class FullscreenWatcher(QObject):
    """Emits the HMONITOR hosting a full-screen foreground window (0 = none)."""

    fullscreen_monitor_changed = pyqtSignal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._current = 0
        self._pid = os.getpid()
        self._timer = QTimer(self)
        self._timer.setInterval(2000)
        self._timer.timeout.connect(self._poll)

    def start(self):
        self._current = 0
        if _user32:
            self._timer.start()

    def stop(self):
        self._timer.stop()
        if self._current:
            self._current = 0
            self.fullscreen_monitor_changed.emit(0)

    def _poll(self):
        mon = self._fullscreen_monitor()
        if mon != self._current:
            self._current = mon
            self.fullscreen_monitor_changed.emit(mon)

    def _fullscreen_monitor(self) -> int:
        hwnd = _user32.GetForegroundWindow()
        if not hwnd:
            return 0
        pid = wintypes.DWORD()
        _user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if pid.value == self._pid:
            return 0
        buf = ctypes.create_unicode_buffer(64)
        _user32.GetClassNameW(hwnd, buf, 64)
        if buf.value in _SHELL_CLASSES:
            return 0
        mon = _monitor_of(hwnd)
        if not mon:
            return 0
        info = _MONITORINFO()
        info.cbSize = ctypes.sizeof(_MONITORINFO)
        rect = wintypes.RECT()
        if not _user32.GetMonitorInfoW(mon, ctypes.byref(info)) or \
                not _user32.GetWindowRect(hwnd, ctypes.byref(rect)):
            return 0
        m = info.rcMonitor
        if (rect.left <= m.left and rect.top <= m.top
                and rect.right >= m.right and rect.bottom >= m.bottom):
            return mon
        return 0
