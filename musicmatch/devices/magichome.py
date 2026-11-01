"""Magic Home / Zengge / flux_led Wi-Fi controllers (TCP 5577, discovery UDP 48899)."""

from __future__ import annotations

import logging
from contextlib import contextmanager

from flux_led import BulbScanner, WifiLedBulb

from .base import RGB, Device

log = logging.getLogger(__name__)


def discover(timeout: float) -> list[dict]:
    return [
        {"type": "magichome", "host": d["ipaddr"], "id": d.get("id", ""), "name": "Magic Home"}
        for d in BulbScanner().scan(timeout=max(1, round(timeout)))
    ]


class _Session:
    def __init__(self, bulb: WifiLedBulb):
        self.bulb = bulb

    @property
    def is_on(self) -> bool:
        return bool(self.bulb.is_on)

    @property
    def rgb(self) -> RGB:
        return tuple(self.bulb.getRgb())

    def set_rgb(self, rgb: RGB, transition: float) -> None:
        self.bulb.setRgb(*rgb, persist=False)  # the controller fades by itself

    def turn_on(self) -> None:
        self.bulb.turnOn()

    def turn_off(self) -> None:
        self.bulb.turnOff()


class MagicHome(Device):
    """Every session opens a fresh connection: when a long-lived flux_led connection goes
    stale, flux_led returns its cached state instead of raising, so on/off and the
    starting colour of a fade can't be trusted."""

    kind, brand = "magichome", "Magic Home"

    @contextmanager
    def session(self):
        bulb = WifiLedBulb(self.host, timeout=3)
        try:
            if not bulb.available:
                raise ConnectionError("no valid state reply from controller")
            yield _Session(bulb)
        finally:
            try:
                bulb.close()
            except Exception:
                pass
