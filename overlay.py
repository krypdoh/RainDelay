"""
overlay.py
Full-screen rain overlay with blurred desktop background + video rain compositing.

Primary mode (VIDEO):
  1. Capture desktop screenshot, apply blur
  2. Load rain overlay video (rain filmed against black background)
  3. Each frame: draw blurred desktop, then composite rain video frame
     using QPainter Screen blend mode (black disappears, water shows)
  4. Loop the video seamlessly

Fallback mode (RENDERED):
  If no video file found in assets/, falls back to QPainter-rendered drops.

Video source:
  Place a rain overlay video (black background) in assets/rain.mp4
  Search 'rain overlay black background free' on Pexels, Pixabay, or YouTube.
  These are standard VFX assets used in film/video production.
"""

import os
import time
import logging
from pathlib import Path

from PyQt6.QtWidgets import QWidget, QApplication, QGraphicsView
from PyQt6.QtCore import Qt, QTimer, pyqtSignal, pyqtProperty, QRectF, QPointF, QDateTime, QElapsedTimer, QPropertyAnimation, QEasingCurve
from PyQt6.QtGui import (
    QPainter, QColor, QPen, QBrush, QPixmap, QImage, QTransform,
    QPainterPath, QRadialGradient, QLinearGradient, QFont, QFontDatabase,
)
from PyQt6.QtMultimedia import QVideoFrame
from PyQt6.QtWidgets import QGraphicsScene

log = logging.getLogger("RainDelay.overlay")

# PyInstaller onefile support: _MEIPASS is the temp folder for bundled data
import sys
_HERE = Path(getattr(sys, '_MEIPASS', Path(__file__).resolve().parent))
_ASSETS = _HERE / "assets"

# Supported video extensions
_VIDEO_EXTS = (".mp4", ".avi", ".mov", ".mkv", ".webm")


class _CustomGraphicsView(QGraphicsView):
    """Custom QGraphicsView that paints background and text overlay."""
    
    def __init__(self, scene, parent_overlay, parent=None):
        super().__init__(scene, parent)
        self._parent_overlay = parent_overlay
        self._frame_count = 0
        self._last_frame_time = 0.0
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        
    def drawBackground(self, painter, rect):
        """Paint the blurred desktop background before the video."""
        overlay = self._parent_overlay
        overlay._frame_pending = False  # allow next frame to be processed
        if overlay._bg_pixmap:
            # Scale pixmap to fill the entire scene, regardless of DPR
            scene_rect = self.scene().sceneRect()
            painter.drawPixmap(scene_rect.toRect(), overlay._bg_pixmap,
                               overlay._bg_pixmap.rect())
        else:
            painter.fillRect(rect, QColor(20, 20, 20))

        darkness = overlay._settings.get("darkness", 0)
        if darkness > 0:
            dark_alpha = int(darkness * 2.55)
            painter.fillRect(rect, QColor(0, 0, 0, dark_alpha))

        # Composite the latest video frame using alpha overlay:
        # rain_opacity (0-100) controls transparency of the rain layer.
        # Higher values make rain more solid/visible.
        px = overlay._current_video_pixmap
        if px and not px.isNull():
            scene_rect = self.scene().sceneRect()
            rain_opacity = overlay._settings.get("rain_opacity", 40) / 100.0
            painter.setOpacity(rain_opacity)
            painter.drawPixmap(scene_rect.toRect(), px)
            painter.setOpacity(1.0)
    
    def drawForeground(self, painter, rect):
        """Paint the wiper animation and text overlay on top of the video."""
        overlay = self._parent_overlay

        # Draw wiper if sweep animation is active
        if overlay._wiper_active and overlay._wiper_pixmap:
            overlay._paint_wiper(painter, rect)

        w = int(rect.width())
        h = int(rect.height())
        if overlay._show_text:
            self._parent_overlay._paint_text_overlay(painter, w, h)
        if overlay._exit_hint_visible:
            self._parent_overlay._paint_exit_hint(painter, w, h)

        # Performance tracking for video mode
        now = time.perf_counter()
        self._frame_count += 1
        overlay = self._parent_overlay
        overlay._frame_count += 1
        
        if overlay._perf_window_start == 0.0:
            overlay._perf_window_start = now
        
        # Log every N frames (use debug level to avoid disk I/O in paint path)
        if overlay._frame_count % overlay._fps_log_interval == 0:
            elapsed = max(now - overlay._perf_window_start, 1e-6)
            effective_fps = overlay._fps_log_interval / elapsed

            log.debug("[PERF] effective fps: %.1f | video: GPU rendering",
                      effective_fps)
            overlay._perf_window_start = now
    
    def mousePressEvent(self, event):
        self._parent_overlay._handle_mouse_press()
        event.accept()

    def keyPressEvent(self, event):
        self._parent_overlay._handle_key(event)
        event.accept()


