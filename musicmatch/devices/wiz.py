"""WiZ bulbs (Philips WiZ and the many brands built on it): JSON over UDP 38899, no pairing."""

from __future__ import annotations

import json
import socket
from contextlib import contextmanager

from .base import RGB, Device, collect, join_brightness, send_discovery, split_brightness, udp_socket

PORT = 38899
WARM_WHITE = (255, 196, 137)  # stand-in colour while the bulb is in a white/scene mode


def discover(timeout: float) -> list[dict]:
    found = {}
    with udp_socket(timeout, broadcast=True) as s:
        send_discovery(s, json.dumps({"method": "getPilot", "params": {}}).encode(), ("255.255.255.255", PORT))
        for data, ip in collect(s, timeout):
            try:
                result = json.loads(data)["result"]
            except (ValueError, KeyError, TypeError):
                continue
            mac = result.get("mac", "")
            found[ip] = {"type": "wiz", "host": ip, "id": mac, "name": f"WiZ {mac[-4:].upper()}".strip()}
    return list(found.values())


def _request(host: str, method: str, params: dict, tries: int = 3) -> dict:
    payload = json.dumps({"method": method, "params": params}).encode()
    with udp_socket(0.4) as s:
        for _ in range(tries):  # UDP: just ask again if the reply got lost
            s.sendto(payload, (host, PORT))
            try:
                data, _ = s.recvfrom(4096)
                reply = json.loads(data)
                if "error" in reply:
                    raise ConnectionError(f"WiZ {host}: {reply['error']}")
                return reply.get("result", {})
            except (socket.timeout, ConnectionResetError):
                continue
    raise ConnectionError(f"WiZ bulb {host} didn't answer")


class _Session:
    def __init__(self, host: str):
        self.host = host
        self._pilot: dict | None = None

    @property
    def pilot(self) -> dict:
        if self._pilot is None:
            self._pilot = _request(self.host, "getPilot", {})
        return self._pilot

    @property
    def is_on(self) -> bool:
        return bool(self.pilot.get("state"))

    @property
    def rgb(self) -> RGB:
        p = self.pilot
        base = (p["r"], p["g"], p["b"]) if "r" in p and max(p["r"], p["g"], p["b"]) else WARM_WHITE
        return join_brightness(base, p.get("dimming", 100))

    def set_rgb(self, rgb: RGB, transition: float) -> None:
        color, level = split_brightness(rgb)
        if level == 0:
            return self.turn_off()
        r, g, b = color
        _request(self.host, "setPilot", {"r": r, "g": g, "b": b, "dimming": max(10, level)})

    def turn_on(self) -> None:
        _request(self.host, "setPilot", {"state": True})

    def turn_off(self) -> None:
        _request(self.host, "setPilot", {"state": False})


class Wiz(Device):
    kind, brand = "wiz", "WiZ"

    @contextmanager
    def session(self):
        yield _Session(self.host)
