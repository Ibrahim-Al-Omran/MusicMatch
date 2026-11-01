"""Govee lights with "LAN Control" switched on in the Govee Home app (UDP, no account).

Lights listen on 4003 and always reply to port 4002, so only one socket can hear them;
a lock serialises everything that needs a reply."""

from __future__ import annotations

import json
import socket
import threading
from contextlib import contextmanager

from .base import RGB, Device, collect, join_brightness, split_brightness, udp_socket

MULTICAST = ("239.255.255.250", 4001)
REPLY_PORT, CONTROL_PORT = 4002, 4003
_reply_lock = threading.Lock()


def _reply_socket(timeout: float) -> socket.socket:
    s = udp_socket(timeout)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    s.bind(("", REPLY_PORT))
    return s


def _msg(cmd: str, data: dict) -> bytes:
    return json.dumps({"msg": {"cmd": cmd, "data": data}}).encode()


def discover(timeout: float) -> list[dict]:
    found = {}
    with _reply_lock:
        try:
            s = _reply_socket(timeout)
        except OSError:
            return []
        with s:
            s.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 2)
            s.sendto(_msg("scan", {"account_topic": "reserve"}), MULTICAST)
            for data, ip in collect(s, timeout):
                try:
                    d = json.loads(data)["msg"]["data"]
                except (ValueError, KeyError, TypeError):
                    continue
                host = d.get("ip") or ip
                found[host] = {"type": "govee", "host": host, "id": d.get("device", ""),
                               "name": f"Govee {d.get('sku', '')}".strip()}
    return list(found.values())


class _Session:
    def __init__(self, host: str):
        self.host = host
        self._status: dict | None = None

    def _send(self, cmd: str, data: dict) -> None:
        with udp_socket(1) as s:
            s.sendto(_msg(cmd, data), (self.host, CONTROL_PORT))

    @property
    def status(self) -> dict:
        if self._status is None:
            with _reply_lock:
                with _reply_socket(0.5) as s:
                    for _ in range(3):
                        s.sendto(_msg("devStatus", {}), (self.host, CONTROL_PORT))
                        for data, ip in collect(s, 0.5):
                            if ip == self.host:
                                self._status = json.loads(data)["msg"]["data"]
                                return self._status
            raise ConnectionError(f"Govee {self.host} didn't answer (is LAN Control on in the Govee app?)")
        return self._status

    @property
    def is_on(self) -> bool:
        return bool(self.status.get("onOff"))

    @property
    def rgb(self) -> RGB:
        c = self.status.get("color") or {}
        base = (c.get("r", 255), c.get("g", 255), c.get("b", 255))
        return join_brightness(base, self.status.get("brightness", 100))

    def set_rgb(self, rgb: RGB, transition: float) -> None:
        color, level = split_brightness(rgb)
        if level == 0:
            return self.turn_off()
        r, g, b = color
        self._send("colorwc", {"color": {"r": r, "g": g, "b": b}, "colorTemInKelvin": 0})
        self._send("brightness", {"value": level})

    def turn_on(self) -> None:
        self._send("turn", {"value": 1})

    def turn_off(self) -> None:
        self._send("turn", {"value": 0})


class Govee(Device):
    kind, brand = "govee", "Govee"

    @contextmanager
    def session(self):
        yield _Session(self.host)
