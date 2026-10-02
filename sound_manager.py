"""
sound_manager.py
Ambient audio for RainDelay using Qt6 Multimedia (QMediaPlayer).

• Loops a single rain+thunder MP3 gaplessly when the overlay is active:
  two players alternate and equal-power crossfade inside the file's
  full-level section, skipping its silent/faded head and tail.
• Separate volume control (rain_volume / active_volume)
• Graceful no-op if sound file is missing or Qt Multimedia unavailable
"""

import math
import sys
from pathlib import Path

from PyQt6.QtCore import QUrl, QVariantAnimation, QTimer, QElapsedTimer
from PyQt6.QtMultimedia import QMediaPlayer, QAudioOutput

# PyInstaller onefile support: _MEIPASS is the temp folder for bundled data
_HERE = Path(getattr(sys, '_MEIPASS', Path(__file__).resolve().parent))
# Primary: combined rain+thunder MP3 in assets/
SOUND_FILE = _HERE / "assets" / "jci-21-rain-and-thunder-sfx-12820.mp3"
# Fallback locations
_FALLBACKS = [
    _HERE / "sounds" / "rain.wav",
    _HERE / "sounds" / "rain.mp3",
]

# Full-level section of SOUND_FILE (measured: silence 0-1s, fade-in to 3.5s,
# fade-out from 57.9s, silence after 60.8s).  (start_ms, end_ms) or None = whole file.
_LOOP_WINDOWS = {SOUND_FILE.name: (3500, 57800)}
CROSSFADE_MS = 1000
_TICK_MS = 30


def _find_sound_file():
    """Return the first existing sound file path, or None."""
    if SOUND_FILE.exists():
        return SOUND_FILE
    for f in _FALLBACKS:
        if f.exists():
            return f
    return None


class SoundManager:
    def __init__(self, settings: dict):
        self._volume = settings.get("rain_volume", 0.7)
        self._gain = 1.0                  # master fade multiplier (0..1)
        self._enabled = False
        self._paused = False
        self._players: list[QMediaPlayer] = []
        self._outputs: list[QAudioOutput] = []
        self._cur = 0                     # index of the player currently in front
        self._xfade = QElapsedTimer()     # valid while crossfading
        self._loop_window: tuple[int, int] | None = None
        self._fade_anim: QVariantAnimation | None = None
        self._tick_timer = QTimer()
        self._tick_timer.setInterval(_TICK_MS)
        self._tick_timer.timeout.connect(self._tick)
        self._init_player()

    # ------------------------------------------------------------------ #
    #  Public API
    # ------------------------------------------------------------------ #

    def play(self, volume: float | None = None, fade_ms: int = 0) -> None:
        """Start looping the rain+thunder audio (optionally at a mode-specific volume)."""
        if not self._players:
            return
        self._stop_fade()
        self._stop_all()
        if volume is not None:
            self._volume = volume
        self._enabled = True
        self._paused = False
        self._gain = 0.0 if fade_ms else 1.0
        self._apply_volumes()
        front = self._players[self._cur]
        front.setPosition(self._loop_start())
        front.play()
        self._tick_timer.start()
        if fade_ms:
            self._fade_gain(1.0, fade_ms)

    def stop(self, fade_ms: int = 0) -> None:
        """Stop playback."""
        if not self._players:
            return
        self._enabled = False
        if fade_ms and not self._paused and self._any_playing():
            self._fade_gain(0.0, fade_ms, on_done=self._stop_all)
        else:
            self._stop_fade()
            self._stop_all()

    def pause(self) -> None:
        if not (self._players and self._enabled) or self._paused:
            return
        self._stop_fade()
        self._finish_crossfade()
        self._tick_timer.stop()
        self._players[self._cur].pause()
        self._paused = True

    def resume(self) -> None:
        if not (self._players and self._enabled) or not self._paused:
            return
        self._paused = False
        self._gain = 1.0
        self._apply_volumes()
        self._players[self._cur].play()
        self._tick_timer.start()

    def set_volume(self, volume: float) -> None:
        self._volume = volume
        if self._enabled:
            self._stop_fade()
            self._gain = 1.0
            self._apply_volumes()

    def update_settings(self, settings: dict) -> None:
        self._volume = settings.get("rain_volume", 0.7)
        if self._enabled:
            self._apply_volumes()

    def is_available(self) -> bool:
        return bool(self._players)

    # ------------------------------------------------------------------ #
    #  Gapless loop
    # ------------------------------------------------------------------ #

    def _loop_start(self) -> int:
        return self._loop_window[0] if self._loop_window else 0

    def _loop_end(self) -> int:
        if self._loop_window:
            return self._loop_window[1]
        return self._players[self._cur].duration()

    def _crossfading(self) -> bool:
        return self._xfade.isValid()

    def _tick(self) -> None:
        if not self._enabled or self._paused:
            return
        front = self._players[self._cur]
        if not self._crossfading():
            end = self._loop_end()
            if end > 0 and front.position() >= end - CROSSFADE_MS:
                back = self._players[1 - self._cur]
                back.setPosition(self._loop_start())
                back.play()
                self._xfade.start()
        elif self._xfade.elapsed() >= CROSSFADE_MS:
            self._finish_crossfade()
        self._apply_volumes()

    def _finish_crossfade(self) -> None:
        if not self._crossfading():
            return
        self._players[self._cur].stop()
        self._cur = 1 - self._cur
        self._xfade.invalidate()
        self._apply_volumes()

    def _apply_volumes(self) -> None:
        if not self._outputs:
            return
        base = self._volume * self._gain
        if self._crossfading():
            t = min(1.0, self._xfade.elapsed() / CROSSFADE_MS)
            # equal-power so the summed noise level stays constant
            front_mix, back_mix = math.cos(t * math.pi / 2), math.sin(t * math.pi / 2)
        else:
            front_mix, back_mix = 1.0, 0.0
        self._outputs[self._cur].setVolume(base * front_mix)
        self._outputs[1 - self._cur].setVolume(base * back_mix)

    def _any_playing(self) -> bool:
        return any(p.playbackState() == QMediaPlayer.PlaybackState.PlayingState
                   for p in self._players)

    def _stop_all(self) -> None:
        self._tick_timer.stop()
        self._xfade.invalidate()
        for p in self._players:
            p.stop()
        self._cur = 0
        self._paused = False

    # ------------------------------------------------------------------ #
    #  Master fade
    # ------------------------------------------------------------------ #

    def _fade_gain(self, target: float, ms: int, on_done=None) -> None:
        self._stop_fade()
        anim = QVariantAnimation()
        anim.setStartValue(float(self._gain))
        anim.setEndValue(float(target))
        anim.setDuration(ms)
        anim.valueChanged.connect(self._on_gain)
        if on_done:
            anim.finished.connect(on_done)
        anim.start()
        self._fade_anim = anim

    def _on_gain(self, v) -> None:
        self._gain = float(v)
        self._apply_volumes()

    def _stop_fade(self) -> None:
        if self._fade_anim:
            self._fade_anim.stop()
            self._fade_anim = None

    def _init_player(self) -> None:
        snd_path = _find_sound_file()
        if not snd_path:
            return
        try:
            for _ in range(2):
                out = QAudioOutput()
                out.setVolume(0.0)
                player = QMediaPlayer()
                player.setAudioOutput(out)
                player.setSource(QUrl.fromLocalFile(str(snd_path)))
                self._players.append(player)
                self._outputs.append(out)
            self._loop_window = _LOOP_WINDOWS.get(snd_path.name)
        except Exception:
            self._players = []
            self._outputs = []

