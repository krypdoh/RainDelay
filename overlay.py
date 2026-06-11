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
from weakref import WeakSet

from PyQt6.QtWidgets import QWidget, QApplication
from PyQt6.QtCore import Qt, QTimer, QUrl, pyqtSignal, QRectF, QPointF, QDateTime, QElapsedTimer
from PyQt6.QtGui import (
    QPainter, QColor, QPen, QBrush, QPixmap, QImage,
    QPainterPath, QRadialGradient, QLinearGradient, QFont, QFontDatabase,
)
from PyQt6.QtMultimedia import QMediaPlayer, QVideoSink, QVideoFrame

log = logging.getLogger("RainDelay.overlay")

from rain_engine import RainEngine, DropState

# PyInstaller onefile support: _MEIPASS is the temp folder for bundled data
import sys
_HERE = Path(getattr(sys, '_MEIPASS', Path(__file__).resolve().parent))
_ASSETS = _HERE / "assets"

# Supported video extensions
_VIDEO_EXTS = (".mp4", ".avi", ".mov", ".mkv", ".webm")


def _find_rain_video():
    """Look for a rain overlay video in assets/."""
    # Try native resolution version first (no scaling = best performance)
    for ext in _VIDEO_EXTS:
        p = _ASSETS / f"rain_native{ext}"
        if p.exists():
            return str(p)
    # Fall back to standard rain video
    for ext in _VIDEO_EXTS:
        p = _ASSETS / f"rain{ext}"
        if p.exists():
            return str(p)
    # Lower-res version as fallback
    for ext in _VIDEO_EXTS:
        p = _ASSETS / f"rain_lowres{ext}"
        if p.exists():
            return str(p)
    # Also check for any video file in assets/
    if _ASSETS.exists():
        for f in _ASSETS.iterdir():
            if f.suffix.lower() in _VIDEO_EXTS:
                return str(f)
    return None


