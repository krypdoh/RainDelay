"""
tray_icon.py
System-tray icon and context menu for RainDelay.

Layout (inspired by RainBreak):
  • Start / Stop RainDelay
  ─────────
  • 1 Min Break
  • 2 Min Break
  • 5 Min Break
  • 10 Min Break
  • 15 Min Break
  • 20 Min Break
  • 30 Min Break
  • 60 Min Break
  • Edit Breaks…
  ─────────
  • Settings…
  • About RainDelay
  ─────────
  • Quit RainDelay
"""

from PyQt6.QtWidgets import (
    QSystemTrayIcon, QMenu, QApplication, QInputDialog,
)
from PyQt6.QtGui import QIcon, QAction, QPixmap, QPainter, QColor, QPainterPath
from PyQt6.QtCore import Qt, QObject, pyqtSignal, QPointF

import settings_manager as sm

DEFAULT_BREAK_MINUTES = [1, 2, 5, 10, 15, 20, 30, 60]


class TrayIcon(QObject):
    toggle_overlay = pyqtSignal()
    start_timed    = pyqtSignal(int)   # emits minutes for a timed break
    open_settings  = pyqtSignal()
    quit_app       = pyqtSignal()

    def __init__(self, parent: QObject | None = None):
        super().__init__(parent)
        self._overlay_active = False

        # Load custom break times from settings, or use defaults
        settings = sm.load()
        self._break_minutes: list[int] = settings.get(
            "break_presets", DEFAULT_BREAK_MINUTES[:]
        )
        self._break_minutes.sort()

        icon = _load_or_generate_icon()
        self._tray = QSystemTrayIcon(icon, self)
        self._tray.setToolTip("RainDelay")

        self._menu = QMenu()
        self._build_menu()

        self._tray.setContextMenu(self._menu)
        # Use a lambda to avoid PyQt6 6.11 enum conversion issue
        self._tray.activated.connect(lambda r: self._on_activated(r))
        self._tray.show()

    # ------------------------------------------------------------------ #
    #  Menu construction
    # ------------------------------------------------------------------ #

    def _build_menu(self):
        menu = self._menu
        menu.clear()

        # --- Start / Stop ---
        self._toggle_action = QAction("Start RainDelay", menu)
        self._toggle_action.triggered.connect(self.toggle_overlay)
        menu.addAction(self._toggle_action)

        menu.addSeparator()

        # --- Timer presets ---
        for mins in self._break_minutes:
            label = f"{mins} Min Break"
            action = QAction(label, menu)
            action.triggered.connect(lambda checked, m=mins: self.start_timed.emit(m))
            menu.addAction(action)

        # --- Edit breaks ---
        edit_action = QAction("Edit Breaks…", menu)
        edit_action.triggered.connect(self._edit_breaks)
        menu.addAction(edit_action)

        menu.addSeparator()

        # --- Settings / About ---
        settings_action = QAction("Settings…", menu)
        settings_action.triggered.connect(self.open_settings)
        menu.addAction(settings_action)

        about_action = QAction("About RainDelay", menu)
        about_action.triggered.connect(self._show_about)
        menu.addAction(about_action)

        menu.addSeparator()

        # --- Quit ---
        quit_action = QAction("Quit RainDelay", menu)
        quit_action.triggered.connect(self.quit_app)
        menu.addAction(quit_action)

        # Sync toggle label with current state
        self._sync_toggle_text()

    # ------------------------------------------------------------------ #
    #  Public helpers
    # ------------------------------------------------------------------ #

    def set_overlay_state(self, active: bool) -> None:
        self._overlay_active = active
        self._sync_toggle_text()

    def show_notification(self, title: str, message: str) -> None:
        self._tray.showMessage(title, message,
                               QSystemTrayIcon.MessageIcon.Information, 3000)

    # ------------------------------------------------------------------ #
    #  Internal
    # ------------------------------------------------------------------ #

    def _sync_toggle_text(self):
        if self._overlay_active:
            self._toggle_action.setText("Stop RainDelay")
            self._tray.setToolTip("RainDelay  [ACTIVE]")
        else:
            self._toggle_action.setText("Start RainDelay")
            self._tray.setToolTip("RainDelay")

    def _on_activated(self, reason) -> None:
        if reason == QSystemTrayIcon.ActivationReason.DoubleClick:
            self.toggle_overlay.emit()

    def _edit_breaks(self) -> None:
        """Let the user edit the break preset minutes via a simple dialog."""
        current_text = ", ".join(str(m) for m in self._break_minutes)
        text, ok = QInputDialog.getText(
            None,
            "Edit Break Presets",
            "Enter break durations in minutes (comma-separated):",
            text=current_text,
        )
        if ok and text.strip():
            # Parse comma-separated integers
            new_vals = []
            for part in text.split(","):
                part = part.strip()
                if part.isdigit() and int(part) > 0:
                    new_vals.append(int(part))
            if new_vals:
                new_vals = sorted(set(new_vals))
                self._break_minutes = new_vals
                # Persist to settings
                settings = sm.load()
                settings["break_presets"] = new_vals
                sm.save(settings)
                # Rebuild menu
                self._build_menu()

    def _show_about(self) -> None:
        from PyQt6.QtWidgets import QMessageBox
        msg = QMessageBox()
        msg.setWindowTitle("About RainDelay")
        msg.setText(
            "<b>RainDelay</b><br>"
            "Version 1.0<br>"
            "Paul R. Charovkine - 2026<br><br>"
            "A desktop rain overlay for taking mindful breaks.<br>"
            "Hotkey: <b>Ctrl+Alt+R</b> (configurable)<br><br>"
            "Sound files: place <tt>rain.wav</tt> and <tt>thunder.wav</tt><br>"
            "in the <tt>sounds/</tt> folder next to this app.<br><br>"
            "Website: <a href='https://krypdoh.github.io/RainDelay/'>krypdoh.github.io/RainDelay</a>"
        )
        msg.setTextFormat(Qt.TextFormat.RichText)
        msg.exec()