import re as _re


def _find_rain_video(screen_w: int = 0, screen_h: int = 0):
    """Find the best-matching rain video for the target screen resolution.

    Search order:
      1. Exact resolution match (e.g. rain_1920x1200.mp4 for a 1920x1200 screen)
      2. Closest larger resolution (downscale is cheap)
      3. Closest smaller resolution (upscale with FastTransformation)
      4. Legacy names: rain_native.* → rain.* → rain_lowres.*
      5. Any video file in assets/
    """
    if not _ASSETS.exists():
        return None

    # Collect resolution-tagged videos: rain_WxH.ext
    _RES_PATTERN = _re.compile(r"rain_(\d+)x(\d+)", _re.IGNORECASE)
    candidates = []  # list of (path, w, h)
    for f in _ASSETS.iterdir():
        if f.suffix.lower() not in _VIDEO_EXTS:
            continue
        m = _RES_PATTERN.match(f.stem)
        if m:
            candidates.append((str(f), int(m.group(1)), int(m.group(2))))

    if candidates and screen_w > 0 and screen_h > 0:
        # Sort by how close the resolution is to the target screen
        # Prefer exact match, then closest larger, then closest smaller
        def _score(item):
            _, vw, vh = item
            dw, dh = vw - screen_w, vh - screen_h
            if dw == 0 and dh == 0:
                return (0, 0)  # exact match — best
            # Prefer larger (score 1) over smaller (score 2)
            tier = 1 if (dw >= 0 and dh >= 0) else 2
            return (tier, abs(dw) + abs(dh))

        candidates.sort(key=_score)
        best = candidates[0]
        log.info("  Resolution picker: screen=%dx%d → selected %s (%dx%d)",
                 screen_w, screen_h, Path(best[0]).name, best[1], best[2])
        return best[0]

    # Legacy fallback names
    for prefix in ("rain_native", "rain", "rain_lowres"):
        for ext in _VIDEO_EXTS:
            p = _ASSETS / f"{prefix}{ext}"
            if p.exists():
                return str(p)

    # Any video file in assets/
    for f in _ASSETS.iterdir():
        if f.suffix.lower() in _VIDEO_EXTS:
            return str(f)
    return None


