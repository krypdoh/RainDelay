"""
hotkey_manager.py
Global hotkey registration using Windows ctypes RegisterHotKey / UnregisterHotKey.

No admin privileges required — this is the Windows-native mechanism used by every
app that registers global hotkeys (e.g. Snipping Tool, Teams, etc.).

Architecture:
  • A background thread runs a Win32 message loop.
  • RegisterHotKey posts WM_HOTKEY messages to that thread's message queue.
  • The thread emits a Qt signal that is safely delivered to the main thread.

Modifier constants (combinable via |):
  MOD_ALT         0x0001
  MOD_CONTROL     0x0002
  MOD_SHIFT       0x0004
  MOD_WIN         0x0008
  MOD_NOREPEAT    0x4000  ← prevents auto-repeat when key is held
"""

import ctypes
import ctypes.wintypes as wt
import threading
from typing import Optional

from PyQt6.QtCore import QObject, pyqtSignal

# Win32 constants
WM_HOTKEY    = 0x0312
MOD_NOREPEAT = 0x4000
HOTKEY_ID    = 1   # arbitrary id; we only use one hotkey

user32 = ctypes.windll.user32  # type: ignore[attr-defined]


class HotkeyManager(QObject):
    """
    Registers a global hotkey using Win32 RegisterHotKey (no admin required).
    Emits `activated` on the Qt main thread when the hotkey fires.
    """

    activated = pyqtSignal()   # safe to connect to UI slots

    def __init__(self, mods: int, vk: int, parent: Optional[QObject] = None):
        super().__init__(parent)
        self._mods = mods
        self._vk   = vk
        self._thread: Optional[threading.Thread] = None
        self._hwnd_holder: list = []   # thread writes hwnd here so we can unregister

    # ------------------------------------------------------------------ #
    #  Public API
    # ------------------------------------------------------------------ #

    def start(self) -> bool:
        """
        Spawn the message-loop thread and register the hotkey.
        Returns True if the hotkey was registered successfully.
        """
        ready = threading.Event()
        success_box: list = [False]

        self._thread = threading.Thread(
            target=self._message_loop,
            args=(ready, success_box),
            daemon=True,
            name="HotkeyThread",
        )
        self._thread.start()
        ready.wait(timeout=3.0)
        return success_box[0]

    def stop(self) -> None:
        """Unregister the hotkey and signal the message loop to exit."""
        if self._hwnd_holder:
            hwnd = self._hwnd_holder[0]
            user32.UnregisterHotKey(hwnd, HOTKEY_ID)
            # Post WM_QUIT to the thread's message queue
            user32.PostThreadMessageW(
                ctypes.windll.kernel32.GetThreadId(  # type: ignore[attr-defined]
                    ctypes.c_void_p(self._thread.ident if self._thread else 0)
                ),
                0x0012,   # WM_QUIT
                0, 0,
            )

    def update_hotkey(self, mods: int, vk: int) -> bool:
        """Replace the current hotkey with a new one (stop → update → start)."""
        self.stop()
        self._mods = mods
        self._vk   = vk
        self._hwnd_holder.clear()
        return self.start()

    # ------------------------------------------------------------------ #
    #  Background message loop
    # ------------------------------------------------------------------ #

    def _message_loop(self, ready: threading.Event, success_box: list) -> None:
        """Runs in a daemon thread.  Never call Qt UI methods directly from here."""

        # Create a message-only window so RegisterHotKey has a valid hwnd.
        # (Passing hwnd=None also works but ties the hotkey to the thread queue.)
        # We use the thread-queue approach for simplicity; it requires a
        # PeekMessage / GetMessage loop instead of a window procedure.
        #
        # Thread-queue hotkeys work fine:
        #   RegisterHotKey(NULL, id, mods, vk)  → WM_HOTKEY posted to thread queue

        ok = user32.RegisterHotKey(
            None,                          # hwnd = NULL → thread queue
            HOTKEY_ID,
            self._mods | MOD_NOREPEAT,
            self._vk,
        )
        success_box[0] = bool(ok)
        self._hwnd_holder.append(None)     # placeholder so stop() can unregister
        ready.set()

        if not ok:
            return

        msg = wt.MSG()
        while True:
            result = user32.GetMessageW(ctypes.byref(msg), None, 0, 0)
            if result == 0 or result == -1:
                break
            if msg.message == WM_HOTKEY and msg.wParam == HOTKEY_ID:
                # Emit the Qt signal (thread-safe; Qt queues it to main thread)
                self.activated.emit()
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))

        user32.UnregisterHotKey(None, HOTKEY_ID)


