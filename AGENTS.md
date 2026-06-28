# RainDelay – Agent Instructions

RainDelay is a Windows-only full-screen rain overlay app (Python 3.11 + PyQt6). It lives in the system tray, plays looping ambient rain audio, and is toggled by a global hotkey. No web stack, no database.

## Quick Facts

| Item | Value |
| ------ | ------- |
| Entry point | `main.py` |
| Runtime | Python 3.11+, Windows only |
| UI toolkit | PyQt6 (Qt 6.6+) |
| Only dependency | `PyQt6>=6.6.0` (see `requirements.txt`) |
| Settings file | `%APPDATA%\RainDelay\settings.json` |
| Log file | `%APPDATA%\RainDelay\raindelay.log` |
| Version scheme | `YYYY.MM.DD.HHmm` (update in `main.py` header comment) |

## Running the App

```powershell
# Install deps (once)
pip install -r requirements.txt

# Run
python main.py
```

## Building the Executable

```powershell
pyinstaller RainDelay.spec --clean
```

Output: `dist/RainDelay/RainDelay.exe`. The spec handles exclusions, asset bundling (`assets/`, `sounds/`), and hidden imports (`PyQt6.QtMultimedia`).

## Architecture

Each module has a single responsibility. `main.py` owns all wiring.

```text
main.py           → Entry point; constructs all components, wires Qt signals, enters event loop
overlay.py        → RainOverlay(QWidget): full-screen window per monitor; video or rendered fallback
rain_engine.py    → Pure Python physics (no Qt); FALLING→BEAD→STREAK state machine
tray_icon.py      → QSystemTrayIcon + context menu; emits signals to main.py
control_panel.py  → 5-tab QDialog for settings; emits settings_saved(dict)
scheduler.py      → Countdown timer + daily schedule; emits should_start/should_stop/timeout
hotkey_manager.py → Win32 RegisterHotKey in a background thread; emits activated() signal
sound_manager.py  → QMediaPlayer looping audio with graceful degradation
settings_manager.py → load()/save() for %APPDATA%\RainDelay\settings.json; DEFAULTS dict
```

### Signal Flow

```text
hotkey.activated  ──┐
tray.toggle_overlay ─┤──→ _toggle_overlay() → _show/_hide_overlay()
                    ─┘
sched.timeout ────────→ _hide_overlay()
sched.should_start ───→ _show_overlay()
tray.start_timed ─────→ _start_timed_break(minutes)
tray.open_settings ───→ ControlPanel.exec()
control_panel.settings_saved → _apply_new_settings()
overlay.dismiss ──────→ _hide_overlay()
```

## Key Conventions

### Settings

- **Single source of truth**: `settings_manager.DEFAULTS` — add new keys here first with a sensible default.
- Settings are passed as plain `dict` everywhere; never import `settings_manager` in leaf modules.
- `load()` automatically back-fills missing keys from `DEFAULTS` (forward-compatible with old files).
- Volume stored as `float 0.0–1.0` internally; control panel UI shows `0–100`.

### Screens / Multi-Monitor

- `settings["screens"]` is either `"all"` or a `list[int]` of screen indices.
- `main.py:_get_target_screens()` resolves this to a list of `QScreen`.
- One `RainOverlay` instance per target screen; they are recreated on every `_show_overlay()` call.

### Qt Patterns

- All UI work happens on the Qt main thread. `HotkeyManager` uses a daemon thread + queued signal to cross the thread boundary safely.
- `app.setQuitOnLastWindowClosed(False)` — the app stays alive via the tray icon.
- Overlay windows use `Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint | Qt.WindowType.Tool`.

### Video Rendering (overlay.py)

- Video frames are composited with **`CompositionMode_Screen`** — black pixels are transparent, which makes rain/water visible over the blurred desktop capture.
- Frames are pre-scaled in `_on_video_frame()` (not at paint time) to avoid per-frame upscaling costs.
- If video resolution is lower than screen resolution, performance degrades badly (see [PERFORMANCE_ANALYSIS.md](PERFORMANCE_ANALYSIS.md)).
- Video asset search order: `rain_native.*` → `rain.*` → `rain_lowres.*` (in `assets/`).

### Rain Physics (rain_engine.py)

- No Qt imports — pure Python. Safe to unit test without a display.
- `RainEngine.update()` advances one frame; call it at your desired framerate.
- `SPEED_MAP` and `FREQ_MAP` in `settings_manager.py` translate string settings to numeric values.

### Hotkey (hotkey_manager.py)

- Uses `ctypes` + `RegisterHotKey(NULL, ...)` — no admin required.
- `MOD_NOREPEAT = 0x4000` is always OR-ed in to prevent repeat-fire when key is held.
- Helper functions (`vk_to_char`, `mods_vk_to_display`, `qt_key_to_vk`, `qt_modifiers_to_win`) live in `hotkey_manager.py` and are imported by `control_panel.py`.

### Sound

- Single combined audio file: `assets/jci-21-rain-and-thunder-sfx-12820.mp3`.
- Legacy fallbacks: `sounds/rain.wav`, `sounds/rain.mp3`.
- `SoundManager` is a no-op if file is missing — never throw on missing audio.

### Autostart

- Windows startup shortcut written to `%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\RainDelay.lnk`.
- Implemented via Win32 COM in `settings_manager._create_shortcut()`.

## Adding a New Setting

1. Add key + default to `settings_manager.DEFAULTS`.
2. Add UI control in the appropriate `control_panel.py` tab.
3. Add corresponding logic in the relevant module (overlay, scheduler, etc.).
4. No migration needed — `load()` auto-fills missing keys.

## Common Pitfalls

- **Don't import Qt in `rain_engine.py`** — it is intentionally Qt-free for testability.
- **Don't call `QApplication.screens()` before `QApplication` is constructed** — `main.py` does this once at startup.
- **PyInstaller**: Any new Qt module used at runtime needs to be added to `hiddenimports` in `RainDelay.spec`.
- **Low-res video**: If `PERF` log shows >25ms/frame upscaling, the video asset resolution is too low for the monitor. See [PERFORMANCE_ANALYSIS.md](PERFORMANCE_ANALYSIS.md).
- **Hotkey conflicts**: `RegisterHotKey` silently fails if another app holds the combo. The tray notification is the only feedback.
- **Single instance**: Enforced only by a lock file (`raindelay.lock`), not a Qt mutex — the lock file is advisory.
