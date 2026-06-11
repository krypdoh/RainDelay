"""
settings_manager.py
Handles loading/saving of RainDelay settings to %APPDATA%\\RainDelay\\settings.json.
Also manages the Windows auto-start shortcut.
"""

import json
import os
import sys
from pathlib import Path

APPDATA = Path(os.environ.get("APPDATA", Path.home())) / "RainDelay"
SETTINGS_FILE = APPDATA / "settings.json"
STARTUP_FOLDER = Path(os.environ.get("APPDATA", "")) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup"
SHORTCUT_NAME = "RainDelay.lnk"

DEFAULTS = {
    "transparency": 70,          # % transparent (higher = more see-through)
    "darkness": 0,               # 0-80: darken the background (0=none, 80=very dark)
    "rain_speed": "medium",       # slow / medium / fast
    "rain_frequency": "moderate", # light / moderate / heavy
    "rain_volume": 0.7,
    "thunder_volume": 0.5,
    "thunder_enabled": True,
    "hotkey_mods": 0x0002 | 0x0004,  # MOD_CONTROL | MOD_ALT  (ctypes values)
    "hotkey_vk": 0x52,               # Virtual key for 'R'
    "hotkey_display": "Ctrl+Alt+R",  # Human-readable label
    "countdown_minutes": 15,
    "countdown_enabled": False,
    "schedule_start": "12:00",
    "schedule_stop":  "13:00",
    "schedule_enabled": False,
    "screens": "all",            # "all" or list of screen indices e.g. [0, 1]
    "autostart": False,
    "lowres_mode": False,        # Render at 1080p then upscale (better performance on weak GPUs)
}

# Speed / frequency lookup tables used by rain_engine and overlay
SPEED_MAP = {"slow": 2.0, "medium": 4.5, "fast": 8.0}
FREQ_MAP  = {"light": 0.5, "moderate": 1.5, "heavy": 3.5}  # new drops per frame (avg)


def load() -> dict:
    """Return settings dict; missing keys filled from DEFAULTS."""
    APPDATA.mkdir(parents=True, exist_ok=True)
    if SETTINGS_FILE.exists():
        try:
            with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            # Forward-compat: add new keys that may not be in older saves
            for k, v in DEFAULTS.items():
                data.setdefault(k, v)
            return data
        except (json.JSONDecodeError, OSError):
            pass
    return dict(DEFAULTS)


def save(settings: dict) -> None:
    """Persist settings to disk; also applies auto-start shortcut."""
    APPDATA.mkdir(parents=True, exist_ok=True)
    with open(SETTINGS_FILE, "w", encoding="utf-8") as f:
        json.dump(settings, f, indent=2)
    _apply_autostart(settings.get("autostart", False))


def _apply_autostart(enabled: bool) -> None:
    """Create or remove the Windows startup shortcut for RainDelay."""
    if sys.platform != "win32":
        return
    shortcut_path = STARTUP_FOLDER / SHORTCUT_NAME
    if enabled:
        try:
            _create_shortcut(shortcut_path)
        except Exception:
            pass
    else:
        try:
            if shortcut_path.exists():
                shortcut_path.unlink()
        except Exception:
            pass


def _create_shortcut(shortcut_path: Path) -> None:
    """
    Create a .lnk shortcut using the Windows Script Host COM object.
    No third-party library required.
    """
    import winreg  # noqa: F401 – just to verify we're on Windows
    import comtypes.client  # type: ignore

    shell = comtypes.client.CreateObject("WScript.Shell")
    shortcut = shell.CreateShortCut(str(shortcut_path))
    shortcut.TargetPath = sys.executable
    shortcut.Arguments = f'"{Path(sys.argv[0]).resolve()}"'
    shortcut.WorkingDirectory = str(Path(sys.argv[0]).resolve().parent)
    shortcut.Description = "RainDelay – desktop rain overlay"
    shortcut.Save()
