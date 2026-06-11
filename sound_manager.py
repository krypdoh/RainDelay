"""
sound_manager.py
Ambient audio for RainDelay using Qt6 Multimedia (QMediaPlayer).

• Loops a single rain+thunder MP3 continuously when overlay is active
• Separate volume control (rain_volume setting controls overall level)
• Graceful no-op if sound file is missing or Qt Multimedia unavailable
"""

import sys
from pathlib import Path

from PyQt6.QtCore import QUrl
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
        self._enabled = False
        self._player: QMediaPlayer | None = None
        self._audio_output: QAudioOutput | None = None
        self._init_player()

    # ------------------------------------------------------------------ #
    #  Public API
    # ------------------------------------------------------------------ #

    def play(self) -> None:
        """Start looping the rain+thunder audio."""
        if not self._player:
            return
        self._audio_output.setVolume(self._volume)
        self._player.setPosition(0)
        self._player.play()
        self._enabled = True

    def stop(self) -> None:
        """Stop playback."""
        if not self._player:
            return
        self._player.stop()
        self._enabled = False

    def update_settings(self, settings: dict) -> None:
        self._volume = settings.get("rain_volume", 0.7)
        if self._enabled and self._audio_output:
            self._audio_output.setVolume(self._volume)

    def is_available(self) -> bool:
        return self._player is not None

    # ------------------------------------------------------------------ #
    #  Internal
    # ------------------------------------------------------------------ #

    def _init_player(self) -> None:
        snd_path = _find_sound_file()
        if not snd_path:
            return
        try:
            self._audio_output = QAudioOutput()
            self._audio_output.setVolume(self._volume)
            self._player = QMediaPlayer()
            self._player.setAudioOutput(self._audio_output)
            self._player.setSource(QUrl.fromLocalFile(str(snd_path)))
            self._player.setLoops(QMediaPlayer.Loops.Infinite)
        except Exception:
            self._player = None
            self._audio_output = None
