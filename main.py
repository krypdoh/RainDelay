"""
main.py
2026.06.11.0104
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

# ── Enable FFmpeg hardware video decoding ──────────────────────────────
# Force Qt Multimedia FFmpeg backend to use GPU hardware acceleration
# Available methods: d3d11va, dxva2, cuda, qsv (Intel Quick Sync)
if sys.platform == "win32":
    # Force NVIDIA GPU for dual-GPU systems (NVIDIA + Intel)
    # This tells Windows to prefer discrete GPU over integrated GPU
    os.environ.setdefault("CUDA_VISIBLE_DEVICES", "0")
    # Force Direct3D 11 Video Acceleration (works with NVIDIA and AMD)
    os.environ.setdefault("QT_MEDIA_BACKEND", "ffmpeg")
    # Use d3d11va for better NVIDIA GPU utilization
    os.environ.setdefault("OPENCV_FFMPEG_CAPTURE_OPTIONS", "hwaccel;d3d11va")
    _log.info("Hardware video decoding enabled (using d3d11va for NVIDIA GPU)")

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
from PyQt6.QtCore    import Qt, QTimer

import settings_manager as sm
from overlay        import RainOverlay
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
        for ov in overlays:
            ov.activate()
        sound.play()
        tray.set_overlay_state(True)
        if settings.get("countdown_enabled", False):
            sched.start_countdown()
        tray.show_notification("RainDelay", "Taking a break \u2614  Press ESC to dismiss.")
        _log.info("Overlay SHOW complete — %d screen(s)", len(overlays))

    def _hide_overlay() -> None:
        if not _active[0]:
            return
        _log.info("Overlay HIDE requested")
        _active[0] = False
        for ov in overlays:
            ov.deactivate()
        sound.stop()
        sched.stop_countdown()
        tray.set_overlay_state(False)
        _log.info("Overlay HIDE complete")

    def _toggle_overlay() -> None:
        if _active[0]:
            _hide_overlay()
        else:
            _show_overlay()

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