# ------------------------------------------------------------------ #
#  Icon generation
# ------------------------------------------------------------------ #

def _load_or_generate_icon() -> QIcon:
    """Load assets/raindelay.ico if it exists, otherwise generate a raindrop icon."""
    import sys
    from pathlib import Path
    base = Path(sys._MEIPASS) if hasattr(sys, "_MEIPASS") else Path(__file__).resolve().parent
    icon_path = base / "assets" / "raindelay.ico"
    if icon_path.exists():
        return QIcon(str(icon_path))
    return QIcon(_generate_raindrop_pixmap(64))


def _generate_raindrop_pixmap(size: int) -> QPixmap:
    """Draw a simple raindrop on a transparent pixmap."""
    px = QPixmap(size, size)
    px.fill(Qt.GlobalColor.transparent)

    p = QPainter(px)
    p.setRenderHint(QPainter.RenderHint.Antialiasing, True)

    cx = size / 2.0
    # Teardrop path
    path = QPainterPath()
    tip   = QPointF(cx, size * 0.92)
    top   = QPointF(cx, size * 0.12)
    left  = QPointF(cx - size * 0.32, size * 0.52)
    right = QPointF(cx + size * 0.32, size * 0.52)

    path.moveTo(tip)
    path.cubicTo(left,  QPointF(cx - size * 0.38, size * 0.25), top)
    path.cubicTo(QPointF(cx + size * 0.38, size * 0.25), right, tip)
    path.closeSubpath()

    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QColor(100, 160, 230, 220))
    p.drawPath(path)

    # Highlight glint
    p.setBrush(QColor(255, 255, 255, 90))
    p.drawEllipse(QPointF(cx - size * 0.1, size * 0.28),
                  size * 0.08, size * 0.12)

    p.end()
    return px
