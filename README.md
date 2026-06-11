# RainDelay

A full-screen desktop rain overlay for mindful breaks.  
When active it turns your screen into a rain-streaked pane of glass — beautiful, calming, and fully blocking until you're ready to return.

---

## Features

| Feature | Detail |
|---|---|
| Rain animation | Falling drops → impact ripple → sliding streak (windshield physics) |
| Glass tint | Adjustable transparency (1–95 %) |
| Audio | Looping rain + random thunder, independent volume sliders |
| Global hotkey | **Ctrl+Alt+R** (configurable) — no admin required |
| System tray | Lives quietly in the notification area; right-click for menu |
| Countdown timer | Auto-dismisses after N minutes |
| Daily schedule | Start/stop at set times each day |
| Auto-start | Optional Windows startup shortcut |
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

### 3. (Optional) Add sound files

Place these files in the `sounds\` folder:

| File | Description |
|---|---|
| `sounds\rain.wav` | Looping ambient rain |
| `sounds\thunder.wav` | Thunder effect (played randomly every 20–90 s) |

**Free sources:**
- https://freesound.org/search/?q=rain+loop  
- https://freesound.org/search/?q=thunder

Download any `.wav` file and rename it.  Rain should be a long loop (30 s+); thunder can be a single crack.

### 4. Run

```cmd
python main.py
```

A raindrop icon appears in the system tray.

---

## Usage

| Action | How |
|---|---|
| Enable / disable overlay | Press **Ctrl+Alt+R** (or double-click tray icon, or right-click → Toggle) |
| Dismiss overlay immediately | Press **ESC** |
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
  "autostart": false
}
```

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
| No sound | Add `rain.wav` / `thunder.wav` to the `sounds\` folder; ensure pygame is installed |
| Hotkey doesn't work | Try a different combo in Settings; avoid combos used by Windows or other apps |
| Overlay won't dismiss | Press **ESC** (hotkey may conflict — change it in Settings) |
| `comtypes` error on autostart | Run `pip install comtypes` or disable the autostart feature |

---

## Project Structure

```
RainDelay/
├── main.py              # Entry point
├── overlay.py           # Full-screen rain glass window
├── rain_engine.py       # Drop physics (FALLING → IMPACT → STREAK)
├── hotkey_manager.py    # Win32 RegisterHotKey via ctypes
├── sound_manager.py     # pygame.mixer rain/thunder audio
├── scheduler.py         # Countdown + daily schedule timers
├── tray_icon.py         # System tray icon & context menu
├── control_panel.py     # Settings dialog (5 tabs)
├── settings_manager.py  # JSON settings load/save + autostart shortcut
├── requirements.txt
├── sounds/
│   ├── rain.wav         # (user-supplied)
│   └── thunder.wav      # (user-supplied)
└── assets/
    └── icon.png         # (optional; generated at runtime if absent)
```

---

## License

MIT — free to use, modify, and distribute.
