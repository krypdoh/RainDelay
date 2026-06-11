"""
scheduler.py
Handles two independent scheduling mechanisms:

1. Countdown timer  — emit `timeout` after N minutes.
2. Daily schedule   — emit `should_start` or `should_stop` when the wall clock
                      crosses the user-configured start / stop times (checked
                      every 30 seconds).

All signals are safe to connect directly to UI / overlay slots.
"""

from datetime import datetime, time as dtime
from typing import Optional

from PyQt6.QtCore import QObject, QTimer, pyqtSignal


class Scheduler(QObject):
    should_start = pyqtSignal()    # daily schedule: time to show overlay
    should_stop  = pyqtSignal()    # daily schedule: time to hide overlay
    timeout      = pyqtSignal()    # countdown expired

    def __init__(self, settings: dict, parent: Optional[QObject] = None):
        super().__init__(parent)
        self._settings = settings

        # Countdown (single-shot)
        self._countdown_timer = QTimer(self)
        self._countdown_timer.setSingleShot(True)
        self._countdown_timer.timeout.connect(self.timeout)

        # Daily schedule poll (repeating every 30 s)
        self._schedule_timer = QTimer(self)
        self._schedule_timer.setInterval(30_000)
        self._schedule_timer.timeout.connect(self._check_schedule)

        self._last_triggered: Optional[str] = None   # 'start' or 'stop'

        self.apply_settings(settings)

    # ------------------------------------------------------------------ #
    #  Public API
    # ------------------------------------------------------------------ #

    def apply_settings(self, settings: dict) -> None:
        self._settings = settings
        self._reschedule_all()

    def start_countdown(self) -> None:
        """Restart the countdown timer using current settings."""
        minutes = self._settings.get("countdown_minutes", 15)
        self._countdown_timer.start(int(minutes * 60 * 1000))

    def start_countdown_minutes(self, minutes: int) -> None:
        """Start a countdown for a specific number of minutes."""
        self._countdown_timer.start(int(minutes * 60 * 1000))

    def stop_countdown(self) -> None:
        self._countdown_timer.stop()

    def stop_all(self) -> None:
        self._countdown_timer.stop()
        self._schedule_timer.stop()

    def countdown_remaining_ms(self) -> int:
        """Return milliseconds remaining on countdown, or 0 if not running."""
        if self._countdown_timer.isActive():
            return self._countdown_timer.remainingTime()
        return 0

    # ------------------------------------------------------------------ #
    #  Internal
    # ------------------------------------------------------------------ #

    def _reschedule_all(self) -> None:
        # Countdown
        if self._settings.get("countdown_enabled", False):
            self.start_countdown()
        else:
            self._countdown_timer.stop()

        # Daily schedule
        if self._settings.get("schedule_enabled", False):
            self._schedule_timer.start()
            self._check_schedule()   # evaluate immediately
        else:
            self._schedule_timer.stop()

    def _check_schedule(self) -> None:
        now = datetime.now().time().replace(second=0, microsecond=0)

        start_str = self._settings.get("schedule_start", "12:00")
        stop_str  = self._settings.get("schedule_stop",  "13:00")

        try:
            h, m = map(int, start_str.split(":"))
            start_t = dtime(h, m)
        except Exception:
            return

        try:
            h, m = map(int, stop_str.split(":"))
            stop_t = dtime(h, m)
        except Exception:
            return

        # Determine which window we're in and emit only on transitions
        in_window = _time_in_range(start_t, stop_t, now)

        if in_window and self._last_triggered != "start":
            self._last_triggered = "start"
            self.should_start.emit()
        elif not in_window and self._last_triggered == "start":
            self._last_triggered = "stop"
            self.should_stop.emit()


def _time_in_range(start: dtime, end: dtime, current: dtime) -> bool:
    """True if current is within [start, end), handling overnight ranges."""
    if start <= end:
        return start <= current < end
    # Overnight range (e.g. 23:00 → 01:00)
    return current >= start or current < end
