"""
main.py
1.1
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

  overlay_mode == "active" → _show_active()/_hide_active() instead: click-through
  ActiveRainOverlay per screen, no capture/blur. Timers/schedule/breaks always
  use screensaver mode and resume active rain afterwards.

  Rain Break: sched.break_due every break_minutes → _start_rain_break():
  lockout → screensaver overlay with focus guard; otherwise active overlay.
  Gradual → _ramp_tick() scales rain opacity + volume up to full, then
  lightning goes into storm mode. Timer restarts when the break ends.
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
from PyQt6.QtCore    import Qt, QTimer, QUrl, QElapsedTimer
from PyQt6.QtMultimedia import QMediaPlayer, QVideoSink

import settings_manager as sm
from overlay        import RainOverlay, _find_rain_video, force_foreground
from active_overlay import ActiveRainOverlay, ActiveFrameProcessor, FullscreenWatcher
from lightning      import Lightning
from tray_icon      import TrayIcon
from sound_manager  import SoundManager
from scheduler      import Scheduler
from hotkey_manager import HotkeyManager
from control_panel  import ControlPanel

# Rain videos fade in from black over ~1.9s; loop past it.
LOOP_START_MS = 2000
# Seek back just before the end so the player never hits EndOfMedia.
LOOP_END_GUARD_MS = 150


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
    _active_rain = [False]          # active (click-through) mode running
    _resume_active_after = [False]  # screensaver interrupted active mode
    active_overlays: list = []      # ActiveRainOverlay instances
    _processor = [None]             # ActiveFrameProcessor
    watcher = FullscreenWatcher()
    lightning = Lightning()

    # Rain Break state
    _break = {"on": False, "lockout": False, "ramp": False}
    _resume_ambient_after_break = [False]
    _ramp_clock = QElapsedTimer()
    _ramp_timer = QTimer()
    _ramp_timer.setInterval(500)
    _lock_timer = QTimer()
    _lock_timer.setInterval(400)

    def _persist(**updates) -> None:
        """Update in-memory settings and merge into the on-disk file."""
        settings.update(updates)
        disk = sm.load()
        disk.update(updates)
        sm.save(disk)

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
    #  Shared video player
    # ------------------------------------------------------------------ #

    def _start_shared_player(screens) -> bool:
        """Create the single shared player + sink (not yet playing)."""
        # One player only: multiple D3D11 devices crash on degraded GPU state.
        if not screens:
            return False
        geom = screens[0].geometry()
        video_path = _find_rain_video(geom.width(), geom.height())
        if not video_path:
            return False
        player = QMediaPlayer()
        sink = QVideoSink()
        player.setVideoOutput(sink)
        player.setSource(QUrl.fromLocalFile(video_path))
        player.setLoops(QMediaPlayer.Loops.Infinite)
        player.errorOccurred.connect(
            lambda err, p=player: _log.warning(
                "Shared player error: %s", p.errorString()))
        player.positionChanged.connect(_skip_video_intro)
        _shared_player[0] = player
        _shared_sink[0] = sink
        _log.info("Shared video player created: %s", video_path)
        return True

    def _skip_video_intro(pos: int) -> None:
        """Loop between LOOP_START_MS and the end so the fade-from-black intro never shows."""
        player = _shared_player[0]
        if not player:
            return
        dur = player.duration()
        if dur <= LOOP_START_MS + 1000:
            return
        if pos < LOOP_START_MS or pos >= dur - LOOP_END_GUARD_MS:
            player.setPosition(LOOP_START_MS)

    def _stop_shared_player() -> None:
        if _shared_player[0]:
            _shared_player[0].stop()
            _shared_player[0].setSource(QUrl())
            _shared_player[0].deleteLater()
            _shared_player[0] = None
        if _shared_sink[0]:
            _shared_sink[0].deleteLater()
            _shared_sink[0] = None

    # ------------------------------------------------------------------ #
    #  Core toggle logic
    # ------------------------------------------------------------------ #

    def _show_overlay(break_mode: bool = False) -> None:
        if _active[0]:
            return
        if _active_rain[0]:
            _resume_active_after[0] = True
            _hide_active(fade=False, persist=False)
        _log.info("Overlay SHOW requested (break=%s)", break_mode)
        _active[0] = True
        sched.stop_break_timer()
        _create_overlays()

        _start_shared_player(_get_target_screens())

        level = _intensity()
        lockout = break_mode and _break["lockout"]
        for ov in overlays:
            ov.set_intensity(level)
            ov.set_lockout(lockout)
            ov.set_wiper_allowed(not break_mode)
            ov.activate()

        # Connect shared sink frames + position to all overlays
        if _shared_sink[0]:
            for ov in overlays:
                _shared_sink[0].videoFrameChanged.connect(ov._on_video_frame)
        if _shared_player[0]:
            _shared_player[0].positionChanged.connect(_broadcast_position)
            _shared_player[0].durationChanged.connect(_broadcast_duration)
            _shared_player[0].play()

        sound.play(volume=settings.get("rain_volume", 0.7) * level)
        tray.set_overlay_state(True)
        if settings.get("lightning_enabled", False):
            lightning.start()
        if lockout:
            _lock_timer.start()
        if not break_mode and settings.get("countdown_enabled", False):
            sched.start_countdown()
        if break_mode:
            tray.show_notification(
                "RainDelay",
                f"Time for a Rain Break \u2614  Press ESC or {_hotkey_name()} to end it.")
        else:
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
        _lock_timer.stop()
        lightning.stop()
        if _break["on"] and _break["lockout"]:
            _end_break()
        for ov in overlays:
            ov.set_lockout(False)
            ov.deactivate()
        _stop_shared_player()
        sound.stop()
        sched.stop_countdown()
        sched.restart_break_timer()
        tray.set_overlay_state(False)
        _log.info("Overlay HIDE complete")
        if _resume_active_after[0]:
            _resume_active_after[0] = False
            QTimer.singleShot(300, _show_active)

    def _toggle_overlay() -> None:
        if _active[0]:
            _hide_overlay()
        elif _active_rain[0]:
            _hide_active()
        elif settings.get("overlay_mode", "screensaver") == "active":
            _show_active()
        else:
            _show_overlay()

    # ------------------------------------------------------------------ #
    #  Active (click-through) mode
    # ------------------------------------------------------------------ #

    def _show_active(break_mode: bool = False) -> None:
        if _active_rain[0] or _active[0]:
            return
        _log.info("Active rain SHOW requested (break=%s)", break_mode)
        screens = _get_target_screens()
        if not _start_shared_player(screens):
            tray.show_notification("RainDelay",
                                   "Active mode needs a rain video in assets/.")
            if break_mode:
                _end_break()
            return
        _active_rain[0] = True
        processor = ActiveFrameProcessor(settings)
        _processor[0] = processor
        _shared_sink[0].videoFrameChanged.connect(processor.on_video_frame)
        level = _intensity()
        for scr in screens:
            ov = ActiveRainOverlay(settings, target_screen=scr)
            ov.set_intensity(level)
            processor.processed.connect(ov.on_processed)
            ov.activate()
            active_overlays.append(ov)
        _shared_player[0].play()

        if settings.get("active_sound_enabled", True):
            sound.play(volume=settings.get("active_volume", 0.3) * level,
                       fade_ms=800 if settings.get("active_fade", True) else 0)
        if settings.get("active_pause_fullscreen", True):
            watcher.start()
        if settings.get("lightning_enabled", False):
            lightning.start()
        tray.set_overlay_state(True)
        if break_mode:
            tray.show_notification(
                "RainDelay",
                f"Time for a Rain Break \u2614  Press {_hotkey_name()} to end it.")
        else:
            _persist(active_was_running=True)
            tray.show_notification(
                "RainDelay",
                f"Active rain on \u2614  Press {_hotkey_name()} to stop.")
        _log.info("Active rain SHOW complete — %d screen(s)", len(active_overlays))

    def _hide_active(fade: bool = True, persist: bool = True) -> None:
        if not _active_rain[0]:
            return
        _log.info("Active rain HIDE requested")
        _active_rain[0] = False
        was_break = _break["on"] and not _break["lockout"]
        watcher.stop()
        lightning.stop()
        for ov in active_overlays:
            ov.deactivate(fade=fade)
        active_overlays.clear()
        _stop_shared_player()
        if _processor[0]:
            _processor[0].shutdown()
            _processor[0].deleteLater()
            _processor[0] = None
        sound.stop(fade_ms=600 if fade and settings.get("active_fade", True) else 0)
        tray.set_overlay_state(False)
        if was_break:
            _end_break()
        elif persist:
            _persist(active_was_running=False)

    # ------------------------------------------------------------------ #
    #  Rain Break (recurring timed start, optional lockout + gradual ramp)
    # ------------------------------------------------------------------ #

    def _hotkey_name() -> str:
        return settings.get("hotkey_display", "Ctrl+Alt+R")

    def _ramp_progress() -> float:
        if not (_break["on"] and _break["ramp"] and _ramp_clock.isValid()):
            return 1.0
        dur_ms = max(1, int(settings.get("break_ramp_minutes", 10))) * 60_000
        return min(1.0, _ramp_clock.elapsed() / dur_ms)

    def _intensity() -> float:
        """Rain opacity/volume multiplier: ramps start_level -> 1.0 during a gradual break."""
        if not (_break["on"] and _break["ramp"]):
            return 1.0
        start = max(0, min(100, settings.get("break_start_level", 10))) / 100.0
        return start + (1.0 - start) * _ramp_progress()

    def _ramp_tick() -> None:
        level = _intensity()
        for ov in (overlays if _active[0] else active_overlays):
            ov.set_intensity(level)
        if _active[0]:
            sound.set_volume(settings.get("rain_volume", 0.7) * level)
        elif _active_rain[0] and settings.get("active_sound_enabled", True):
            sound.set_volume(settings.get("active_volume", 0.3) * level)
        if _ramp_progress() >= 1.0:
            lightning.set_storm(True)
            _ramp_timer.stop()

    def _guard_lockout() -> None:
        """Keep the break overlay focused so the desktop can't be used."""
        if not (_active[0] and overlays):
            return
        if QApplication.activeWindow() in overlays:
            return
        ov = overlays[0]
        ov.raise_()
        force_foreground(int(ov.winId()))
        ov.activateWindow()
        if ov._graphics_view:
            ov._graphics_view.setFocus()

    def _start_rain_break() -> None:
        if _active[0] or _break["on"]:
            return  # already in a (manual or scheduled) screensaver session
        lockout = not settings.get("break_interactive", True)
        gradual = settings.get("break_gradual", True)
        _log.info("Rain Break due (lockout=%s, gradual=%s)", lockout, gradual)
        _break.update(on=True, lockout=lockout, ramp=gradual)
        sched.stop_break_timer()
        if gradual:
            _ramp_clock.start()
            _ramp_timer.start()
        if lockout:
            _show_overlay(break_mode=True)
        else:
            if _active_rain[0]:
                _resume_ambient_after_break[0] = True
                _hide_active(fade=False, persist=False)
            _show_active(break_mode=True)

    def _end_break() -> None:
        if not _break["on"]:
            return
        _log.info("Rain Break ended")
        _break.update(on=False, lockout=False, ramp=False)
        _ramp_timer.stop()
        _ramp_clock.invalidate()
        _lock_timer.stop()
        lightning.set_storm(False)
        sched.restart_break_timer()
        if _resume_ambient_after_break[0]:
            _resume_ambient_after_break[0] = False
            QTimer.singleShot(700, _show_active)

    def _on_flash(level: float) -> None:
        for ov in (overlays if _active[0] else active_overlays):
            ov.set_flash(level)

    def _on_fullscreen_changed(hmon: int) -> None:
        if not _active_rain[0]:
            return
        any_suspended = False
        for ov in active_overlays:
            if hmon and ov.monitor_handle() == hmon:
                ov.suspend()
                any_suspended = True
            else:
                ov.resume()
        if any_suspended:
            sound.pause()
        else:
            sound.resume()

    def _set_mode(mode: str) -> None:
        if mode == settings.get("overlay_mode"):
            return
        _persist(overlay_mode=mode)
        tray.set_mode(mode)
        if _active_rain[0] and mode == "screensaver":
            _hide_active()
            QTimer.singleShot(700, _show_overlay)
        elif _active[0] and not _resume_active_after[0] and mode == "active":
            _hide_overlay()
            QTimer.singleShot(300, _show_active)

    def _trigger_all_wipers() -> None:
        """Trigger wiper sweep on all active overlays and restart video."""
        if _break["on"]:
            return
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
        new_mode = new_settings.get("overlay_mode", settings.get("overlay_mode"))
        old_mode = settings.get("overlay_mode")
        prev_sound_on = settings.get("active_sound_enabled", True)
        settings.update(new_settings)
        settings["overlay_mode"] = old_mode  # let _set_mode handle the switch
        for ov in overlays:
            ov.update_settings(settings)
        sched.apply_settings(settings)
        if _break["on"] or _active[0]:
            sched.stop_break_timer()  # restarted when the current session ends
        if _active[0] or _active_rain[0]:
            if settings.get("lightning_enabled", False):
                if not lightning.is_running():
                    lightning.start()
            else:
                lightning.stop()
        # Re-register hotkey if it changed
        hotkey.update_hotkey(
            settings.get("hotkey_mods", 0x0006),
            settings.get("hotkey_vk", 0x52),
        )

        if _active_rain[0]:
            if _processor[0]:
                _processor[0].update_settings(settings)
            for ov in active_overlays:
                ov.update_settings(settings)
            sound_on = settings.get("active_sound_enabled", True)
            if sound_on and not prev_sound_on:
                sound.play(volume=settings.get("active_volume", 0.3) * _intensity())
            elif prev_sound_on and not sound_on:
                sound.stop()
            else:
                sound.set_volume(settings.get("active_volume", 0.3) * _intensity())
            if settings.get("active_pause_fullscreen", True):
                watcher.start()
            else:
                watcher.stop()
        elif _active[0]:
            sound.set_volume(settings.get("rain_volume", 0.7) * _intensity())
        else:
            sound.update_settings(settings)

        # The dialog saved a stale copy of the live run-state; correct it.
        _persist(active_was_running=_active_rain[0], overlay_mode=old_mode)
        _set_mode(new_mode)

    def _quit() -> None:
        _resume_active_after[0] = False
        _resume_ambient_after_break[0] = False
        _hide_overlay()
        _hide_active(fade=False, persist=False)
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
    tray.mode_changed.connect(_set_mode)
    watcher.fullscreen_monitor_changed.connect(_on_fullscreen_changed)

    sched.timeout.connect(_hide_overlay)          # countdown expired
    sched.should_start.connect(_show_overlay)     # daily schedule
    sched.should_stop.connect(_hide_overlay)
    sched.break_due.connect(_start_rain_break)
    _ramp_timer.timeout.connect(_ramp_tick)
    _lock_timer.timeout.connect(_guard_lockout)
    lightning.flash.connect(_on_flash)

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

    if settings.get("active_resume_on_startup") and settings.get("active_was_running"):
        QTimer.singleShot(1500, _show_active)

    # ------------------------------------------------------------------ #
    #  Enter event loop
    # ------------------------------------------------------------------ #
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