class RainOverlay(QWidget):
    """Full-screen overlay: blurred desktop + rain video (or rendered fallback)."""

    dismiss = pyqtSignal()
    wiper_requested = pyqtSignal()  # emitted on W key; main.py triggers all overlays

    def __init__(self, settings: dict, target_screen=None):
        super().__init__()
        self._settings = settings
        self._target_screen = target_screen  # QScreen or None for primary
        self._bg_pixmap = None
        self._use_video = False
        self._video_sink = None       # unused (shared sink in main.py)
        self._current_video_pixmap = None  # Latest frame as QPixmap for Screen blend compositing
        self._frame_pending = False  # True when a frame is ready but not yet painted
        self._scene_size = None  # (w, h) of graphics scene for frame pre-scaling
        self._graphics_view = None  # QGraphicsView to display the video
        self._media_player = None  # unused (shared player in main.py)
        self._fade_anim = None    # QPropertyAnimation for fade-in
        self._show_text = False
        self._exit_hint_visible = False
        self._countdown_remaining_ms = 0  # set externally by main.py
        self._exit_hint_timer = QTimer(self)
        self._exit_hint_timer.setSingleShot(True)
        self._exit_hint_timer.setInterval(3000)
        self._exit_hint_timer.timeout.connect(self._hide_exit_hint)
        self._timer = QTimer(self)
        self._timer.setInterval(33)  # ~30fps for rendered fallback
        self._timer.timeout.connect(self._tick)
        # Performance tracking
        self._frame_count = 0
        self._perf_accum_ms = 0.0
        self._perf_window_start = 0.0
        self._last_tick_time = 0.0
        self._last_paint_time = 0.0
        self._fps_log_interval = 30  # log every N frames (~1 sec at 30fps)
        self._frame_skip = False  # set True if previous frame was slow
        self._slow_frame_count = 0  # track slow frames for diagnostics
        self._slow_paint_threshold = 33.0  # log paint times exceeding this (ms)
        # Video decode diagnostics
        self._paint_time_samples = []  # track paint times for statistics
        # Wiper animation state
        self._wiper_pixmap = None      # loaded wiper PNG
        self._wiper_active = False     # True during sweep animation
        self._wiper_angle_val = -70.0  # current sweep angle (degrees)
        self._wiper_anim = None        # QPropertyAnimation for the sweep
        self._last_video_pos = 0       # for detecting video loop restart
        self._video_duration = 0       # video duration in ms
        self._setup_window()

    def _setup_window(self):
        flags = (Qt.WindowType.FramelessWindowHint
                 | Qt.WindowType.WindowStaysOnTopHint
                 | Qt.WindowType.Tool)
        self.setWindowFlags(flags)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

    # ================================================================== #
    #  Show / hide
    # ================================================================== #

    def activate(self):
        t_start = time.perf_counter()
        screen = self._target_screen or QApplication.primaryScreen()
        if not screen:
            return
        geom = screen.geometry()
        self.setGeometry(geom)
        log.info("Activating overlay on %s (%dx%d, DPR=%.2f)",
                 screen.name(), geom.width(), geom.height(),
                 screen.devicePixelRatio())

        # Capture + blur desktop (grab full screen; Qt sets DPR on returned pixmap)
        screenshot = screen.grabWindow(0)
        self._bg_pixmap = self._blur_pixmap(screenshot)
        log.debug("  Background capture+blur: %.0fms",
                  (time.perf_counter() - t_start) * 1000)

        w, h = geom.width(), geom.height()

        # Try to set up video compositing view (actual player is in main.py)
        video_path = _find_rain_video(w, h)
        if video_path:
            log.info("  Using video rain: %s", video_path)
            try:
                self._setup_video_widget(video_path, w, h)
                self._use_video = True
                log.info("  [PERF] Video mode enabled - GPU-accelerated rendering")
            except Exception as e:
                log.warning("  Video init failed: %s -- falling back to rendered", e)
                self._use_video = False
        else:
            log.warning("  No video file found in assets/ — rain video required")

        self.setWindowOpacity(0.0)
        self.showFullScreen()
        self.raise_()
        self.activateWindow()
        if self._graphics_view:
            self._graphics_view.setFocus()
        else:
            self.setFocus()

        # Fade in from transparent to fully opaque over 500ms
        self._fade_anim = QPropertyAnimation(self, b"windowOpacity", self)
        self._fade_anim.setDuration(500)
        self._fade_anim.setStartValue(0.0)
        self._fade_anim.setEndValue(1.0)
        self._fade_anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        # Clear our reference when the animation finishes so deactivate() doesn't
        # try to call .stop() on an already-deleted C++ object.
        self._fade_anim.finished.connect(lambda: setattr(self, '_fade_anim', None))
        self._fade_anim.start(QPropertyAnimation.DeletionPolicy.DeleteWhenStopped)

        self._reset_perf_counters()
        if self._use_video:
            # Defer diagnostics to next event loop iteration so activate() returns fast
            QTimer.singleShot(0, self._log_video_diagnostics)
        self._timer.start()
        log.info("  Overlay activated in %.0fms",
                 (time.perf_counter() - t_start) * 1000)

    def deactivate(self):
        # Stop fade-in animation if still running
        if self._fade_anim:
            self._fade_anim.stop()
            self._fade_anim = None
        self._timer.stop()
        self._exit_hint_timer.stop()
        self._exit_hint_visible = False
        # Stop wiper animation
        if self._wiper_anim:
            self._wiper_anim.stop()
            self._wiper_anim = None
        self._wiper_active = False
        # No media player to stop — main.py owns the shared player
        self._current_video_pixmap = None
        self._frame_pending = False
        self._scene_size = None
        # Clean up graphics view
        if self._graphics_view:
            self._graphics_view.hide()
            self._graphics_view.deleteLater()
            self._graphics_view = None
        self._bg_pixmap = None
        self._use_video = False
        self._reset_perf_counters()
        self.setWindowOpacity(1.0)
        self.hide()

    def update_settings(self, settings: dict):
        self._settings = settings

    def set_countdown_remaining(self, ms: int):
        """Called by main.py to update the countdown remaining time."""
        self._countdown_remaining_ms = ms

    # ================================================================== #
    #  Video widget setup (receives frames from shared player in main.py)
    # ================================================================== #

    def _setup_video_widget(self, video_path: str, w: int, h: int):
        """Set up QGraphicsView for frame-by-frame compositing with Screen blend mode.
        
        NOTE: No QMediaPlayer is created here. main.py owns a single shared player
        and connects its videoFrameChanged signal to our _on_video_frame method.
        This avoids creating multiple D3D11 devices (which crashes on degraded GPU).
        """
        # Create graphics scene — drawBackground paints bg + composited video frame
        scene = QGraphicsScene()
        scene.setSceneRect(0, 0, w, h)
        self._scene_size = (w, h)  # used to pre-scale frames in _on_video_frame

        # Create custom graphics view as child of this overlay
        self._graphics_view = _CustomGraphicsView(scene, self, self)
        self._graphics_view.setGeometry(0, 0, w, h)
        self._graphics_view.setStyleSheet("background: transparent;")
        self._graphics_view.setFrameShape(QGraphicsView.Shape.NoFrame)
        self._graphics_view.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._graphics_view.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._graphics_view.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self._graphics_view.setSceneRect(0, 0, w, h)

        # Performance hints: disable antialiasing for video compositing
        self._graphics_view.setRenderHint(QPainter.RenderHint.Antialiasing, False)
        self._graphics_view.setRenderHint(
            QPainter.RenderHint.SmoothPixmapTransform, False)
        self._graphics_view.setOptimizationFlag(
            QGraphicsView.OptimizationFlag.DontAdjustForAntialiasing, True)

        # Show graphics view (playback is started by main.py after connecting signals)
        self._graphics_view.show()
        self._graphics_view.raise_()
        log.info("  [PERF] Video view ready for Screen blend compositing")

        # Load wiper image for loop-transition animation
        wiper_path = _ASSETS / "wiper_transparent_pro.png"
        if wiper_path.exists():
            raw = QPixmap(str(wiper_path))
            if not raw.isNull():
                # Pre-scale wiper to 72% of screen height so paint just rotates
                wiper_length = int(h * 0.72)
                scale_factor = wiper_length / raw.height()
                scaled_w = int(raw.width() * scale_factor)
                self._wiper_pixmap = raw.scaled(
                    scaled_w, wiper_length,
                    Qt.AspectRatioMode.IgnoreAspectRatio,
                    Qt.TransformationMode.SmoothTransformation)
                log.debug("  Wiper image loaded and pre-scaled: %dx%d",
                          self._wiper_pixmap.width(), self._wiper_pixmap.height())
            else:
                self._wiper_pixmap = None

    def _on_video_frame(self, frame: QVideoFrame):
        """Receive a decoded video frame, convert to QPixmap (GPU-resident), trigger repaint."""
        # Skip frame if previous hasn't been painted yet (avoids saturating
        # the event loop with frame conversions on multi-monitor setups)
        if self._frame_pending:
            return
        if not frame.isValid():
            return
        img = frame.toImage()
        if img.isNull():
            return
        # Convert to ARGB32_Premultiplied once on CPU, then upload to GPU as QPixmap.
        # QPixmap is GPU-resident so drawPixmap in paint is hardware-accelerated.
        # Pre-scale to scene size so drawPixmap does no scaling at paint time.
        img = img.convertToFormat(QImage.Format.Format_ARGB32_Premultiplied)
        if self._scene_size:
            w, h = self._scene_size
            if img.width() != w or img.height() != h:
                # FastTransformation (nearest-neighbor) is 5-10x faster than Smooth
                # and visually indistinguishable on rain-over-blur compositing
                img = img.scaled(w, h,
                                 Qt.AspectRatioMode.IgnoreAspectRatio,
                                 Qt.TransformationMode.FastTransformation)
        self._current_video_pixmap = QPixmap.fromImage(img)
        self._frame_pending = True
        if self._graphics_view:
            self._graphics_view.viewport().update()

    # ================================================================== #
    #  Blur
    # ================================================================== #

    def _blur_pixmap(self, px):
        """Fast blur via downscale/upscale. blur_strength controls blur amount."""
        blur_strength = self._settings.get("blur_strength", 50)
        if blur_strength == 0:
            return px  # no blur requested
        # Map 1-100 to scale factor 3-20 (higher scale = more blur)
        scale = max(3, min(20, int(blur_strength / 5) + 3))

        w, h = px.width(), px.height()
        small = px.scaled(w // scale, h // scale,
                          Qt.AspectRatioMode.IgnoreAspectRatio,
                          Qt.TransformationMode.SmoothTransformation)
        # Upscale with FastTransformation — result is intentionally blurry
        # so quality scaling adds no visible benefit, only CPU cost
        blurred = small.scaled(w, h,
                               Qt.AspectRatioMode.IgnoreAspectRatio,
                               Qt.TransformationMode.FastTransformation)
        return blurred

    # ================================================================== #
    #  Frame tick
    # ================================================================== #

    def _reset_perf_counters(self):
        self._frame_count = 0
        self._perf_accum_ms = 0.0
        self._perf_window_start = 0.0
        self._last_tick_time = 0.0
        self._last_paint_time = 0.0
        self._frame_skip = False
        self._slow_frame_count = 0
        self._paint_time_samples = []

    def _log_video_diagnostics(self):
        """Log detailed video subsystem diagnostics on startup"""
        if not self._use_video:
            return
        log.info("[DIAG] ========== VIDEO DIAGNOSTICS ==========")
        log.info("[DIAG] Display: %dx%d @ %.2f DPI scale", 
                 self.width(), self.height(), 
                 self.screen().devicePixelRatio() if self.screen() else 1.0)
        log.info("[DIAG] Rendering: Shared player → Screen blend compositing")
        log.info("[DIAG] Target: 30fps video playback with CPU compositing")
        log.info("[DIAG] =========================================")
        log.info("[PERF] Performance monitoring active...")

    def _tick(self):
        # In video mode, _on_video_frame already triggers repaints at video
        # framerate. Only force a repaint here for non-video mode (rendered
        # fallback) or when text overlay needs a clock/countdown refresh.
        if self._graphics_view and not self._use_video:
            self._graphics_view.viewport().update()

    # ================================================================== #
    #  Wiper sweep animation
    # ================================================================== #

    def _get_wiper_angle(self) -> float:
        return self._wiper_angle_val

    def _set_wiper_angle(self, angle: float) -> None:
        self._wiper_angle_val = angle
        # Request repaint so the wiper is drawn at the new angle
        if self._graphics_view:
            self._graphics_view.viewport().update()

    wiper_angle = pyqtProperty(float, _get_wiper_angle, _set_wiper_angle)

    def _on_position_changed(self, position: int) -> None:
        """Detect video loop restart by watching for position to jump backward."""
        if self._video_duration <= 0:
            self._last_video_pos = position
            return
        # Loop detected: position jumped from near the end to near the start
        if (self._last_video_pos > self._video_duration * 0.85
                and position < self._video_duration * 0.15):
            if self._settings.get("wiper_enabled", True):
                self._start_wiper_sweep()
        self._last_video_pos = position

    def trigger_wiper(self) -> None:
        """Manually trigger wiper sweep (video restart handled by main.py)."""
        if not self._settings.get("wiper_enabled", True):
            return
        if not self._wiper_pixmap or self._wiper_active:
            return
        self._start_wiper_sweep()

    def _start_wiper_sweep(self) -> None:
        """Trigger a single left-to-right wiper sweep animation."""
        if not self._wiper_pixmap or self._wiper_active:
            return
        log.debug("Wiper sweep triggered")
        self._wiper_active = True
        self._wiper_angle_val = -90.0

        self._wiper_anim = QPropertyAnimation(self, b"wiper_angle", self)
        self._wiper_anim.setDuration(1500)  # 1.5 second sweep
        self._wiper_anim.setStartValue(-90.0)   # start: upper-left (off-screen left)
        self._wiper_anim.setEndValue(135.0)     # end: past bottom-right (off-screen)
        self._wiper_anim.setEasingCurve(QEasingCurve.Type.InOutQuad)
        self._wiper_anim.finished.connect(self._on_wiper_finished)
        self._wiper_anim.start(QPropertyAnimation.DeletionPolicy.DeleteWhenStopped)

    def _on_wiper_finished(self) -> None:
        """Called when the wiper sweep completes."""
        self._wiper_active = False
        self._wiper_anim = None
        # One final repaint to clear the wiper from screen
        if self._graphics_view:
            self._graphics_view.viewport().update()

    def _paint_wiper(self, painter: QPainter, rect: QRectF) -> None:
        """Draw the pre-scaled wiper image rotated around a pivot at bottom-center."""
        w = int(rect.width())
        h = int(rect.height())

        # Pivot position: bottom-center of the screen, slightly below visible area
        pivot_x = w * 0.5
        pivot_y = h + 20

        img_h = self._wiper_pixmap.height()

        painter.save()
        # No SmoothPixmapTransform — wiper is pre-scaled and fast-moving
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, False)

        # Move origin to pivot, rotate, then draw image offset so pivot
        # aligns with the ball-joint corner (bottom-left of the image)
        painter.translate(pivot_x, pivot_y)
        # Angle 0 = wiper pointing straight up; negative = left, positive = right
        # The image's natural orientation has pivot at bottom-left pointing upper-right (~+45°)
        # Adjust by -45° so angle=0 means straight up
        painter.rotate(self._wiper_angle_val - 45.0)
        # Draw so the pivot corner (bottom-left of image) is at origin
        painter.drawPixmap(0, -img_h, self._wiper_pixmap)

        painter.restore()

    # ================================================================== #
    #  Painting
    # ================================================================== #

    def paintEvent(self, event):
        """QGraphicsView handles painting in video mode; paint hint in fallback."""
        if self._graphics_view:
            return
        if not self._exit_hint_visible:
            return
        p = QPainter(self)
        self._paint_exit_hint(p, self.width(), self.height())
        p.end()

    # ================================================================== #
    #  Text overlay (title + timer/clock)
    # ================================================================== #

    _archivo_loaded: str = ""  # class-level cache for font family name

    @classmethod
    def _archivo_family(cls) -> str:
        """Load Archivo Black from assets and return the family name."""
        if cls._archivo_loaded:
            return cls._archivo_loaded
        font_path = Path(__file__).resolve().parent / "assets" / "ArchivoBlack-Regular.ttf"
        if hasattr(sys, "_MEIPASS"):
            font_path = Path(sys._MEIPASS) / "assets" / "ArchivoBlack-Regular.ttf"
        if font_path.exists():
            font_id = QFontDatabase.addApplicationFont(str(font_path))
            if font_id >= 0:
                families = QFontDatabase.applicationFontFamilies(font_id)
                if families:
                    cls._archivo_loaded = families[0]
                    return cls._archivo_loaded
        # Fallback if font file not found
        cls._archivo_loaded = "Arial Black"
        return cls._archivo_loaded

    def _paint_text_overlay(self, p, w, h):
        """Draw 'RainDelay' title and countdown/clock centred on screen."""
        from datetime import datetime

        # Semi-transparent backdrop pill behind text
        pill_w, pill_h = 420, 140
        pill_x = (w - pill_w) // 2
        pill_y = (h - pill_h) // 2 - 20
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(0, 0, 0, 40))
        p.drawRoundedRect(QRectF(pill_x, pill_y, pill_w, pill_h), 20, 20)

        # Title: "RainDelay"
        title_font = QFont(self._archivo_family(), 32)
        p.setFont(title_font)
        p.setPen(QColor(255, 255, 255, 220))
        title_rect = QRectF(0, pill_y + 15, w, 50)
        p.drawText(title_rect, Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop,
                   "RainDelay")

        # Time line: countdown remaining or current clock
        time_font = QFont(self._archivo_family(), 26)
        p.setFont(time_font)

        countdown_ms = self._countdown_remaining_ms
        if countdown_ms > 0:
            # Show countdown remaining
            total_sec = max(0, countdown_ms // 1000)
            hrs = total_sec // 3600
            mins = (total_sec % 3600) // 60
            secs = total_sec % 60
            if hrs > 0:
                time_str = f"{hrs}:{mins:02d}:{secs:02d}"
            else:
                time_str = f"{mins:02d}:{secs:02d}"
        else:
            # Show current time in 12-hour AM/PM format
            now = datetime.now()
            time_str = now.strftime("%-I:%M:%S %p") if sys.platform != "win32" \
                else now.strftime("%#I:%M:%S %p")

        # Rainbow color that cycles smoothly over time (~10 second full cycle)
        import colorsys
        hue = (time.perf_counter() % 10.0) / 10.0  # 0.0–1.0 over 10 seconds
        r, g, b = colorsys.hsv_to_rgb(hue, 0.6, 1.0)  # saturation 0.6 keeps it pastel/readable
        p.setPen(QColor(int(r * 255), int(g * 255), int(b * 255), 220))

        time_rect = QRectF(0, pill_y + 75, w, 45)
        p.drawText(time_rect, Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop,
                   time_str)

    def _paint_exit_hint(self, p, w, h):
        """Draw temporary top-left hint for how to exit the overlay."""
        margin = 16
        pill_h = 36
        pill_w = 370
        pill_x = margin
        pill_y = margin
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(0, 0, 0, 75))
        p.drawRoundedRect(QRectF(pill_x, pill_y, pill_w, pill_h), 8, 8)
        hint_font = QFont(self._archivo_family(), 12)
        p.setFont(hint_font)
        p.setPen(QColor(255, 255, 255, 200))
        text_rect = QRectF(pill_x, pill_y, pill_w, pill_h)
        p.drawText(text_rect,
                   Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignVCenter,
                   "Press ESC or SPACEBAR to Exit")

    # ================================================================== #
    #  Exit hint + input handling
    # ================================================================== #

    def _arm_exit_hint(self):
        self._exit_hint_visible = True
        self._exit_hint_timer.start()
        self._request_repaint()

    def _hide_exit_hint(self):
        self._exit_hint_visible = False
        self._request_repaint()

    def _request_repaint(self):
        if self._graphics_view:
            self._graphics_view.viewport().update()
        else:
            self.update()

    def _handle_key(self, event):
        if event.isAutoRepeat():
            return
        key = event.key()
        if key in (Qt.Key.Key_Escape, Qt.Key.Key_Space):
            self.dismiss.emit()
            return
        if key == Qt.Key.Key_W:
            self.wiper_requested.emit()
        self._arm_exit_hint()

    def _handle_mouse_press(self):
        self._show_text = not self._show_text
        self._request_repaint()

    # ================================================================== #
    #  Event handling
    # ================================================================== #

    def keyPressEvent(self, event):
        self._handle_key(event)
        event.accept()

    def mousePressEvent(self, event):
        self._handle_mouse_press()
        event.accept()

    def mouseReleaseEvent(self, event):
        event.accept()

    def mouseMoveEvent(self, event):
        event.accept()

    def wheelEvent(self, event):
        event.accept()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self._graphics_view:
            self._graphics_view.setGeometry(0, 0, self.width(), self.height())