# ------------------------------------------------------------------ #
#  Virtual-key helpers (used by control_panel hotkey recorder)
# ------------------------------------------------------------------ #

# Common VK codes for display purposes
VK_NAMES = {
    0x08: "Backspace", 0x09: "Tab",      0x0D: "Enter",  0x1B: "Esc",
    0x20: "Space",     0x21: "PgUp",     0x22: "PgDn",   0x23: "End",
    0x24: "Home",      0x25: "Left",     0x26: "Up",     0x27: "Right",
    0x28: "Down",      0x2C: "PrtSc",    0x2D: "Insert", 0x2E: "Delete",
    0x70: "F1",  0x71: "F2",  0x72: "F3",  0x73: "F4",
    0x74: "F5",  0x75: "F6",  0x76: "F7",  0x77: "F8",
    0x78: "F9",  0x79: "F10", 0x7A: "F11", 0x7B: "F12",
}

MOD_NAMES = {
    0x0001: "Alt",
    0x0002: "Ctrl",
    0x0004: "Shift",
    0x0008: "Win",
}


def vk_to_char(vk: int) -> str:
    """Return a human-readable name for a virtual key code."""
    if vk in VK_NAMES:
        return VK_NAMES[vk]
    if 0x30 <= vk <= 0x39:
        return chr(vk)
    if 0x41 <= vk <= 0x5A:
        return chr(vk)
    return f"0x{vk:02X}"


def mods_vk_to_display(mods: int, vk: int) -> str:
    """e.g. mods=0x0006, vk=0x52  →  'Ctrl+Alt+R'"""
    parts = [name for bit, name in MOD_NAMES.items() if mods & bit]
    parts.append(vk_to_char(vk))
    return "+".join(parts)


def qt_key_to_vk(qt_key: int) -> int:
    """
    Convert a Qt.Key integer to a Windows virtual key code.
    Covers A–Z, 0–9, and the function keys used in hotkey combos.
    For anything else returns 0 (not usable as a hotkey).
    """
    from PyQt6.QtCore import Qt
    k = Qt.Key(qt_key)
    # Letters A–Z
    if Qt.Key.Key_A <= k <= Qt.Key.Key_Z:
        return 0x41 + (k - Qt.Key.Key_A)
    # Digits 0–9
    if Qt.Key.Key_0 <= k <= Qt.Key.Key_9:
        return 0x30 + (k - Qt.Key.Key_0)
    # Function keys
    fk_map = {
        Qt.Key.Key_F1: 0x70,  Qt.Key.Key_F2: 0x71,  Qt.Key.Key_F3: 0x72,
        Qt.Key.Key_F4: 0x73,  Qt.Key.Key_F5: 0x74,  Qt.Key.Key_F6: 0x75,
        Qt.Key.Key_F7: 0x76,  Qt.Key.Key_F8: 0x77,  Qt.Key.Key_F9: 0x78,
        Qt.Key.Key_F10: 0x79, Qt.Key.Key_F11: 0x7A, Qt.Key.Key_F12: 0x7B,
    }
    return fk_map.get(k, 0)


def qt_modifiers_to_win(qt_mods) -> int:
    """Convert a Qt.KeyboardModifiers value to a Win32 MOD_* bitmask."""
    from PyQt6.QtCore import Qt
    result = 0
    if qt_mods & Qt.KeyboardModifier.ControlModifier:
        result |= 0x0002   # MOD_CONTROL
    if qt_mods & Qt.KeyboardModifier.AltModifier:
        result |= 0x0001   # MOD_ALT
    if qt_mods & Qt.KeyboardModifier.ShiftModifier:
        result |= 0x0004   # MOD_SHIFT
    if qt_mods & Qt.KeyboardModifier.MetaModifier:
        result |= 0x0008   # MOD_WIN
    return result