class RainOverlay(QWidget):
    """Full-screen overlay: blurred desktop + rain video (or rendered fallback)."""

    dismiss = pyqtSignal()
    _video_subscribers = WeakSet()
    _shared_player = None
    _shared_sink = None
    _shared_image = None

    def __init__(self, settings: dict, target_screen=None):
        super().__init__()
        self._settings = settings
        self._target_screen = target_screen  # QScreen or None for primary
        self._engine = None
        self._bg_pixmap = None
        self._current_frame = None
        self._use_video = False
        self._using_shared_video = False
        self._show_text = False
        self._countdown_remaining_ms = 0  # set externally by main.py
        self._timer = QTimer(self)
        self._timer.setInterval(33)  # ~30fps for rendered fallback
        self._timer.timeout.connect(self._tick)
        self._video_repaint_timer = QTimer(self)
        self._video_repaint_timer.setInterval(33)  # ~30fps to match video playback (was 250ms)
        self._video_repaint_timer.timeout.connect(self._tick)
        # Performance tracking
        self._frame_count = 0
        self._perf_accum_ms = 0.0
        self._perf_window_start = 0.0
        self._last_tick_time = 0.0
        self._last_paint_time = 0.0
        self._last_video_frame_time = 0.0
        self._last_frame_delivery_time = None  # for video frame gap detection (None = first frame)
        self._fps_log_interval = 30  # log every N frames (~1 sec at 30fps)
        self._frame_skip = False  # set True if previous frame was slow
        self._slow_frame_count = 0  # track slow frames for diagnostics
        self._slow_paint_threshold = 33.0  # log paint times exceeding this (ms)
        self._cached_video_frame = None  # cache last converted frame to avoid redundant conversions
        self._video_scale_warning_logged = False  # warn once about video upscaling performance
        # Video decode diagnostics
        self._frame_gap_samples = []  # track frame gaps for statistics
        self._paint_time_samples = []  # track paint times for statistics
        self._last_video_playback_state = None  # detect playback state changes
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

        # Capture + blur desktop
        # QScreen.grabWindow coordinates are screen-local for the selected
        # screen. Using global geometry offsets can produce blank captures on
        # secondary displays.
        screenshot = screen.grabWindow(0, 0, 0, geom.width(), geom.height())
        self._bg_pixmap = self._blur_pixmap(screenshot)
        log.debug("  Background capture+blur: %.0fms",
                  (time.perf_counter() - t_start) * 1000)

        w, h = geom.width(), geom.height()

        # Try to open rain video via QMediaPlayer + QVideoSink
        video_path = _find_rain_video()
        if video_path:
            log.info("  Using video rain: %s", video_path)
            try:
                self._attach_shared_video(video_path)
                self._use_video = True
                log.info("  [PERF] Video mode enabled - rendering at ~30fps")
            except Exception as e:
                log.warning("  Video init failed: %s -- falling back to rendered", e)
                self._use_video = False
        else:
            log.info("  No video file found, using rendered rain engine")

        # Fallback: rendered rain engine
        if not self._use_video:
            self._engine = RainEngine(w, h, self._settings)
            log.info("  [PERF] Rendered mode enabled - rendering at ~30fps with %d initial drops", len(self._engine.drops))

        self.showFullScreen()
        self.raise_()
        self.activateWindow()
        self._reset_perf_counters()
        if self._use_video:
            self._log_video_diagnostics()
            self._video_repaint_timer.start()
        else:
            self._timer.start()
        log.info("  Overlay activated in %.0fms",
                 (time.perf_counter() - t_start) * 1000)

    def deactivate(self):
        self._timer.stop()
        self._video_repaint_timer.stop()
        if self._engine:
            self._engine.clear()
            self._engine = None
        if self._using_shared_video:
            self._detach_shared_video()
        self._current_frame = None
        self._bg_pixmap = None
        self._use_video = False
        self._using_shared_video = False
        self._reset_perf_counters()
        self.hide()

    def update_settings(self, settings: dict):
        self._settings = settings
        if self._engine:
            self._engine.apply_settings(settings)

    def set_countdown_remaining(self, ms: int):
        """Called by main.py to update the countdown remaining time."""
        self._countdown_remaining_ms = ms

    # ================================================================== #
    #  Video frame handling (via QVideoSink)
    # ================================================================== #

    @classmethod
    def _ensure_shared_video_backend(cls):
        if cls._shared_sink is None:
            cls._shared_sink = QVideoSink()
            cls._shared_sink.videoFrameChanged.connect(cls._on_shared_video_frame)
        if cls._shared_player is None:
            cls._shared_player = QMediaPlayer()
            cls._shared_player.setVideoOutput(cls._shared_sink)
            cls._shared_player.setLoops(QMediaPlayer.Loops.Infinite)
            
            # Log comprehensive system information for diagnostics
            try:
                import platform
                import sys
                from PyQt6.QtCore import QSysInfo, qVersion
                
                log.info("[DIAG] ========== SYSTEM DIAGNOSTICS ==========")
                log.info("[DIAG] OS: %s %s", platform.system(), platform.release())
                log.info("[DIAG] OS Version: %s", platform.version())
                log.info("[DIAG] CPU: %s", platform.processor() or platform.machine())
                log.info("[DIAG] Python: %s", sys.version.split()[0])
                log.info("[DIAG] Qt Version: %s", qVersion())
                try:
                    from PyQt6.QtMultimedia import QMediaFormat
                    log.info("[DIAG] PyQt6.QtMultimedia available")
                except:
                    pass
                
                # GPU detection (Windows)
                if platform.system() == "Windows":
                    import subprocess
                    try:
                        result = subprocess.run(
                            ["wmic", "path", "win32_VideoController", "get", "Name,DriverVersion,AdapterRAM", "/format:list"],
                            capture_output=True, text=True, timeout=2, creationflags=subprocess.CREATE_NO_WINDOW
                        )
                        if result.returncode == 0:
                            for line in result.stdout.split('\n'):
                                line = line.strip()
                                if line and '=' in line:
                                    log.info("[DIAG] GPU: %s", line)
                    except:
                        try:
                            # Fallback: simpler GPU query
                            result = subprocess.run(
                                ["powershell", "-Command", "Get-WmiObject Win32_VideoController | Select Name | Format-List"],
                                capture_output=True, text=True, timeout=2, creationflags=subprocess.CREATE_NO_WINDOW
                            )
                            if result.returncode == 0:
                                for line in result.stdout.split('\n'):
                                    line = line.strip()
                                    if line and ':' in line:
                                        log.info("[DIAG] GPU: %s", line)
                        except:
                            log.info("[DIAG] GPU: Detection failed")
                
                log.info("[DIAG] FFmpeg will auto-select hardware decoder if GPU supports H.264")
                log.info("[DIAG] =========================================")
            except Exception as e:
                log.debug("[DIAG] System info failed: %s", e)
            
            # Log any playback errors
            cls._shared_player.errorOccurred.connect(
                lambda err: log.warning("[PERF] Video playback error: %s", cls._shared_player.errorString())
            )

    def _attach_shared_video(self, video_path: str):
        cls = type(self)
        cls._ensure_shared_video_backend()
        cls._video_subscribers.add(self)
        self._using_shared_video = True
        if cls._shared_player.source().toLocalFile() != video_path:
            cls._shared_player.setSource(QUrl.fromLocalFile(video_path))
            cls._shared_player.play()
            log.info("  [PERF] Starting video playback from: %s", video_path)
        elif cls._shared_player.playbackState() != QMediaPlayer.PlaybackState.PlayingState:
            cls._shared_player.play()
            log.info("  [PERF] Resuming video playback")

    def _detach_shared_video(self):
        cls = type(self)
        if self in cls._video_subscribers:
            cls._video_subscribers.discard(self)
        if not cls._video_subscribers and cls._shared_player:
            cls._shared_player.stop()
            cls._shared_player.setSource(QUrl())
            cls._shared_image = None

    @classmethod
    def _on_shared_video_frame(cls, frame: QVideoFrame):
        if not frame.isValid():
            return
        
        # Log first frame details including format (hardware vs software decoding indicator)
        if cls._shared_image is None:
            pixel_format = frame.pixelFormat()
            log.info("  [PERF] First video frame: %dx%d", frame.width(), frame.height())
            log.info("  [PERF] Video pixel format: %s", 
                     pixel_format.name if hasattr(pixel_format, 'name') else str(pixel_format))
            log.info("  [PERF] Hardware decode: Check if format is NV12/P010 (GPU) vs RGB/YUV420P (CPU software)")
        
        # Convert to QImage - this copies from GPU to CPU memory (unavoidable in Qt)
        # Use RGB888 format for faster conversion (no alpha channel needed)
        img = frame.toImage()
        if img.isNull():
            return
        
        # Convert to RGB888 if not already (faster for our use case)
        if img.format() != QImage.Format.Format_RGB888:
            img = img.convertToFormat(QImage.Format.Format_RGB888)
        
        cls._shared_image = img
        timestamp = time.perf_counter()
        for overlay in list(cls._video_subscribers):
            overlay._on_shared_video_image(img, timestamp)

    def _on_shared_video_image(self, img: QImage, timestamp: float):
        w, h = self.width(), self.height()
        if w <= 0 or h <= 0:
            return
        
        t0 = time.perf_counter()
        orig_w, orig_h = img.width(), img.height()
        
        # Check if low-resolution mode is enabled (better performance on weak GPUs)
        lowres_mode = self._settings.get("lowres_mode", False)
        
        # Determine target resolution
        if lowres_mode and (w > 1920 or h > 1080):
            # Scale to 1080p max, maintaining aspect ratio of target screen
            aspect = w / h
            if aspect > (1920 / 1080):  # Wide screen
                target_w, target_h = 1920, int(1920 / aspect)
            else:  # Tall or square screen
                target_w, target_h = int(1080 * aspect), 1080
        else:
            # Full resolution mode
            target_w, target_h = w, h
        
        # Scale and convert to pixmap in one operation for efficiency
        if orig_w != target_w or orig_h != target_h:
            # Create scaled pixmap directly (more efficient than scale-then-convert)
            self._current_frame = QPixmap.fromImage(
                img.scaled(target_w, target_h, Qt.AspectRatioMode.IgnoreAspectRatio,
                          Qt.TransformationMode.FastTransformation)
            )
            scaled = True
        else:
            self._current_frame = QPixmap.fromImage(img)
            scaled = False
        
        self._last_video_frame_time = timestamp
        frame_prep_ms = (time.perf_counter() - t0) * 1000
        
        # Only log if DEBUG level and frame is slow
        if log.isEnabledFor(logging.DEBUG) and frame_prep_ms > 5.0:
            if scaled:
                mode_str = " [LOWRES]" if lowres_mode else ""
                log.debug("[PERF] Frame prep: %.1fms (scale %dx%d->%dx%d)%s",
                         frame_prep_ms, orig_w, orig_h, target_w, target_h, mode_str)
            else:
                log.debug("[PERF] Frame prep: %.1fms (no scale)", frame_prep_ms)
        # Track frame gaps for statistics (only log severe issues)
        if self._last_frame_delivery_time is not None:
            gap = (timestamp - self._last_frame_delivery_time) * 1000
            self._frame_gap_samples.append(gap)
            # Only log severe decode lag (>100ms) to reduce overhead
            if log.isEnabledFor(logging.DEBUG) and gap > 100:
                log.debug("[PERF] Frame gap: %.0fms (decode lag)", gap)
        self._last_frame_delivery_time = timestamp
        
        # Use QWidget.update() instead of repaint() to batch paint events
        self.update()

    def _on_video_frame(self, frame: QVideoFrame):
        """Called by QVideoSink whenever a new frame is decoded."""
        if not frame.isValid():
            return
        # Convert QVideoFrame to QImage then to QPixmap scaled to widget size
        img = frame.toImage()
        if img.isNull():
            return
        w, h = self.width(), self.height()
        if img.width() != w or img.height() != h:
            # Use FastTransformation — SmoothTransformation is too expensive
            # at 30fps on full-screen resolution (was causing unresponsiveness)
            img = img.scaled(w, h, Qt.AspectRatioMode.IgnoreAspectRatio,
                             Qt.TransformationMode.FastTransformation)
        self._current_frame = QPixmap.fromImage(img)
        self._last_video_frame_time = time.perf_counter()
        self.update()

    # ================================================================== #
    #  Blur
    # ================================================================== #

    def _blur_pixmap(self, px):
        """Fast blur via downscale/upscale. Transparency controls blur amount."""
        transparency = self._settings.get("transparency", 70)
        scale = max(3, min(14, int(transparency / 8) + 3))

        w, h = px.width(), px.height()
        small = px.scaled(w // scale, h // scale,
                          Qt.AspectRatioMode.IgnoreAspectRatio,
                          Qt.TransformationMode.SmoothTransformation)
        blurred = small.scaled(w, h,
                               Qt.AspectRatioMode.IgnoreAspectRatio,
                               Qt.TransformationMode.SmoothTransformation)
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
        self._last_video_frame_time = 0.0
        self._last_frame_delivery_time = None  # First frame will set this
        self._frame_skip = False
        self._slow_frame_count = 0
        self._frame_gap_samples = []
        self._paint_time_samples = []

    def _log_video_diagnostics(self):
        """Log detailed video subsystem diagnostics on startup"""
        cls = type(self)
        if not cls._shared_player:
            return
        
        player = cls._shared_player
        source = player.source().toLocalFile()
        
        # Get current frame info
        frame_info = ""
        if cls._shared_image and not cls._shared_image.isNull():
            img = cls._shared_image
            frame_info = f" | first frame: {img.width()}x{img.height()}"
        
        # Get playback state
        state_map = {
            0: "StoppedState",
            1: "PlayingState", 
            2: "PausedState"
        }
        state_name = state_map.get(player.playbackState(), "UnknownState")
        
        log.info("[DIAG] ========== VIDEO DIAGNOSTICS ==========")
        log.info("[DIAG] Source: %s", source)
        log.info("[DIAG] Playback State: %s%s", state_name, frame_info)
        log.info("[DIAG] Display: %dx%d @ %.2f DPI scale", 
                 self.width(), self.height(), 
                 self.screen().devicePixelRatio() if self.screen() else 1.0)
        lowres_mode = self._settings.get("lowres_mode", False)
        if lowres_mode:
            log.info("[DIAG] Mode: LOW-RESOLUTION (rendering at 1080p, upscaling to screen)")
        else:
            log.info("[DIAG] Mode: FULL-RESOLUTION (native rendering)")
        log.info("[DIAG] Target: 30fps = 33.33ms/frame budget")
        log.info("[DIAG] =========================================")
        log.info("[PERF] Starting performance monitoring...")

    def _tick(self):
        now = time.perf_counter()
        if self._use_video:
            self._frame_skip = False
            if self._show_text or self._countdown_remaining_ms > 0:
                self.update()
            else:
                # In video mode, always call update() to keep painting at 30fps
                self.update()
            return
        # If the previous frame took too long, skip engine update to let
        # the event loop breathe (prevents "hard to exit" issue)
        if self._last_tick_time > 0:
            elapsed_since_last = (now - self._last_tick_time) * 1000
            if elapsed_since_last > 80:  # >80ms means we're behind
                log.debug("[PERF] Frame behind: %.1fms since last tick, skipping engine update", elapsed_since_last)
                self._frame_skip = True
                self._slow_frame_count += 1
                # Process pending events so ESC/quit still work
                QApplication.processEvents()
                self._last_tick_time = now
                self.update()
                return
        self._last_tick_time = now
        self._frame_skip = False
        if self._engine:
            self._engine.update()
        self.update()

    # ================================================================== #
    #  Painting
    # ================================================================== #

    def paintEvent(self, _event):
        t0 = time.perf_counter()
        p = QPainter(self)
        # Disable antialiasing for video mode (not needed, saves CPU)
        if not self._use_video:
            p.setRenderHint(QPainter.RenderHint.Antialiasing, not self._frame_skip)

        w, h = self.width(), self.height()

        # Draw blurred desktop
        if self._bg_pixmap:
            p.drawPixmap(0, 0, self._bg_pixmap)
        else:
            p.fillRect(0, 0, w, h, QColor(20, 20, 20, 200))

        # Darken the background (user-controlled)
        darkness = self._settings.get("darkness", 0)
        if darkness > 0:
            dark_alpha = int(darkness * 2.55)  # 0-80% -> 0-204 alpha
            p.fillRect(0, 0, w, h, QColor(0, 0, 0, dark_alpha))

        # Composite rain layer
        if self._use_video and self._current_frame:
            # Screen blend: black becomes invisible, bright water pixels show
            # Use SourceOver with opacity for better performance on some GPUs
            p.setCompositionMode(QPainter.CompositionMode.CompositionMode_Screen)
            # Draw cached, pre-scaled pixmap (no per-frame conversion/scaling)
            # If lowres_mode is enabled, Qt will upscale from 1080p to full screen here
            p.drawPixmap(0, 0, w, h, self._current_frame)
            p.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceOver)
        elif self._engine:
            # Fallback: rendered drops
            for drop in self._engine.drops:
                if drop.state == DropState.FALLING:
                    self._draw_falling(p, drop)
                elif drop.state == DropState.BEAD:
                    self._draw_bead(p, drop)
                elif drop.state == DropState.STREAK:
                    self._draw_streak(p, drop)

        # ── Centred text overlay (shown on click) ───────────────────────
        if self._show_text:
            self._paint_text_overlay(p, w, h)

        p.end()

        # Performance tracking
        frame_ms = (time.perf_counter() - t0) * 1000
        now = time.perf_counter()
        self._frame_count += 1
        self._perf_accum_ms += frame_ms
        if self._perf_window_start == 0.0:
            self._perf_window_start = now
        fps = 0.0
        if self._last_paint_time > 0:
            paint_interval = now - self._last_paint_time
            if paint_interval > 0:
                fps = 1.0 / paint_interval
        self._last_paint_time = now
        
        # Log individual slow frames immediately for diagnosis
        if frame_ms > self._slow_paint_threshold:
            log.debug("[PERF] Slow frame: %.1fms paint time | fps: %.1f | drops: %d | video: %s",
                     frame_ms, fps if fps > 0 else 0,
                     len(self._engine.drops) if self._engine else 0,
                     "yes" if self._use_video else "no")
            self._paint_time_samples.append(frame_ms)
        
        if self._frame_count % self._fps_log_interval == 0:
            avg_ms = self._perf_accum_ms / self._fps_log_interval
            elapsed = max(now - self._perf_window_start, 1e-6)
            effective_fps = self._fps_log_interval / elapsed
            drop_count = len(self._engine.drops) if self._engine else 0
            
            # Calculate comprehensive frame timing statistics
            gap_stats = ""
            if self._frame_gap_samples:
                avg_gap = sum(self._frame_gap_samples) / len(self._frame_gap_samples)
                min_gap = min(self._frame_gap_samples)
                max_gap = max(self._frame_gap_samples)
                # Calculate percentiles for better insight
                sorted_gaps = sorted(self._frame_gap_samples)
                p50_gap = sorted_gaps[len(sorted_gaps)//2]
                p95_gap = sorted_gaps[int(len(sorted_gaps)*0.95)] if len(sorted_gaps) > 1 else sorted_gaps[0]
                gap_stats = f" | gaps: min={min_gap:.0f} p50={p50_gap:.0f} avg={avg_gap:.0f} p95={p95_gap:.0f} max={max_gap:.0f}ms"
            
            # Calculate paint time statistics
            paint_stats = ""
            if self._paint_time_samples:
                avg_paint = sum(self._paint_time_samples) / len(self._paint_time_samples)
                min_paint = min(self._paint_time_samples)
                max_paint = max(self._paint_time_samples)
                sorted_paints = sorted(self._paint_time_samples)
                p95_paint = sorted_paints[int(len(sorted_paints)*0.95)] if len(sorted_paints) > 1 else sorted_paints[0]
                paint_stats = f" | paint: min={min_paint:.0f} avg={avg_paint:.0f} p95={p95_paint:.0f} max={max_paint:.0f}ms"
            
            # Detailed bottleneck analysis with frame counts
            analysis = ""
            if self._frame_gap_samples and self._paint_time_samples:
                avg_gap = sum(self._frame_gap_samples) / len(self._frame_gap_samples)
                avg_paint = sum(self._paint_time_samples) / len(self._paint_time_samples)
                target_gap = 33.33  # 30fps
                
                # Count severe issues
                severe_gaps = sum(1 for g in self._frame_gap_samples if g > 50)
                severe_paints = len(self._paint_time_samples)  # Already filtered >33ms
                
                if avg_gap > 40:
                    analysis = f" [DECODE LAG: {avg_gap:.0f}ms avg, {severe_gaps}/{len(self._frame_gap_samples)} frames >50ms]"
                elif avg_paint > 35:
                    analysis = f" [PAINT SLOW: {avg_paint:.0f}ms avg, {severe_paints} slow frames]"
                else:
                    analysis = f" [HEALTHY: decode={avg_gap:.0f}ms paint={avg_paint:.0f}ms]"
            elif self._frame_gap_samples:
                avg_gap = sum(self._frame_gap_samples) / len(self._frame_gap_samples)
                severe_gaps = sum(1 for g in self._frame_gap_samples if g > 50)
                if avg_gap > 40:
                    analysis = f" [DECODE LAG: {avg_gap:.0f}ms avg, {severe_gaps}/{len(self._frame_gap_samples)} frames >50ms]"
            
            log.info("[PERF] avg paint: %.1fms | effective fps: %.1f | drops: %d | video: %s | slow_frames: %d%s%s%s",
                     avg_ms, effective_fps, drop_count,
                     "yes" if self._use_video else "no", self._slow_frame_count,
                     gap_stats, paint_stats, analysis)
            self._perf_accum_ms = 0.0
            self._perf_window_start = now
            self._slow_frame_count = 0
            self._frame_gap_samples = []
            self._paint_time_samples = []
            if avg_ms > 50:
                log.warning("[PERF] Paint time %.1fms exceeds 50ms — UI may be unresponsive!", avg_ms)
            # Warn if persistent upscaling detected (paint time consistently high with video)
            if self._use_video and avg_ms > 40 and not self._video_scale_warning_logged:
                log.warning("[PERF] Video frame upscaling is consuming most frame budget (%.1fms/frame). "
                           "Consider using higher-resolution video (2560x1600+) or rendered mode.", avg_ms)
                self._video_scale_warning_logged = True

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
        p.setPen(QColor(200, 220, 255, 200))

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
            # Show current time with seconds
            now = datetime.now()
            time_str = now.strftime("%H:%M:%S")

        time_rect = QRectF(0, pill_y + 75, w, 45)
        p.drawText(time_rect, Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop,
                   time_str)

    # ================================================================== #
    #  Fallback rendered drops (used only when no video available)
    # ================================================================== #

    @staticmethod
    def _draw_water_drop(p, cx, cy, rx, ry, opacity=1.0):
        if opacity <= 0.01 or rx < 0.4 or ry < 0.4:
            return

        def a(base):
            return max(0, min(255, int(base * opacity)))

        center = QPointF(cx, cy)

        if rx < 3.0:
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(30, 40, 50, a(100)))
            p.drawEllipse(center, rx, ry)
            if rx > 1.5:
                p.setBrush(QColor(255, 255, 255, a(80)))
                p.drawEllipse(QPointF(cx - rx * 0.2, cy - ry * 0.25),
                              rx * 0.3, ry * 0.25)
            return

        rim_grad = QRadialGradient(center, max(rx, ry) * 1.05)
        rim_grad.setColorAt(0.00, QColor(255, 255, 255, a(8)))
        rim_grad.setColorAt(0.55, QColor(200, 210, 220, a(15)))
        rim_grad.setColorAt(0.78, QColor(80, 95, 110, a(65)))
        rim_grad.setColorAt(0.92, QColor(35, 45, 55, a(130)))
        rim_grad.setColorAt(1.00, QColor(20, 30, 40, a(155)))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QBrush(rim_grad))
        p.drawEllipse(center, rx, ry)

        hl_cx = cx - rx * 0.28
        hl_cy = cy - ry * 0.32
        hl_rx = rx * 0.28
        hl_ry = ry * 0.20
        hl_grad = QRadialGradient(QPointF(hl_cx, hl_cy), max(hl_rx, hl_ry) * 1.1)
        hl_grad.setColorAt(0.0, QColor(255, 255, 255, a(200)))
        hl_grad.setColorAt(0.5, QColor(255, 255, 255, a(90)))
        hl_grad.setColorAt(1.0, QColor(255, 255, 255, a(0)))
        p.setBrush(QBrush(hl_grad))
        p.drawEllipse(QPointF(hl_cx, hl_cy), hl_rx, hl_ry)

    @staticmethod
    def _draw_falling(p, drop):
        col = QColor(180, 200, 215, 55)
        p.save()
        p.translate(drop.x, drop.y)
        p.rotate(drop.FALL_ANGLE_DEG)
        w = max(0.5, drop.size * 0.6)
        h = drop.size * 6.0
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(col)
        p.drawEllipse(QRectF(-w / 2, -h / 2, w, h))
        p.restore()

    def _draw_bead(self, p, drop):
        opacity = drop.bead_opacity
        if opacity <= 0:
            return
        rx = drop.size * 4.2
        ry = rx * 0.88
        self._draw_water_drop(p, drop.streak_x, drop.streak_y, rx, ry, opacity)

    def _draw_streak(self, p, drop):
        opacity = drop.streak_opacity_val
        if opacity <= 0:
            return
        cx = drop.streak_x
        blob_rx = drop.size * 3.8
        blob_ry = drop.size * 4.8

        trail_top = drop.impact_y
        trail_bottom = drop.streak_y - blob_ry * 0.7

        if trail_bottom > trail_top + 2:
            tw = max(0.6, drop.size * 1.0)
            trail_col = QColor(40, 55, 70, int(75 * opacity))
            pen = QPen(trail_col, tw)
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            p.setPen(pen)
            p.drawLine(QPointF(cx, trail_top), QPointF(cx, trail_bottom))
            p.setPen(Qt.PenStyle.NoPen)

        self._draw_water_drop(p, cx, drop.streak_y, blob_rx, blob_ry, opacity)

    # ================================================================== #
    #  Event handling
    # ================================================================== #

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Escape:
            self.dismiss.emit()
        else:
            event.accept()

    def mousePressEvent(self, event):
        # Toggle the time/title overlay on click
        self._show_text = not self._show_text
        event.accept()

    def mouseReleaseEvent(self, event):
        event.accept()

    def mouseMoveEvent(self, event):
        event.accept()

    def wheelEvent(self, event):
        event.accept()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self._engine:
            self._engine.resize(self.width(), self.height())
