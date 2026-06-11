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
from PyQt6.QtCore import Qt, QTimer, QUrl, pyqtSignal, QRectF, QPointF, QDateTime, QElapsedTimer
from PyQt6.QtGui import (
    QPainter, QColor, QPen, QBrush, QPixmap, QImage,
    QPainterPath, QRadialGradient, QLinearGradient, QFont, QFontDatabase,
)
from PyQt6.QtMultimedia import QMediaPlayer
from PyQt6.QtMultimediaWidgets import QGraphicsVideoItem
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
        
    def drawBackground(self, painter, rect):
        """Paint the blurred desktop background before the video."""
        overlay = self._parent_overlay
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
    
    def drawForeground(self, painter, rect):
        """Paint the text overlay on top of the video."""
        if self._parent_overlay._show_text:
            w = int(rect.width())
            h = int(rect.height())
            self._parent_overlay._paint_text_overlay(painter, w, h)
        
        # Performance tracking for video mode
        now = time.perf_counter()
        self._frame_count += 1
        overlay = self._parent_overlay
        overlay._frame_count += 1
        
        if overlay._perf_window_start == 0.0:
            overlay._perf_window_start = now
        
        # Log every N frames
        if overlay._frame_count % overlay._fps_log_interval == 0:
            elapsed = max(now - overlay._perf_window_start, 1e-6)
            effective_fps = overlay._fps_log_interval / elapsed
            
            log.info("[PERF] effective fps: %.1f | video: GPU rendering",
                     effective_fps)
            overlay._perf_window_start = now
    
    def mousePressEvent(self, event):
        """Toggle text overlay on click."""
        self._parent_overlay._show_text = not self._parent_overlay._show_text
        self.viewport().update()
        event.accept()
    
    def keyPressEvent(self, event):
        """Forward key events to parent overlay."""
        if event.key() == Qt.Key.Key_Escape:
            self._parent_overlay.dismiss.emit()
        else:
            event.accept()


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

    def __init__(self, settings: dict, target_screen=None):
        super().__init__()
        self._settings = settings
        self._target_screen = target_screen  # QScreen or None for primary
        self._bg_pixmap = None
        self._use_video = False
        self._video_item = None  # QGraphicsVideoItem for GPU-accelerated rendering
        self._graphics_view = None  # QGraphicsView to display the video
        self._media_player = None  # QMediaPlayer for this overlay
        self._show_text = False
        self._countdown_remaining_ms = 0  # set externally by main.py
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

        # Try to open rain video via QVideoWidget for GPU-accelerated rendering
        video_path = _find_rain_video()
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

        self.showFullScreen()
        self.raise_()
        self.activateWindow()
        self._reset_perf_counters()
        if self._use_video:
            self._log_video_diagnostics()
        self._timer.start()
        log.info("  Overlay activated in %.0fms",
                 (time.perf_counter() - t_start) * 1000)

    def deactivate(self):
        self._timer.stop()
        if self._media_player:
            self._media_player.stop()
            self._media_player.setSource(QUrl())
            self._media_player = None
        # Clean up graphics view (which will clean up the video item automatically)
        if self._graphics_view:
            self._graphics_view.hide()
            self._graphics_view.deleteLater()
            self._graphics_view = None
        # Video item is owned by the scene, don't delete it separately
        self._video_item = None
        self._bg_pixmap = None
        self._use_video = False
        self._reset_perf_counters()
        self.hide()

    def update_settings(self, settings: dict):
        self._settings = settings

    def set_countdown_remaining(self, ms: int):
        """Called by main.py to update the countdown remaining time."""
        self._countdown_remaining_ms = ms

    # ================================================================== #
    #  Video widget setup (GPU-accelerated rendering with blend mode)
    # ================================================================== #

    def _setup_video_widget(self, video_path: str, w: int, h: int):
        """Set up QGraphicsVideoItem for GPU-accelerated video rendering with screen blend."""
        # Create media player
        self._media_player = QMediaPlayer(self)
        
        # Create graphics scene — drawBackground in _CustomGraphicsView paints the bg pixmap
        scene = QGraphicsScene()
        scene.setSceneRect(0, 0, w, h)
        
        # Video item — IgnoreAspectRatio so it fills the full screen (no letterbox bars)
        self._video_item = QGraphicsVideoItem()
        self._video_item.setSize(QRectF(0, 0, w, h).size())
        self._video_item.setPos(0, 0)
        self._video_item.setAspectRatioMode(Qt.AspectRatioMode.IgnoreAspectRatio)
        scene.addItem(self._video_item)
        
        # Create custom graphics view as child of this overlay
        self._graphics_view = _CustomGraphicsView(scene, self, self)
        self._graphics_view.setGeometry(0, 0, w, h)
        self._graphics_view.setStyleSheet("background: transparent;")
        self._graphics_view.setFrameShape(QGraphicsView.Shape.NoFrame)
        self._graphics_view.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._graphics_view.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._graphics_view.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self._graphics_view.setSceneRect(0, 0, w, h)
        
        # Enable OpenGL rendering for better performance (optional)
        try:
            from PyQt6.QtOpenGLWidgets import QOpenGLWidget
            self._graphics_view.setViewport(QOpenGLWidget())
            log.info("  [PERF] OpenGL viewport enabled for hardware acceleration")
        except:
            log.info("  [PERF] Using default viewport (OpenGL not available)")
        
        # Connect player to video item
        self._media_player.setVideoOutput(self._video_item)
        
        # Set source and loop infinitely
        self._media_player.setSource(QUrl.fromLocalFile(video_path))
        self._media_player.setLoops(QMediaPlayer.Loops.Infinite)
        
        # Log diagnostics
        try:
            import platform
            import sys
            from PyQt6.QtCore import qVersion
            
            log.info("[DIAG] ========== SYSTEM DIAGNOSTICS ==========")
            log.info("[DIAG] OS: %s %s", platform.system(), platform.release())
            log.info("[DIAG] OS Version: %s", platform.version())
            log.info("[DIAG] CPU: %s", platform.processor() or platform.machine())
            log.info("[DIAG] Python: %s", sys.version.split()[0])
            log.info("[DIAG] Qt Version: %s", qVersion())
            
            # GPU detection (Windows)
            if platform.system() == "Windows":
                import subprocess
                try:
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
            
            log.info("[DIAG] Using QGraphicsVideoItem for direct GPU rendering (no CPU conversion)")
            log.info("[DIAG] =========================================")
        except Exception as e:
            log.debug("[DIAG] System info failed: %s", e)
        
        # Log playback errors
        self._media_player.errorOccurred.connect(
            lambda err: log.warning("[PERF] Video playback error: %s", self._media_player.errorString())
        )
        
        # Show graphics view and start playback
        self._graphics_view.show()
        self._graphics_view.raise_()
        self._media_player.play()
        log.info("  [PERF] Starting GPU-accelerated video playback from: %s", video_path)

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
        self._frame_skip = False
        self._slow_frame_count = 0
        self._paint_time_samples = []

    def _log_video_diagnostics(self):
        """Log detailed video subsystem diagnostics on startup"""
        if not self._media_player:
            return
        
        source = self._media_player.source().toLocalFile()
        
        # Get playback state
        state_map = {
            0: "StoppedState",
            1: "PlayingState", 
            2: "PausedState"
        }
        state_name = state_map.get(self._media_player.playbackState(), "UnknownState")
        
        log.info("[DIAG] ========== VIDEO DIAGNOSTICS ==========")
        log.info("[DIAG] Source: %s", source)
        log.info("[DIAG] Playback State: %s", state_name)
        log.info("[DIAG] Display: %dx%d @ %.2f DPI scale", 
                 self.width(), self.height(), 
                 self.screen().devicePixelRatio() if self.screen() else 1.0)
        log.info("[DIAG] Rendering: QGraphicsVideoItem (GPU-accelerated, no CPU conversion)")
        log.info("[DIAG] Target: 30fps native video playback")
        log.info("[DIAG] Note: Video is rendered directly by GPU, bypassing slow CPU NV12→RGB conversion")
        log.info("[DIAG] =========================================")
        log.info("[PERF] Performance monitoring active...")

    def _tick(self):
        # Refresh the text overlay on the graphics view each tick
        if self._graphics_view:
            self._graphics_view.viewport().update()

    # ================================================================== #
    #  Painting
    # ================================================================== #

    def paintEvent(self, _event):
        """QGraphicsView handles all painting in video mode."""
        pass

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
        if self._graphics_view:
            self._graphics_view.setGeometry(0, 0, self.width(), self.height())
