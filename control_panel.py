"""
control_panel.py
Settings dialog for RainDelay.

Tabs:
  Rain    — transparency, speed, frequency
  Sound   — rain volume, thunder volume, enable toggles
  Hotkey  — record a new global hotkey
  Timers  — countdown duration, daily start/stop schedule
  System  — auto-start with Windows
"""

from __future__ import annotations

from PyQt6.QtWidgets import (
    QApplication, QDialog, QTabWidget, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QSlider, QComboBox, QCheckBox, QSpinBox, QTimeEdit,
    QPushButton, QGroupBox, QDialogButtonBox, QSizePolicy,
    QFrame, QKeySequenceEdit,
)
from PyQt6.QtCore import Qt, QTime, pyqtSignal, QKeyCombination
from PyQt6.QtGui  import QKeySequence

import settings_manager as sm
from hotkey_manager import (
    qt_key_to_vk, qt_modifiers_to_win, mods_vk_to_display
)


class ControlPanel(QDialog):
    """Modal settings dialog.  Emits `settings_saved` with the new dict on Accept."""

    settings_saved = pyqtSignal(dict)

    def __init__(self, settings: dict, parent=None):
        super().__init__(parent)
        self._settings = dict(settings)   # work on a copy
        self._pending_mods: int = settings.get("hotkey_mods", 0x0006)
        self._pending_vk:   int = settings.get("hotkey_vk",   0x52)

        self.setWindowTitle("RainDelay Settings")
        self.setMinimumWidth(440)
        self.setWindowFlags(
            self.windowFlags() & ~Qt.WindowType.WindowContextHelpButtonHint
        )

        root = QVBoxLayout(self)
        tabs = QTabWidget()
        root.addWidget(tabs)

        tabs.addTab(self._rain_tab(),   "🌧  Rain")
        tabs.addTab(self._sound_tab(),  "🔊  Sound")
        tabs.addTab(self._hotkey_tab(), "⌨  Hotkey")
        tabs.addTab(self._timers_tab(), "⏱  Timers")
        tabs.addTab(self._system_tab(), "⚙  System")

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save |
            QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self._on_save)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

    # ================================================================== #
    #  Tab builders
    # ================================================================== #

    def _rain_tab(self) -> QWidget:
        w   = QWidget()
        lay = QVBoxLayout(w)
        lay.setSpacing(14)

        # Transparency
        lay.addWidget(_heading("Glass Transparency"))
        self._transparency_slider = _labeled_slider(
            lay, "Transparent", "Opaque",
            1, 95, self._settings.get("transparency", 70)
        )
        self._transparency_label = QLabel(
            f"{self._settings.get('transparency', 70)}%"
        )
        self._transparency_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(self._transparency_label)
        self._transparency_slider.valueChanged.connect(
            lambda v: self._transparency_label.setText(f"{v}%")
        )

        lay.addWidget(_separator())

        # Darkness
        lay.addWidget(_heading("Background Darkness"))
        self._darkness_slider = _labeled_slider(
            lay, "None", "Dark",
            0, 80, self._settings.get("darkness", 0)
        )
        self._darkness_label = QLabel(
            f"{self._settings.get('darkness', 0)}%"
        )
        self._darkness_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(self._darkness_label)
        self._darkness_slider.valueChanged.connect(
            lambda v: self._darkness_label.setText(f"{v}%")
        )

        lay.addWidget(_separator())

        # Speed
        lay.addWidget(_heading("Rain Speed"))
        self._speed_combo = QComboBox()
        self._speed_combo.addItems(["Slow", "Medium", "Fast"])
        self._speed_combo.setCurrentText(
            self._settings.get("rain_speed", "medium").capitalize()
        )
        lay.addWidget(self._speed_combo)

        lay.addWidget(_separator())

        # Frequency
        lay.addWidget(_heading("Rain Frequency"))
        self._freq_combo = QComboBox()
        self._freq_combo.addItems(["Light", "Moderate", "Heavy"])
        self._freq_combo.setCurrentText(
            self._settings.get("rain_frequency", "moderate").capitalize()
        )
        lay.addWidget(self._freq_combo)

        lay.addStretch()
        return w

    def _sound_tab(self) -> QWidget:
        w   = QWidget()
        lay = QVBoxLayout(w)
        lay.setSpacing(14)

        lay.addWidget(_heading("Rain Sound"))
        self._rain_vol_slider = _labeled_slider(
            lay, "Silent", "Loud",
            0, 100, int(self._settings.get("rain_volume", 0.7) * 100)
        )

        lay.addWidget(_separator())

        lay.addWidget(_heading("Thunder Sound"))
        self._thunder_enabled_cb = QCheckBox("Enable thunder")
        self._thunder_enabled_cb.setChecked(
            self._settings.get("thunder_enabled", True)
        )
        lay.addWidget(self._thunder_enabled_cb)

        self._thunder_vol_slider = _labeled_slider(
            lay, "Silent", "Loud",
            0, 100, int(self._settings.get("thunder_volume", 0.5) * 100)
        )
        self._thunder_enabled_cb.toggled.connect(
            self._thunder_vol_slider.setEnabled
        )
        self._thunder_vol_slider.setEnabled(
            self._settings.get("thunder_enabled", True)
        )

        note = QLabel(
            "<small>Place <tt>rain.wav</tt> and <tt>thunder.wav</tt> in the "
            "<tt>sounds/</tt> folder.<br>"
            "Free sounds at <a href='https://freesound.org'>freesound.org</a>."
            "</small>"
        )
        note.setTextFormat(Qt.TextFormat.RichText)
        note.setOpenExternalLinks(True)
        note.setWordWrap(True)
        lay.addWidget(note)

        lay.addStretch()
        return w

    def _hotkey_tab(self) -> QWidget:
        w   = QWidget()
        lay = QVBoxLayout(w)
        lay.setSpacing(14)

        lay.addWidget(_heading("Global Hotkey"))

        current_display = self._settings.get("hotkey_display", "Ctrl+Alt+R")
        self._hotkey_current_lbl = QLabel(f"Current hotkey:  <b>{current_display}</b>")
        self._hotkey_current_lbl.setTextFormat(Qt.TextFormat.RichText)
        lay.addWidget(self._hotkey_current_lbl)

        lay.addWidget(QLabel("Press a new key combination:"))
        self._hotkey_recorder = _HotkeyRecorder()
        self._hotkey_recorder.combo_changed.connect(self._on_hotkey_combo)
        lay.addWidget(self._hotkey_recorder)

        note = QLabel(
            "<small>Requires a modifier key (Ctrl, Alt, Shift, or Win) "
            "combined with a letter, digit, or function key.<br>"
            "Uses Windows <tt>RegisterHotKey</tt> — no admin required.</small>"
        )
        note.setTextFormat(Qt.TextFormat.RichText)
        note.setWordWrap(True)
        lay.addWidget(note)

        lay.addStretch()
        return w

    def _timers_tab(self) -> QWidget:
        w   = QWidget()
        lay = QVBoxLayout(w)
        lay.setSpacing(14)

        # Countdown
        grp_cd = QGroupBox("Countdown Timer")
        cd_lay = QVBoxLayout(grp_cd)

        self._countdown_enabled_cb = QCheckBox("Enable countdown timer")
        self._countdown_enabled_cb.setChecked(
            self._settings.get("countdown_enabled", False)
        )
        cd_lay.addWidget(self._countdown_enabled_cb)

        row = QHBoxLayout()
        row.addWidget(QLabel("Duration:"))
        self._countdown_spin = QSpinBox()
        self._countdown_spin.setRange(1, 480)
        self._countdown_spin.setSuffix(" min")
        self._countdown_spin.setValue(self._settings.get("countdown_minutes", 15))
        row.addWidget(self._countdown_spin)
        row.addStretch()
        cd_lay.addLayout(row)

        self._countdown_enabled_cb.toggled.connect(self._countdown_spin.setEnabled)
        self._countdown_spin.setEnabled(
            self._settings.get("countdown_enabled", False)
        )

        lay.addWidget(grp_cd)

        # Daily schedule
        grp_sch = QGroupBox("Daily Schedule")
        sch_lay = QVBoxLayout(grp_sch)

        self._schedule_enabled_cb = QCheckBox("Enable daily schedule")
        self._schedule_enabled_cb.setChecked(
            self._settings.get("schedule_enabled", False)
        )
        sch_lay.addWidget(self._schedule_enabled_cb)

        def _parse_time(s: str, fallback: str) -> QTime:
            try:
                h, m = map(int, s.split(":"))
                return QTime(h, m)
            except Exception:
                h, m = map(int, fallback.split(":"))
                return QTime(h, m)

        row2 = QHBoxLayout()
        row2.addWidget(QLabel("Start:"))
        self._sched_start = QTimeEdit(
            _parse_time(self._settings.get("schedule_start", "12:00"), "12:00")
        )
        self._sched_start.setDisplayFormat("HH:mm")
        row2.addWidget(self._sched_start)
        row2.addSpacing(20)
        row2.addWidget(QLabel("Stop:"))
        self._sched_stop = QTimeEdit(
            _parse_time(self._settings.get("schedule_stop", "13:00"), "13:00")
        )
        self._sched_stop.setDisplayFormat("HH:mm")
        row2.addWidget(self._sched_stop)
        row2.addStretch()
        sch_lay.addLayout(row2)

        def _toggle_schedule(enabled: bool) -> None:
            self._sched_start.setEnabled(enabled)
            self._sched_stop.setEnabled(enabled)

        self._schedule_enabled_cb.toggled.connect(_toggle_schedule)
        _toggle_schedule(self._settings.get("schedule_enabled", False))

        lay.addWidget(grp_sch)
        lay.addStretch()
        return w

    def _system_tab(self) -> QWidget:
        w   = QWidget()
        lay = QVBoxLayout(w)
        lay.setSpacing(14)

        # ── Monitor / Screen selection ──────────────────────────────────
        lay.addWidget(_heading("Display Screens"))

        from PyQt6.QtWidgets import QListWidget, QListWidgetItem, QAbstractItemView
        self._screen_list = QListWidget()
        self._screen_list.setSelectionMode(
            QAbstractItemView.SelectionMode.MultiSelection
        )

        screens = QApplication.screens()
        screen_setting = self._settings.get("screens", "all")

        for i, scr in enumerate(screens):
            geom = scr.geometry()
            name = scr.name() or f"Screen {i + 1}"
            label = f"{name}  ({geom.width()}x{geom.height()} @ {geom.x()},{geom.y()})"
            item = QListWidgetItem(label)
            self._screen_list.addItem(item)
            # Select it if in settings
            if screen_setting == "all":
                item.setSelected(True)
            elif isinstance(screen_setting, list) and i in screen_setting:
                item.setSelected(True)

        self._screen_list.setMaximumHeight(120)
        lay.addWidget(self._screen_list)

        note_scr = QLabel(
            "<small>Select which monitors to display the rain overlay on.<br>"
            "Hold <b>Ctrl</b> to select multiple.</small>"
        )
        note_scr.setTextFormat(Qt.TextFormat.RichText)
        note_scr.setWordWrap(True)
        lay.addWidget(note_scr)

        lay.addWidget(_separator())

        # ── Performance ─────────────────────────────────────────────────
        lay.addWidget(_heading("Performance"))

        self._lowres_mode_cb = QCheckBox("Low-resolution mode (1080p)")
        self._lowres_mode_cb.setChecked(self._settings.get("lowres_mode", False))
        lay.addWidget(self._lowres_mode_cb)

        note_perf = QLabel(
            "<small>Renders video at 1920×1080 then upscales to screen size.<br>"
            "<b>Enable this if video playback is choppy</b> (trades quality for speed).</small>"
        )
        note_perf.setTextFormat(Qt.TextFormat.RichText)
        note_perf.setWordWrap(True)
        lay.addWidget(note_perf)

        lay.addWidget(_separator())

        # ── Auto-start ──────────────────────────────────────────────────
        lay.addWidget(_heading("Windows Integration"))

        self._autostart_cb = QCheckBox("Launch RainDelay when Windows starts")
        self._autostart_cb.setChecked(self._settings.get("autostart", False))
        lay.addWidget(self._autostart_cb)

        note = QLabel(
            "<small>Creates a shortcut in your Windows Startup folder "
            "(<tt>%APPDATA%\\Microsoft\\Windows\\Start Menu\\"
            "Programs\\Startup\\</tt>).</small>"
        )
        note.setTextFormat(Qt.TextFormat.RichText)
        note.setWordWrap(True)
        lay.addWidget(note)

        lay.addStretch()
        return w

    # ================================================================== #
    #  Signals / slots
    # ================================================================== #

    def _on_hotkey_combo(self, mods: int, vk: int, display: str) -> None:
        self._pending_mods = mods
        self._pending_vk   = vk
        self._hotkey_current_lbl.setText(f"New hotkey:  <b>{display}</b>")

    def _on_save(self) -> None:
        self._settings["transparency"]       = self._transparency_slider.value()
        self._settings["darkness"]           = self._darkness_slider.value()
        self._settings["rain_speed"]         = self._speed_combo.currentText().lower()
        self._settings["rain_frequency"]     = self._freq_combo.currentText().lower()
        self._settings["rain_volume"]        = self._rain_vol_slider.value() / 100.0
        self._settings["thunder_volume"]     = self._thunder_vol_slider.value() / 100.0
        self._settings["thunder_enabled"]    = self._thunder_enabled_cb.isChecked()
        self._settings["hotkey_mods"]        = self._pending_mods
        self._settings["hotkey_vk"]          = self._pending_vk
        self._settings["hotkey_display"]     = mods_vk_to_display(
            self._pending_mods, self._pending_vk
        )
        self._settings["countdown_minutes"]  = self._countdown_spin.value()
        self._settings["countdown_enabled"]  = self._countdown_enabled_cb.isChecked()
        self._settings["schedule_start"]     = self._sched_start.time().toString("HH:mm")
        self._settings["schedule_stop"]      = self._sched_stop.time().toString("HH:mm")
        self._settings["schedule_enabled"]   = self._schedule_enabled_cb.isChecked()
        self._settings["lowres_mode"]        = self._lowres_mode_cb.isChecked()
        self._settings["autostart"]          = self._autostart_cb.isChecked()

        # Screen selection
        selected_indices = [
            self._screen_list.row(item)
            for item in self._screen_list.selectedItems()
        ]
        total_screens = self._screen_list.count()
        if len(selected_indices) == total_screens or len(selected_indices) == 0:
            self._settings["screens"] = "all"
        else:
            self._settings["screens"] = sorted(selected_indices)

        sm.save(self._settings)
        self.settings_saved.emit(dict(self._settings))
        self.accept()


