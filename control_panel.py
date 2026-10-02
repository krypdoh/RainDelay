"""
control_panel.py
Settings dialog for RainDelay.

Tabs:
  Rain    — transparency, speed, frequency, lightning (all modes)
  Active  — click-through rain: opacity, haze, tint, sound, fps, behaviour
  Break   — Rain Break: recurring timed start, lockout, gradual ramp
  Sound   — rain volume, thunder volume, enable toggles
  Hotkey  — mode the hotkey starts + record a new global hotkey
  Timers  — countdown duration, daily start/stop schedule
  System  — auto-start with Windows
"""

from __future__ import annotations

from PyQt6.QtWidgets import (
    QApplication, QDialog, QTabWidget, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QSlider, QComboBox, QCheckBox, QSpinBox, QTimeEdit,
    QPushButton, QGroupBox, QDialogButtonBox, QSizePolicy,
    QFrame, QKeySequenceEdit, QColorDialog,
)
from PyQt6.QtCore import Qt, QTime, pyqtSignal, QKeyCombination
from PyQt6.QtGui  import QKeySequence, QColor

import settings_manager as sm
from overlay import _find_rain_video
from hotkey_manager import (
    qt_key_to_vk, qt_modifiers_to_win, mods_vk_to_display
)

_TINT_PRESETS = [("White", "#FFFFFF"), ("Cool Blue", "#A8D8FF"), ("Soft Gray", "#C8C8C8")]
_CUSTOM_TINT = "Custom…"


