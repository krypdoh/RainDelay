"""
main.py
2026.06.28 1044
RainDelay entry point.

Start-up sequence:
  1. Load settings
  2. Create QApplication (single instance via lock file)
  3. Create Overlay, TrayIcon, SoundManager, Scheduler, HotkeyManager
  4. Wire all signals/slots
  5. Register global hotkey (ctypes RegisterHotKey — no admin required)
  6. Enter Qt event loop

Toggle sequence:
  hotkey / tray toggle → _toggle_overlay()
  → show:  overlay.activate()  + sound.play()  + tray.set_overlay_state(True)
           + scheduler.start_countdown() (if enabled)
  → hide:  overlay.deactivate() + sound.stop() + tray.set_overlay_state(False)
           + scheduler.stop_countdown()
"""

import sys
import os
import logging

# ── Logging setup ──────────────────────────────────────────────────────
# Writes to %APPDATA%/RainDelay/raindelay.log for performance diagnosis.
# Also prints to stderr if a console is attached.
_log_dir = os.path.join(os.environ.get("APPDATA", os.path.expanduser("~")), "RainDelay")
os.makedirs(_log_dir, exist_ok=True)
_log_file = os.path.join(_log_dir, "raindelay.log")
logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.FileHandler(_log_file, mode="w", encoding="utf-8"),
        logging.StreamHandler(sys.stderr),
    ],
)
_log = logging.getLogger("RainDelay")
_log.info("RainDelay starting — log file: %s", _log_file)
_log.info("Python %s | Platform: %s", sys.version, sys.platform)

# ── Qt Multimedia backend ──────────────────────────────────────────────
_d3d11_ok = True  # assume GPU is available until proven otherwise
if sys.platform == "win32":
    # Probe D3D11 with VIDEO_SUPPORT flag (0x800) — this is what FFmpeg uses.
    # If D3D11 is broken, FFmpeg crashes instead of falling back gracefully.
    # In that case, use Windows Media Foundation backend (no D3D11 dependency).
    try:
        import ctypes
        _d3d11 = ctypes.windll.d3d11
        _device = ctypes.c_void_p()
        _context = ctypes.c_void_p()
        _level = ctypes.c_int()
        _D3D11_CREATE_DEVICE_VIDEO_SUPPORT = 0x800
        _hr = _d3d11.D3D11CreateDevice(
            None,              # pAdapter (NULL = default)
            1,                 # D3D_DRIVER_TYPE_HARDWARE
            None,              # Software module
            _D3D11_CREATE_DEVICE_VIDEO_SUPPORT,
            None,              # pFeatureLevels
            0,                 # FeatureLevels count
            7,                 # SDK version (D3D11_SDK_VERSION)
            ctypes.byref(_device),
            ctypes.byref(_level),
            ctypes.byref(_context),
        )
        if _hr == 0:  # S_OK
            # Release immediately — we only needed to test availability
            _release_fn = ctypes.WINFUNCTYPE(ctypes.c_ulong, ctypes.c_void_p)
            if _context.value:
                _vtbl = ctypes.cast(
                    ctypes.c_void_p(ctypes.cast(_context, ctypes.POINTER(ctypes.c_void_p))[0]),
                    ctypes.POINTER(ctypes.c_void_p))
                _rel = _release_fn(_vtbl[2])
                _rel(_context)
            if _device.value:
                _vtbl = ctypes.cast(
                    ctypes.c_void_p(ctypes.cast(_device, ctypes.POINTER(ctypes.c_void_p))[0]),
                    ctypes.POINTER(ctypes.c_void_p))
                _rel = _release_fn(_vtbl[2])
                _rel(_device)
            _d3d11_ok = True
            _log.info("D3D11 probe: OK — using FFmpeg backend")
        else:
            _d3d11_ok = False
            _log.warning("D3D11 probe: FAILED (hr=0x%08X) — using WMF backend",
                         _hr & 0xFFFFFFFF)
    except Exception as e:
        _d3d11_ok = False
        _log.warning("D3D11 probe: exception (%s) — using WMF backend", e)

    # Choose backend based on D3D11 availability:
    # - FFmpeg: faster, but crashes if D3D11 device creation fails
    # - WMF (Windows Media Foundation): no D3D11 dependency, always works
    if _d3d11_ok:
        os.environ.setdefault("QT_MEDIA_BACKEND", "ffmpeg")
        _log.info("Qt Multimedia: FFmpeg backend (D3D11 available)")
    else:
        os.environ["QT_MEDIA_BACKEND"] = "windows"
        _log.info("Qt Multimedia: Windows Media Foundation backend (D3D11 unavailable)")

# Windows: hide the console window when launched via pythonw or double-click
if sys.platform == "win32":
    try:
        import ctypes
        ctypes.windll.user32.ShowWindow(  # type: ignore[attr-defined]
            ctypes.windll.kernel32.GetConsoleWindow(), 0  # type: ignore[attr-defined]
        )
    except Exception:
        pass

