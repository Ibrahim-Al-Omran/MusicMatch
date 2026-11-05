from __future__ import annotations

import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor

from . import devices
from .color import rebrighten
from .config import RGB

log = logging.getLogger(__name__)

FADE_STEP_SECONDS = 0.08
# Lights that fade natively get this when no extra fade is set, so changes glide
# the way Magic Home controllers do on their own.
NATIVE_TRANSITION = 0.4


class Light:
    """One light of any brand; one operation at a time, retried once."""

    def __init__(self, entry):
        self.device = devices.make(entry)
        self.label = devices.describe(entry)
        self._lock = threading.Lock()

    def run(self, fn):
        """Call fn(session) on a fresh session with the light."""
        with self._lock:
            for attempt in (1, 2):
                try:
                    with self.device.session() as session:
                        return fn(session)
                except Exception as exc:
                    if attempt == 2:
                        raise
                    log.debug("light %s: %s, retrying", self.label, exc)


class LightGroup:
    def __init__(self, entries: list, fade_seconds: float, auto_turn_on: bool):
        self.lights = [Light(e) for e in devices.enabled(entries)]
        self.fade_seconds = fade_seconds
        self.auto_turn_on = auto_turn_on
        self._pool = ThreadPoolExecutor(max_workers=max(1, min(16, len(self.lights))))

    def set_color(self, rgb: RGB, *, preserve_brightness: bool = False) -> None:
        self._each(lambda light: light.run(
            lambda session: self._apply(light, session, rgb, preserve_brightness)
        ))

    def turn_off(self) -> None:
        self._each(lambda light: light.run(lambda s: s.turn_off()))

    def set_brightness(self, brightness: int) -> None:
        """Re-dim whatever colour each light is showing right now. Off lights stay off."""

        def one(light: Light, s) -> None:
            target = rebrighten(s.rgb, brightness)
            if s.is_on and target:
                self._fade(light, s, target)

        self._each(lambda light: light.run(lambda s: one(light, s)))

    def identify(self) -> None:
        """Flash red, green, blue, then put each light back how it was."""

        def one(s) -> None:
            was_on, original = s.is_on, s.rgb
            if not was_on:
                s.turn_on()
            for rgb in ((255, 0, 0), (0, 255, 0), (0, 0, 255)):
                s.set_rgb(rgb, 0)
                time.sleep(0.8)
            s.set_rgb(original, 0)
            if not was_on:
                s.turn_off()

        self._each(lambda light: light.run(one))

    def _each(self, fn) -> None:
        futures = {self._pool.submit(fn, light): light for light in self.lights}
        for fut, light in futures.items():
            try:
                fut.result()
            except Exception as exc:
                log.warning("light %s unreachable: %s", light.label, exc)

    def _apply(self, light: Light, s, target: RGB, preserve_brightness: bool = False) -> None:
        if s.is_on:
            if preserve_brightness and max(target):
                peak = max(s.rgb)
                if peak:
                    target = tuple(round(channel * peak / max(target)) for channel in target)
            self._fade(light, s, target)
        elif self.auto_turn_on:
            s.set_rgb(target, 0)
            s.turn_on()
        else:
            log.debug("light %s is off, leaving it alone", light.label)

    def _fade(self, light: Light, s, target: RGB) -> None:
        if light.device.native_transition:
            s.set_rgb(target, self.fade_seconds or NATIVE_TRANSITION)
            return
        steps = round(self.fade_seconds / FADE_STEP_SECONDS)
        if steps == 0:
            s.set_rgb(target, 0)
            return
        current = s.rgb
        for i in range(1, steps + 1):
            t = i / steps
            s.set_rgb(tuple(round(c + (n - c) * t) for c, n in zip(current, target)), 0)
            time.sleep(FADE_STEP_SECONDS)