# ================================================================== #
#  Hotkey recorder widget
# ================================================================== #

class _HotkeyRecorder(QWidget):
    """
    Click the button, then press a key combo.
    Emits combo_changed(mods: int, vk: int, display: str).
    """

    combo_changed = pyqtSignal(int, int, str)

    def __init__(self, parent=None):
        super().__init__(parent)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        self._btn = QPushButton("Click here, then press your key combo…")
        self._btn.setCheckable(True)
        self._btn.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self._btn.toggled.connect(self._on_toggled)
        lay.addWidget(self._btn)
        self._recording = False

    def _on_toggled(self, checked: bool) -> None:
        self._recording = checked
        if checked:
            self._btn.setText("Listening… (press combo now)")
            self._btn.grabKeyboard()
        else:
            self._btn.releaseKeyboard()
            self._btn.setText("Click here, then press your key combo…")

    def keyPressEvent(self, event) -> None:
        if not self._recording:
            super().keyPressEvent(event)
            return

        mod_keys = {
            Qt.Key.Key_Control, Qt.Key.Key_Alt,
            Qt.Key.Key_Shift,   Qt.Key.Key_Meta,
        }
        key = Qt.Key(event.key())
        if key in mod_keys:
            return   # wait for the non-modifier key

        vk   = qt_key_to_vk(event.key())
        mods = qt_modifiers_to_win(event.modifiers())

        if vk == 0 or mods == 0:
            self._btn.setText("Need modifier + letter/digit/F-key  — try again")
            return

        display = mods_vk_to_display(mods, vk)
        self._btn.setChecked(False)
        self._btn.releaseKeyboard()
        self._btn.setText(f"Recorded:  {display}")
        self._recording = False
        self.combo_changed.emit(mods, vk, display)


# ================================================================== #
#  Small layout helpers
# ================================================================== #

def _heading(text: str) -> QLabel:
    lbl = QLabel(f"<b>{text}</b>")
    lbl.setTextFormat(Qt.TextFormat.RichText)
    return lbl


def _separator() -> QFrame:
    line = QFrame()
    line.setFrameShape(QFrame.Shape.HLine)
    line.setFrameShadow(QFrame.Shadow.Sunken)
    return line


def _labeled_slider(
    parent_layout: QVBoxLayout,
    left_label: str,
    right_label: str,
    minimum: int,
    maximum: int,
    value: int,
) -> QSlider:
    slider = QSlider(Qt.Orientation.Horizontal)
    slider.setMinimum(minimum)
    slider.setMaximum(maximum)
    slider.setValue(value)
    slider.setTickPosition(QSlider.TickPosition.TicksBelow)
    slider.setTickInterval((maximum - minimum) // 10)

    row = QHBoxLayout()
    row.addWidget(QLabel(left_label))
    row.addWidget(slider, 1)
    row.addWidget(QLabel(right_label))
    parent_layout.addLayout(row)
    return slider
