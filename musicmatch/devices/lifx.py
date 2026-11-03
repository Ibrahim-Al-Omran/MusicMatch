"""LIFX bulbs and strips: the documented LAN protocol, binary over UDP 56700, no pairing."""

from __future__ import annotations

import colorsys
import os
import socket
import struct
from contextlib import contextmanager

from .base import RGB, Device, collect, send_discovery, udp_socket

PORT = 56700
GET_SERVICE, STATE_SERVICE = 2, 3
LIGHT_GET, LIGHT_SET_COLOR, LIGHT_STATE = 101, 102, 107
LIGHT_SET_POWER = 117
KELVIN = 3500
_SOURCE = os.getpid() & 0xFFFFFFFF or 1


def _packet(msg_type: int, payload: bytes = b"", target: bytes = b"\0" * 8, tagged: bool = False,
            seq: int = 0) -> bytes:
    flags = 1024 | (1 << 12) | ((1 << 13) if tagged else 0)  # protocol 1024, addressable
    header = struct.pack("<HHI", 36 + len(payload), flags, _SOURCE)
    header += target + b"\0" * 6 + struct.pack("<BB", 0x01, seq)  # res_required
    header += struct.pack("<QHH", 0, msg_type, 0)
    return header + payload


def _parse(data: bytes) -> tuple[int, bytes, bytes] | None:
    """(message type, target mac, payload)"""
    if len(data) < 36:
        return None
    return struct.unpack_from("<H", data, 32)[0], data[8:16], data[36:]


def _parse_state(payload: bytes) -> tuple[RGB, bool, str]:
    h, s, b, _k = struct.unpack_from("<HHHH", payload, 0)
    power = struct.unpack_from("<H", payload, 10)[0]
    label = payload[12:44].split(b"\0", 1)[0].decode("utf-8", "replace")
    r, g, bl = colorsys.hsv_to_rgb(h / 65535, s / 65535, b / 65535)
    return (round(r * 255), round(g * 255), round(bl * 255)), power > 0, label


def discover(timeout: float) -> list[dict]:
    found: dict[str, dict] = {}
    with udp_socket(timeout, broadcast=True) as s:
        send_discovery(s, _packet(GET_SERVICE, tagged=True), ("255.255.255.255", PORT))
        for data, ip in collect(s, min(timeout, 1.5)):
            msg = _parse(data)
            if msg and msg[0] == STATE_SERVICE:
                found[ip] = {"type": "lifx", "host": ip, "id": msg[1][:6].hex(), "name": "LIFX"}
        for entry in found.values():  # ask each one for its name
            try:
                _rgb, _on, label = _Session(entry["host"], bytes.fromhex(entry["id"]) + b"\0\0").state
                entry["name"] = label or entry["name"]
            except Exception:
                pass
    return list(found.values())


class _Session:
    def __init__(self, host: str, target: bytes):
        self.host, self.target = host, target
        self._state: tuple[RGB, bool, str] | None = None
        self._seq = 0

    def _ask(self, msg_type: int, payload: bytes = b"", want: int | None = None) -> bytes:
        with udp_socket(0.4) as s:
            for _ in range(3):
                self._seq = (self._seq + 1) % 256
                s.sendto(_packet(msg_type, payload, self.target, seq=self._seq), (self.host, PORT))
                try:
                    while True:
                        data, _ = s.recvfrom(1024)
                        msg = _parse(data)
                        if msg and (want is None or msg[0] == want):
                            return msg[2]
                except (socket.timeout, ConnectionResetError):
                    continue
        raise ConnectionError(f"LIFX {self.host} didn't answer")

    @property
    def state(self) -> tuple[RGB, bool, str]:
        if self._state is None:
            self._state = _parse_state(self._ask(LIGHT_GET, want=LIGHT_STATE))
        return self._state

    @property
    def is_on(self) -> bool:
        return self.state[1]

    @property
    def rgb(self) -> RGB:
        return self.state[0]

    def set_rgb(self, rgb: RGB, transition: float) -> None:
        h, s, v = colorsys.rgb_to_hsv(*(c / 255 for c in rgb))
        payload = struct.pack("<BHHHHI", 0, round(h * 65535), round(s * 65535), round(v * 65535),
                              KELVIN, round(max(0.0, transition) * 1000))
        self._ask(LIGHT_SET_COLOR, payload)

    def turn_on(self) -> None:
        self._ask(LIGHT_SET_POWER, struct.pack("<HI", 65535, 0))

    def turn_off(self) -> None:
        self._ask(LIGHT_SET_POWER, struct.pack("<HI", 0, 0))


class Lifx(Device):
    kind, brand = "lifx", "LIFX"
    native_transition = True

    @contextmanager
    def session(self):
        mac = bytes.fromhex(self.entry.get("id") or "000000000000")
        yield _Session(self.host, mac + b"\0\0")
