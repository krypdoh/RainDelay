# RainDelay

A full-screen desktop rain overlay for mindful breaks.  
When active it turns your screen into a rain-streaked pane of glass — beautiful, calming, and fully blocking until you're ready to return.

---

## Features

| Feature | Detail |
|---|---|
| Rain video overlay | Full-screen rain video composited over a blurred desktop capture |
| Blur strength | Adjustable background blur intensity (0–100) |
| Rain opacity | Controls rain visibility (0–100); alpha-blended over the desktop |
| Darkness | Darken the background behind the rain (0–80) |
| Windshield wiper | Animated wiper sweeps across the screen on video loop or **W** key |
| Audio | Looping rain + thunder ambient sound |
| Global hotkey | **Ctrl+Alt+R** (configurable) — no admin required |
| System tray | Lives quietly in the notification area; right-click for menu |
| Multi-monitor | Overlays all screens simultaneously (configurable) |
| Countdown timer | Auto-dismisses after N minutes |
| Daily schedule | Start/stop at set times each day |
| Auto-start | Optional Windows startup shortcut |
| D3D11 resilience | Probes GPU availability; falls back to WMF backend if D3D11 is degraded |
| Settings | Saved to `%APPDATA%\RainDelay\settings.json` |

---

## Quick Start

### 1. Install Python 3.11+
Download from https://python.org/downloads  
Make sure to check **"Add Python to PATH"** during install.

### 2. Install dependencies

```cmd
cd "path\to\RainDelay"
pip install -r requirements.txt
```

### 3. Add rain video

Place a rain overlay video (rain filmed against a **black background**) in the `assets/` folder.  
Name it with the resolution for best performance: e.g. `rain_1920x1080.mp4`.

The app auto-selects the best match for each monitor's resolution.  
Search "rain overlay black background free" on Pexels, Pixabay, or YouTube — these are standard VFX assets.

### 4. (Optional) Add sound

The default audio file is `assets/jci-21-rain-and-thunder-sfx-12820.mp3`.  
Legacy fallbacks: `sounds/rain.wav`, `sounds/rain.mp3`.

**Free sources:**
- https://freesound.org/search/?q=rain+loop
- https://pixabay.com/sound-effects/search/rain/

### 5. Run

```cmd
python main.py
```

A raindrop icon appears in the system tray.

---

## Usage

| Action | How |
|---|---|
| Enable / disable overlay | Press **Ctrl+Alt+R** (or double-click tray icon, or right-click → Toggle) |
| Dismiss overlay immediately | Press **ESC** or **SPACEBAR** |
| Trigger wiper sweep | Press **W** while overlay is active |
| Toggle clock overlay | Click anywhere on the overlay |
| Open settings | Right-click tray icon → Settings… |
| Change hotkey | Settings → Hotkey tab → click the button and press your combo |
| Quit | Right-click tray icon → Quit RainDelay |

---

## Settings Reference

All settings are stored in `%APPDATA%\RainDelay\settings.json`.  
You can edit this file by hand if needed (the app will recreate it with defaults if it is missing or corrupt).

```json
{
  "transparency": 70,
  "darkness": 0,
  "blur_strength": 50,
  "rain_opacity": 40,
  "rain_speed": "medium",
  "rain_frequency": "moderate",
  "rain_volume": 0.7,
  "thunder_volume": 0.5,
  "thunder_enabled": true,
  "hotkey_mods": 6,
  "hotkey_vk": 82,
  "hotkey_display": "Ctrl+Alt+R",
  "countdown_minutes": 15,
  "countdown_enabled": false,
  "schedule_start": "12:00",
  "schedule_stop": "13:00",
  "schedule_enabled": false,
  "screens": "all",
  "autostart": false,
  "lowres_mode": false,
  "wiper_enabled": true
}
```

| Setting | Range | Description |
|---|---|---|
| `transparency` | 0–95 | Overlay transparency (higher = more see-through) |
| `darkness` | 0–80 | Darken the blurred background |
| `blur_strength` | 0–100 | How much to blur the captured desktop |
| `rain_opacity` | 0–100 | Rain video visibility (alpha blend) |
| `screens` | `"all"` or `[0,1,...]` | Which monitors to overlay |
| `wiper_enabled` | true/false | Windshield wiper animation on video loop |
| `lowres_mode` | true/false | Render at lower resolution for weak GPUs |

### `hotkey_mods` bitmask

| Value | Modifier |
|---|---|
| 1 | Alt |
| 2 | Ctrl |
| 4 | Shift |
| 8 | Win |

Combine with `|`  (e.g. Ctrl+Alt = 2 | 1 = **6**).

### `hotkey_vk` — Windows virtual key codes

Letters A–Z: 65–90 (decimal).  `R` = **82**.  
Digits 0–9: 48–57.  Function keys F1–F12: 112–123.

---

## Hotkey Implementation Note

RainDelay uses **Windows `RegisterHotKey`** (via `ctypes`) to register global hotkeys.  
This is the same mechanism used by Snipping Tool, Teams, and most Windows apps.  
**No administrator privileges are required.**

If the hotkey fails to register (another app already claimed it), a tray notification will tell you — simply open Settings and pick a different combo.

---

## Auto-start

Enable "Launch RainDelay when Windows starts" in Settings → System.  
This creates a `.lnk` shortcut in:

```
%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\
```

To remove it manually, open `shell:startup` in Explorer and delete `RainDelay.lnk`.

---

## Troubleshooting

| Problem | Fix |
|---|---|
| No tray icon appears | Ensure `pip install PyQt6` succeeded; check Windows "Hidden icons" in the tray |
| No sound | Ensure audio file exists in `assets/` or `sounds/`; SoundManager degrades silently if missing |
| No rain video | Place a rain overlay `.mp4` in `assets/` (e.g. `rain_1920x1080.mp4`) |
| D3D11 error on second run | The app auto-detects this and switches to WMF backend; a reboot fully clears it |
| Hotkey doesn't work | Try a different combo in Settings; avoid combos used by Windows or other apps |
| Overlay won't dismiss | Press **ESC** (hotkey may conflict — change it in Settings) |
| Low FPS | Use a video matching your screen resolution; enable `lowres_mode` in settings |

---

## Project Structure

```
RainDelay/
├── main.py              # Entry point; D3D11 probe, shared video player, signal wiring
├── overlay.py           # Full-screen overlay: blurred desktop + video compositing + wiper
├── rain_engine.py       # Drop physics fallback (FALLING → IMPACT → STREAK) — Qt-free
├── hotkey_manager.py    # Win32 RegisterHotKey via ctypes
├── sound_manager.py     # QMediaPlayer looping audio with graceful degradation
├── scheduler.py         # Countdown + daily schedule timers
├── tray_icon.py         # System tray icon & context menu
├── control_panel.py     # Settings dialog (5 tabs)
├── settings_manager.py  # JSON settings load/save + autostart shortcut
├── requirements.txt
├── RainDelay.spec       # PyInstaller build spec
├── assets/
│   ├── rain_1920x1080.mp4         # Rain overlay video (black background)
│   ├── wiper_transparent_pro.png  # Wiper blade image
│   ├── ArchivoBlack-Regular.ttf   # UI font
│   └── jci-21-rain-and-thunder-sfx-12820.mp3  # Ambient audio
└── sounds/
    └── (legacy fallback audio files)
```

---

## License

MIT — free to use, modify, and distribute.
