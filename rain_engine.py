"""
rain_engine.py
Pure-logic rain simulation.  No Qt imports — keeps physics separate from rendering.

State machine per drop:
  FALLING  → angled raindrop falls from top toward a random landing point
  BEAD     → drop sits still on the glass (water surface tension)
             Small drops fade and die here.
             Large drops transition to STREAK after a random dwell time.
  STREAK   → blob slides down slowly, leaving a thin wet trail
  DEAD     → remove from list
"""

import random
import math
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import List


class DropState(Enum):
    FALLING = auto()
    BEAD    = auto()
    STREAK  = auto()
    DEAD    = auto()


@dataclass
class Raindrop:
    # ---- spawn parameters (set once) ----
    x: float          # current horizontal position (px)
    y: float          # current vertical position (px)
    target_y: float   # y at which the drop lands on the glass
    size: float       # base size: 0.6–4.5  (scales everything)
    speed: float      # px per frame while FALLING

    # ---- rendering / state ----
    state: DropState = DropState.FALLING
    age: int = 0       # frames spent in current state
    bead_duration: int = 120   # set randomly when transitioning to BEAD

    # ---- positional helpers (set at BEAD transition) ----
    streak_x: float = field(init=False)
    streak_y: float = field(init=False)   # current leading-edge y in STREAK
    impact_y: float = field(init=False)   # y where drop first hit glass (trail top)

    def __post_init__(self):
        self.streak_x = self.x
        self.streak_y = self.target_y
        self.impact_y = self.target_y

    # Angle of falling rain (degrees from vertical, positive = right-leaning)
    FALL_ANGLE_DEG: float = 12.0

    @property
    def fall_dx(self) -> float:
        return self.speed * math.tan(math.radians(self.FALL_ANGLE_DEG))

    # ------------------------------------------------------------------ #
    #  BEAD helpers
    # ------------------------------------------------------------------ #

    @property
    def bead_opacity(self) -> float:
        """Small drops fade out at the end of their bead lifetime."""
        if self.size >= 1.8:
            return 1.0   # large drops won't fade — they'll streak instead
        fade_frames = min(35, self.bead_duration // 3)
        start_fade = max(0, self.bead_duration - fade_frames)
        if self.age < start_fade:
            return 1.0
        return max(0.0, 1.0 - (self.age - start_fade) / fade_frames)

    # ------------------------------------------------------------------ #
    #  STREAK helpers
    # ------------------------------------------------------------------ #
    STREAK_FRAMES = 90

    @property
    def streak_speed(self) -> float:
        return self.speed * 0.28   # much slower than the falling drop

    @property
    def streak_opacity_val(self) -> float:
        return max(0.0, 1.0 - self.age / self.STREAK_FRAMES)


class RainEngine:
    """
    Manages a list of Raindrop objects.
    Call update() every frame; it modifies the list in place.
    The overlay reads `drops` for rendering.
    """

    def __init__(self, screen_w: int, screen_h: int, settings: dict):
        self.screen_w = screen_w
        self.screen_h = screen_h
        self.drops: List[Raindrop] = []
        self._spawn_accum: float = 0.0
        self.apply_settings(settings)

    # ------------------------------------------------------------------ #
    #  Public API
    # ------------------------------------------------------------------ #

    def apply_settings(self, settings: dict) -> None:
        from settings_manager import SPEED_MAP, FREQ_MAP
        self.base_speed = SPEED_MAP.get(settings.get("rain_speed", "medium"), 4.5)
        self.freq       = FREQ_MAP.get(settings.get("rain_frequency", "moderate"), 1.5)

    def resize(self, w: int, h: int) -> None:
        self.screen_w = w
        self.screen_h = h

    def clear(self) -> None:
        self.drops.clear()
        self._spawn_accum = 0.0

    def update(self) -> None:
        """Advance simulation by one frame."""
        self._spawn_drops()
        surviving = []
        for drop in self.drops:
            self._advance(drop)
            if drop.state != DropState.DEAD:
                surviving.append(drop)
        self.drops = surviving

    # ------------------------------------------------------------------ #
    #  Internal helpers
    # ------------------------------------------------------------------ #

    def _spawn_drops(self) -> None:
        self._spawn_accum += self.freq
        while self._spawn_accum >= 1.0:
            self._spawn_accum -= 1.0
            self._create_drop()

    def _create_drop(self) -> None:
        # 60% small drops, 40% large — mimics real windshield distribution
        if random.random() < 0.60:
            size = random.uniform(0.6, 1.8)
        else:
            size = random.uniform(1.8, 4.5)

        speed = self.base_speed * random.uniform(0.7, 1.4)

        start_x = random.uniform(-60, self.screen_w + 60)
        start_y = random.uniform(-100, -5)

        # Landing point: weighted toward upper 55% of screen
        target_y = random.triangular(self.screen_h * 0.08,
                                     self.screen_h * 0.95,
                                     self.screen_h * 0.40)

        self.drops.append(Raindrop(
            x=start_x, y=start_y,
            target_y=target_y,
            size=size, speed=speed,
        ))

    def _advance(self, drop: Raindrop) -> None:
        drop.age += 1

        if drop.state == DropState.FALLING:
            drop.y += drop.speed
            drop.x += drop.fall_dx
            if drop.y >= drop.target_y:
                drop.state = DropState.BEAD
                drop.age = 0
                drop.streak_x = drop.x
                drop.streak_y = drop.target_y
                drop.impact_y = drop.target_y
                drop.bead_duration = random.randint(55, 260)

        elif drop.state == DropState.BEAD:
            if drop.age >= drop.bead_duration:
                if drop.size >= 1.8:
                    # Large drop: start sliding
                    drop.state = DropState.STREAK
                    drop.age = 0
                else:
                    drop.state = DropState.DEAD

        elif drop.state == DropState.STREAK:
            drop.streak_y += drop.streak_speed
            if drop.age >= drop.STREAK_FRAMES or drop.streak_y > self.screen_h + 30:
                drop.state = DropState.DEAD