from PyQt6.QtWidgets import QApplication
from PyQt6.QtCore    import Qt, QTimer, QUrl
from PyQt6.QtMultimedia import QMediaPlayer, QVideoSink

import settings_manager as sm
from overlay        import RainOverlay, _find_rain_video
from tray_icon      import TrayIcon
from sound_manager  import SoundManager
from scheduler      import Scheduler
from hotkey_manager import HotkeyManager
from control_panel  import ControlPanel


def main() -> None:
    # ------------------------------------------------------------------ #
    #  Single-instance guard via lock file
    # ------------------------------------------------------------------ #
    lock_path = sm.APPDATA / "raindelay.lock"
    sm.APPDATA.mkdir(parents=True, exist_ok=True)
    try:
        lock_path.write_text(str(os.getpid()))
    except OSError:
        pass   # non-critical

    # ------------------------------------------------------------------ #
    #  Qt application
    # ------------------------------------------------------------------ #
    app = QApplication(sys.argv)
    app.setApplicationName("RainDelay")
    app.setQuitOnLastWindowClosed(False)   # keep alive in system tray

    # Log screen/display info for diagnosis
    for i, scr in enumerate(QApplication.screens()):
        geo = scr.geometry()
        _log.info("Screen %d: %s — %dx%d @ (%.1f, %.1f) DPR=%.2f",
                  i, scr.name(), geo.width(), geo.height(),
                  geo.x(), geo.y(), scr.devicePixelRatio())

    settings = sm.load()
    _log.info("Settings loaded: rain_speed=%s, rain_frequency=%s, screens=%s",
              settings.get("rain_speed"), settings.get("rain_frequency"),
              settings.get("screens"))

    # ------------------------------------------------------------------ #
    #  Construct components
    # ------------------------------------------------------------------ #
    overlays = []   # list of RainOverlay instances (one per screen)
    _shared_player = [None]  # single QMediaPlayer shared across all overlays
    _shared_sink = [None]    # QVideoSink that broadcasts frames
    tray     = TrayIcon()
    sound    = SoundManager(settings)
    sched    = Scheduler(settings)
    hotkey   = HotkeyManager(
        mods=settings.get("hotkey_mods", 0x0006),
        vk=settings.get("hotkey_vk", 0x52),
    )

    _active = [False]   # mutable flag: overlay currently shown

    def _get_target_screens():
        """Return list of QScreen objects based on settings."""
        all_screens = QApplication.screens()
        screen_setting = settings.get("screens", "all")
        if screen_setting == "all":
            return all_screens
        if isinstance(screen_setting, list):
            return [all_screens[i] for i in screen_setting
                    if 0 <= i < len(all_screens)]
        return [QApplication.primaryScreen()]

    def _create_overlays():
        """Create overlay windows for selected screens."""
        nonlocal overlays
        for ov in overlays:
            ov.deactivate()
            ov.deleteLater()
        overlays = []
        for scr in _get_target_screens():
            ov = RainOverlay(settings, target_screen=scr)
            ov.dismiss.connect(_hide_overlay)
            ov.wiper_requested.connect(_trigger_all_wipers)
            overlays.append(ov)

    # ------------------------------------------------------------------ #
    #  Core toggle logic
    # ------------------------------------------------------------------ #

    def _show_overlay() -> None:
        if _active[0]:
            return
        _log.info("Overlay SHOW requested")
        _active[0] = True
        _create_overlays()

        # Create ONE shared media player for all overlays (avoids multiple
        # D3D11 device creations which crash on degraded GPU state).
        # Even if D3D11 is broken, a single player gracefully falls back to
        # software decode — the crash only happened with 2+ players.
        screens = _get_target_screens()
        if screens:
            geom = screens[0].geometry()
            video_path = _find_rain_video(geom.width(), geom.height())
            if video_path:
                player = QMediaPlayer()
                sink = QVideoSink()
                player.setVideoOutput(sink)
                player.setSource(QUrl.fromLocalFile(video_path))
                player.setLoops(QMediaPlayer.Loops.Infinite)
                player.errorOccurred.connect(
                    lambda err, p=player: _log.warning(
                        "Shared player error: %s", p.errorString()))
                _shared_player[0] = player
                _shared_sink[0] = sink
                _log.info("Shared video player created: %s", video_path)

        for ov in overlays:
            ov.activate()

        # Connect shared sink frames + position to all overlays
        if _shared_sink[0]:
            for ov in overlays:
                _shared_sink[0].videoFrameChanged.connect(ov._on_video_frame)
        if _shared_player[0]:
            _shared_player[0].positionChanged.connect(_broadcast_position)
            _shared_player[0].durationChanged.connect(_broadcast_duration)
            _shared_player[0].play()

        sound.play()
        tray.set_overlay_state(True)
        if settings.get("countdown_enabled", False):
            sched.start_countdown()
        tray.show_notification("RainDelay", "Taking a break \u2614  Press ESC or SPACEBAR to dismiss.")
        _log.info("Overlay SHOW complete — %d screen(s)", len(overlays))

    def _broadcast_position(pos: int) -> None:
        """Forward shared player position to all overlays for wiper detection."""
        for ov in overlays:
            ov._on_position_changed(pos)

    def _broadcast_duration(dur: int) -> None:
        """Forward shared player duration to all overlays."""
        for ov in overlays:
            ov._video_duration = dur

    def _hide_overlay() -> None:
        if not _active[0]:
            return
        _log.info("Overlay HIDE requested")
        _active[0] = False
        for ov in overlays:
            ov.deactivate()
        # Stop and clean up shared player
        if _shared_player[0]:
            _shared_player[0].stop()
            _shared_player[0].setSource(QUrl())
            _shared_player[0].deleteLater()
            _shared_player[0] = None
        if _shared_sink[0]:
            _shared_sink[0].deleteLater()
            _shared_sink[0] = None
        sound.stop()
        sched.stop_countdown()
        tray.set_overlay_state(False)
        _log.info("Overlay HIDE complete")

    def _toggle_overlay() -> None:
        if _active[0]:
            _hide_overlay()
        else:
            _show_overlay()

    def _trigger_all_wipers() -> None:
        """Trigger wiper sweep on all active overlays and restart video."""
        for ov in overlays:
            ov.trigger_wiper()
        # Restart video from beginning on manual wiper trigger
        if _shared_player[0]:
            _shared_player[0].setPosition(0)

    # ------------------------------------------------------------------ #
    #  Helper functions (must be defined before signal wiring)
    # ------------------------------------------------------------------ #

    def _open_settings() -> None:
        panel = ControlPanel(settings)
        panel.settings_saved.connect(_apply_new_settings)
        panel.exec()

    def _apply_new_settings(new_settings: dict) -> None:
        settings.update(new_settings)
        for ov in overlays:
            ov.update_settings(settings)
        sound.update_settings(settings)
        sched.apply_settings(settings)
        # Re-register hotkey if it changed
        hotkey.update_hotkey(
            settings.get("hotkey_mods", 0x0006),
            settings.get("hotkey_vk", 0x52),
        )

    def _quit() -> None:
        _hide_overlay()
        # Force-destroy all overlay widgets so QMediaPlayer/D3D11 resources
        # are released before the process exits (prevents D3D11 device leak
        # that causes "Failed to create Direct3D device" on next launch)
        for ov in overlays:
            ov.deleteLater()
        overlays.clear()
        # Process pending deletions so Qt releases GPU resources now
        app.processEvents()
        sched.stop_all()
        hotkey.stop()
        try:
            lock_path.unlink(missing_ok=True)
        except Exception:
            pass
        app.quit()

    def _start_timed_break(minutes: int) -> None:
        """Start the overlay with a specific countdown duration."""
        if not _active[0]:
            _show_overlay()
        sched.start_countdown_minutes(minutes)

    # ------------------------------------------------------------------ #
    #  Signal wiring
    # ------------------------------------------------------------------ #

    hotkey.activated.connect(_toggle_overlay)

    tray.toggle_overlay.connect(_toggle_overlay)
    tray.start_timed.connect(_start_timed_break)
    tray.open_settings.connect(_open_settings)
    tray.quit_app.connect(_quit)

    sched.timeout.connect(_hide_overlay)          # countdown expired
    sched.should_start.connect(_show_overlay)     # daily schedule
    sched.should_stop.connect(_hide_overlay)

    # ------------------------------------------------------------------ #
    #  Start hotkey listener
    # ------------------------------------------------------------------ #
    ok = hotkey.start()
    if not ok:
        display = settings.get("hotkey_display", "Ctrl+Alt+R")
        tray.show_notification(
            "RainDelay – Hotkey Warning",
            f"Could not register {display}.  "
            "Another app may be using this combo. "
            "Change it in Settings.",
        )

    # ------------------------------------------------------------------ #
    #  1-second timer to sync countdown display on overlays
    # ------------------------------------------------------------------ #
    def _sync_countdown():
        remaining = sched.countdown_remaining_ms()
        for ov in overlays:
            ov.set_countdown_remaining(remaining)

    _countdown_sync_timer = QTimer()
    _countdown_sync_timer.setInterval(1000)
    _countdown_sync_timer.timeout.connect(_sync_countdown)
    _countdown_sync_timer.start()

    # ------------------------------------------------------------------ #
    #  Enter event loop
    # ------------------------------------------------------------------ #
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