class ControlPanel(QDialog):
    """Modal settings dialog.  Emits `settings_saved` with the new dict on Accept."""

    settings_saved = pyqtSignal(dict)

    def __init__(self, settings: dict, parent=None):
        super().__init__(parent)
        self._settings = dict(settings)   # work on a copy
        self._pending_mods: int = settings.get("hotkey_mods", 0x0006)
        self._pending_vk:   int = settings.get("hotkey_vk",   0x52)

        self.setWindowTitle("RainDelay Settings")
        self.setWindowFlags(
            self.windowFlags() & ~Qt.WindowType.WindowContextHelpButtonHint
        )

        root = QVBoxLayout(self)
        tabs = QTabWidget()
        tabs.setUsesScrollButtons(False)  # forces the dialog wide enough to show every tab
        root.addWidget(tabs)

        tabs.addTab(self._rain_tab(),   "🌧  Rain")
        tabs.addTab(self._active_tab(), "💧  Active Rain")
        tabs.addTab(self._break_tab(),  "☕  Rain Break")
        tabs.addTab(self._sound_tab(),  "🔊  Sound")
        tabs.addTab(self._hotkey_tab(), "⌨  Hotkey")
        tabs.addTab(self._timers_tab(), "⏱  Timers")
        tabs.addTab(self._system_tab(), "⚙  System")

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save |
            QDialogButtonBox.StandardButton.Apply |
            QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self._on_save)
        buttons.rejected.connect(self.reject)
        buttons.button(QDialogButtonBox.StandardButton.Apply).clicked.connect(self._apply)
        root.addWidget(buttons)

        hint = self.sizeHint()
        self.resize(max(hint.width(), 760), max(hint.height(), 680))

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

        # Background Blur
        lay.addWidget(_heading("Desktop Background Blur"))
        self._blur_slider = _labeled_slider(
            lay, "None", "Max",
            0, 100, self._settings.get("blur_strength", 50)
        )
        self._blur_label = QLabel(
            f"{self._settings.get('blur_strength', 50)}%"
        )
        self._blur_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(self._blur_label)
        self._blur_slider.valueChanged.connect(
            lambda v: self._blur_label.setText(f"{v}%")
        )

        # Speed & Frequency — only relevant for rendered fallback (no video)
        self._speed_sep = _separator()
        lay.addWidget(self._speed_sep)
        self._speed_heading = _heading("Rain Speed")
        lay.addWidget(self._speed_heading)
        self._speed_combo = QComboBox()
        self._speed_combo.addItems(["Slow", "Medium", "Fast"])
        self._speed_combo.setCurrentText(
            self._settings.get("rain_speed", "medium").capitalize()
        )
        lay.addWidget(self._speed_combo)

        self._freq_sep = _separator()
        lay.addWidget(self._freq_sep)

        self._freq_heading = _heading("Rain Frequency")
        lay.addWidget(self._freq_heading)
        self._freq_combo = QComboBox()
        self._freq_combo.addItems(["Light", "Moderate", "Heavy"])
        self._freq_combo.setCurrentText(
            self._settings.get("rain_frequency", "moderate").capitalize()
        )
        lay.addWidget(self._freq_combo)

        # Hide speed/frequency when video mode is active (settings are baked into video)
        has_video = _find_rain_video() is not None
        if has_video:
            self._speed_sep.hide()
            self._speed_heading.hide()
            self._speed_combo.hide()
            self._freq_sep.hide()
            self._freq_heading.hide()
            self._freq_combo.hide()

        lay.addWidget(_separator())

        # Rain Opacity
        lay.addWidget(_heading("Rain Opacity"))
        self._rain_opacity_slider = _labeled_slider(
            lay, "Subtle", "Full",
            0, 100, self._settings.get("rain_opacity", 40)
        )
        self._rain_opacity_label = QLabel(
            f"{self._settings.get('rain_opacity', 40)}%"
        )
        self._rain_opacity_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(self._rain_opacity_label)
        self._rain_opacity_slider.valueChanged.connect(
            lambda v: self._rain_opacity_label.setText(f"{v}%")
        )

        lay.addWidget(_separator())

        # Wiper
        self._wiper_enabled_cb = QCheckBox("Enable Wiper  (press W to trigger)")
        self._wiper_enabled_cb.setChecked(self._settings.get("wiper_enabled", True))
        lay.addWidget(self._wiper_enabled_cb)

        self._lightning_cb = QCheckBox("⚡ Lightning flashes  (all modes)")
        self._lightning_cb.setChecked(self._settings.get("lightning_enabled", False))
        lay.addWidget(self._lightning_cb)
        note_l = QLabel(
            "<small>Brief white flash every 30 s; more frequent and random once a "
            "Rain Break reaches full rain. <b>Not recommended if you are "
            "sensitive to flashing light.</b></small>"
        )
        note_l.setTextFormat(Qt.TextFormat.RichText)
        note_l.setWordWrap(True)
        lay.addWidget(note_l)

        lay.addStretch()
        return w

    def _active_tab(self) -> QWidget:
        w   = QWidget()
        lay = QVBoxLayout(w)
        lay.setSpacing(10)

        intro = QLabel(
            "<small>Rain over your live desktop. Clicks and typing pass straight "
            "through. Choose <b>Active Mode</b> on the Hotkey tab or the tray "
            "<b>Mode</b> menu.</small>"
        )
        intro.setTextFormat(Qt.TextFormat.RichText)
        intro.setWordWrap(True)
        lay.addWidget(intro)

        lay.addWidget(_heading("Rain Opacity"))
        self._act_opacity_slider = _labeled_slider(
            lay, "Subtle", "Full", 0, 100,
            self._settings.get("active_rain_opacity", 35)
        )

        lay.addWidget(_heading("Remove Haze (black level)"))
        self._act_black_slider = _labeled_slider(
            lay, "None", "Strong", 0, 60,
            self._settings.get("active_black_level", 15)
        )

        row = QHBoxLayout()
        row.addWidget(QLabel("Brightness:"))
        self._act_brightness_combo = QComboBox()
        self._act_brightness_combo.addItems(["Normal", "Bright", "Extra"])
        self._act_brightness_combo.setCurrentText(
            self._settings.get("active_brightness", "normal").capitalize()
        )
        row.addWidget(self._act_brightness_combo)
        row.addSpacing(16)
        row.addWidget(QLabel("Tint:"))
        self._act_tint = self._settings.get("active_tint", "#FFFFFF").upper()
        self._act_tint_combo = QComboBox()
        self._act_tint_combo.addItems([n for n, _ in _TINT_PRESETS] + [_CUSTOM_TINT])
        preset = next((n for n, c in _TINT_PRESETS if c == self._act_tint), _CUSTOM_TINT)
        self._act_tint_combo.setCurrentText(preset)
        self._act_tint_combo.activated.connect(self._on_tint_picked)
        row.addWidget(self._act_tint_combo)
        self._act_tint_swatch = QLabel()
        self._act_tint_swatch.setFixedSize(18, 18)
        row.addWidget(self._act_tint_swatch)
        row.addStretch()
        lay.addLayout(row)
        self._update_tint_swatch()

        lay.addWidget(_separator())

        self._act_sound_cb = QCheckBox("Play rain sound")
        self._act_sound_cb.setChecked(self._settings.get("active_sound_enabled", True))
        lay.addWidget(self._act_sound_cb)
        self._act_vol_slider = _labeled_slider(
            lay, "Silent", "Loud", 0, 100,
            int(self._settings.get("active_volume", 0.3) * 100)
        )
        self._act_sound_cb.toggled.connect(self._act_vol_slider.setEnabled)
        self._act_vol_slider.setEnabled(self._act_sound_cb.isChecked())

        lay.addWidget(_separator())

        row2 = QHBoxLayout()
        row2.addWidget(QLabel("Frame rate:"))
        self._act_fps_combo = QComboBox()
        self._act_fps_combo.addItems(["15", "24", "30"])
        self._act_fps_combo.setCurrentText(str(self._settings.get("active_fps", 24)))
        row2.addWidget(self._act_fps_combo)
        row2.addWidget(QLabel("fps  <small>(lower = less CPU)</small>"))
        row2.addStretch()
        lay.addLayout(row2)

        self._act_capture_cb = QCheckBox("Hide rain from screen sharing / recording")
        self._act_capture_cb.setChecked(self._settings.get("active_hide_from_capture", True))
        lay.addWidget(self._act_capture_cb)

        self._act_fullscreen_cb = QCheckBox("Pause when a full-screen app is in front")
        self._act_fullscreen_cb.setChecked(self._settings.get("active_pause_fullscreen", True))
        lay.addWidget(self._act_fullscreen_cb)

        self._act_fade_cb = QCheckBox("Fade in / out")
        self._act_fade_cb.setChecked(self._settings.get("active_fade", True))
        lay.addWidget(self._act_fade_cb)

        self._act_resume_cb = QCheckBox("Resume Active rain when RainDelay starts")
        self._act_resume_cb.setChecked(self._settings.get("active_resume_on_startup", False))
        lay.addWidget(self._act_resume_cb)

        lay.addStretch()
        return w

    def _on_tint_picked(self, index: int) -> None:
        name = self._act_tint_combo.itemText(index)
        if name == _CUSTOM_TINT:
            color = QColorDialog.getColor(QColor(self._act_tint), self, "Rain Tint")
            if color.isValid():
                self._act_tint = color.name().upper()
        else:
            self._act_tint = dict(_TINT_PRESETS)[name]
        self._update_tint_swatch()

    def _update_tint_swatch(self) -> None:
        self._act_tint_swatch.setStyleSheet(
            f"background: {self._act_tint}; border: 1px solid #666;"
        )

    def _break_tab(self) -> QWidget:
        w   = QWidget()
        lay = QVBoxLayout(w)
        lay.setSpacing(12)

        self._break_enabled_cb = QCheckBox("Start a Rain Break automatically")
        self._break_enabled_cb.setChecked(self._settings.get("break_enabled", False))
        lay.addWidget(self._break_enabled_cb)

        grp = QGroupBox()
        g = QVBoxLayout(grp)

        row = QHBoxLayout()
        row.addWidget(QLabel("Every:"))
        self._break_minutes_spin = QSpinBox()
        self._break_minutes_spin.setRange(1, 480)
        self._break_minutes_spin.setSuffix(" min")
        self._break_minutes_spin.setValue(self._settings.get("break_minutes", 60))
        row.addWidget(self._break_minutes_spin)
        row.addStretch()
        g.addLayout(row)

        g.addWidget(_separator())

        self._break_interactive_cb = QCheckBox("Allow desktop interaction during the break")
        self._break_interactive_cb.setChecked(self._settings.get("break_interactive", True))
        g.addWidget(self._break_interactive_cb)
        note_lock = QLabel(
            "<small>On: click-through rain over your desktop (Active Rain settings). "
            "Off: blurs and locks the screen until you press <b>ESC</b> or the hotkey.</small>"
        )
        note_lock.setTextFormat(Qt.TextFormat.RichText)
        note_lock.setWordWrap(True)
        g.addWidget(note_lock)

        g.addWidget(_separator())

        self._break_gradual_cb = QCheckBox("Gradually increase rain and volume")
        self._break_gradual_cb.setChecked(self._settings.get("break_gradual", True))
        g.addWidget(self._break_gradual_cb)

        row2 = QHBoxLayout()
        row2.addWidget(QLabel("Reach full rain after:"))
        self._break_ramp_spin = QSpinBox()
        self._break_ramp_spin.setRange(1, 120)
        self._break_ramp_spin.setSuffix(" min")
        self._break_ramp_spin.setValue(self._settings.get("break_ramp_minutes", 10))
        row2.addWidget(self._break_ramp_spin)
        row2.addStretch()
        g.addLayout(row2)

        g.addWidget(QLabel("Starting level:"))
        self._break_start_slider = _labeled_slider(
            g, "Faint", "Full", 0, 100, self._settings.get("break_start_level", 10)
        )

        def _sync_ramp(on: bool) -> None:
            self._break_ramp_spin.setEnabled(on)
            self._break_start_slider.setEnabled(on)

        self._break_gradual_cb.toggled.connect(_sync_ramp)
        _sync_ramp(self._break_gradual_cb.isChecked())

        self._break_enabled_cb.toggled.connect(grp.setEnabled)
        grp.setEnabled(self._break_enabled_cb.isChecked())
        lay.addWidget(grp)

        note = QLabel(
            "<small>The timer restarts after each break ends. Opacity and volume "
            "ramp up to the Rain / Active Rain tab values.</small>"
        )
        note.setTextFormat(Qt.TextFormat.RichText)
        note.setWordWrap(True)
        lay.addWidget(note)

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

        row = QHBoxLayout()
        row.addWidget(_heading("Hotkey starts:"))
        self._mode_combo = QComboBox()
        self._mode_combo.addItem("Screensaver Mode", "screensaver")
        self._mode_combo.addItem("Active Mode (click-through)", "active")
        self._mode_combo.setCurrentIndex(
            max(0, self._mode_combo.findData(self._settings.get("overlay_mode", "screensaver")))
        )
        row.addWidget(self._mode_combo, 1)
        lay.addLayout(row)

        lay.addWidget(_separator())

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
        self._apply()
        self.accept()

    def _apply(self) -> None:
        """Save and push settings live without closing the dialog."""
        self._settings["transparency"]       = self._transparency_slider.value()
        self._settings["darkness"]           = self._darkness_slider.value()
        self._settings["blur_strength"]      = self._blur_slider.value()
        self._settings["rain_speed"]         = self._speed_combo.currentText().lower()
        self._settings["rain_frequency"]     = self._freq_combo.currentText().lower()
        self._settings["rain_opacity"]       = self._rain_opacity_slider.value()
        self._settings["wiper_enabled"]      = self._wiper_enabled_cb.isChecked()
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
        self._settings["overlay_mode"]       = self._mode_combo.currentData()
        self._settings["active_rain_opacity"] = self._act_opacity_slider.value()
        self._settings["active_black_level"] = self._act_black_slider.value()
        self._settings["active_brightness"]  = self._act_brightness_combo.currentText().lower()
        self._settings["active_tint"]        = self._act_tint
        self._settings["active_sound_enabled"] = self._act_sound_cb.isChecked()
        self._settings["active_volume"]      = self._act_vol_slider.value() / 100.0
        self._settings["active_fps"]         = int(self._act_fps_combo.currentText())
        self._settings["active_hide_from_capture"] = self._act_capture_cb.isChecked()
        self._settings["active_pause_fullscreen"]  = self._act_fullscreen_cb.isChecked()
        self._settings["active_fade"]        = self._act_fade_cb.isChecked()
        self._settings["active_resume_on_startup"] = self._act_resume_cb.isChecked()
        self._settings["lightning_enabled"]  = self._lightning_cb.isChecked()
        self._settings["break_enabled"]      = self._break_enabled_cb.isChecked()
        self._settings["break_minutes"]      = self._break_minutes_spin.value()
        self._settings["break_interactive"]  = self._break_interactive_cb.isChecked()
        self._settings["break_gradual"]      = self._break_gradual_cb.isChecked()
        self._settings["break_ramp_minutes"] = self._break_ramp_spin.value()
        self._settings["break_start_level"]  = self._break_start_slider.value()

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
        self._hotkey_current_lbl.setText(
            f"Current hotkey:  <b>{self._settings['hotkey_display']}</b>"
        )


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
