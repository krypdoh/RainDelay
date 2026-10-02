"""
lightning.py
Lightning flash controller shared by all overlay modes.

Emits `flash(level)` (0.0-1.0) driving a short white double-flicker.
Strikes every 30 s; in "storm" mode (rain at full intensity) strikes
come more often at random intervals.
"""

import random

from PyQt6.QtCore import QObject, QTimer, QVariantAnimation, QEasingCurve, pyqtSignal

CALM_INTERVAL_MS = 30_000
STORM_INTERVAL_MS = (5_000, 15_000)
STRIKE_MS = 450


class Lightning(QObject):
    flash = pyqtSignal(float)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._storm = False
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self._strike)
        self._anim = QVariantAnimation(self)
        self._anim.setDuration(STRIKE_MS)
        self._anim.setStartValue(0.0)
        # bright flash (held so frame sampling can't miss the peak), dip, second flicker, decay
        self._anim.setKeyValueAt(0.04, 1.0)
        self._anim.setKeyValueAt(0.14, 1.0)
        self._anim.setKeyValueAt(0.24, 0.15)
        self._anim.setKeyValueAt(0.34, 0.75)
        self._anim.setEndValue(0.0)
        self._anim.setEasingCurve(QEasingCurve.Type.Linear)
        self._anim.valueChanged.connect(lambda v: self.flash.emit(float(v)))

    def start(self) -> None:
        self._schedule()

    def stop(self) -> None:
        self._timer.stop()
        self._anim.stop()
        self._storm = False
        self.flash.emit(0.0)

    def is_running(self) -> bool:
        return self._timer.isActive() or self._anim.state() == QVariantAnimation.State.Running

    def set_storm(self, storm: bool) -> None:
        if storm != self._storm:
            self._storm = storm
            if self._timer.isActive():
                self._schedule()

    def _schedule(self) -> None:
        ms = random.randint(*STORM_INTERVAL_MS) if self._storm else CALM_INTERVAL_MS
        self._timer.start(ms)

    def _strike(self) -> None:
        self._anim.stop()
        self._anim.start()
        self._schedule()
