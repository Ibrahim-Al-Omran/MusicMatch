"""Yeelight bulbs and strips with "LAN Control" switched on in the Yeelight app.
Discovery is SSDP-style multicast on 1982; control is JSON lines over TCP 55443."""

from __future__ import annotations

import json
import socket
from contextlib import contextmanager

from .base import RGB, Device, collect, join_brightness, send_discovery, split_brightness, udp_socket

SEARCH = (b"M-SEARCH * HTTP/1.1\r\nHOST: 239.255.255.250:1982\r\n"
          b"MAN: \"ssdp:discover\"\r\nST: wifi_bulb\r\n")
WARM_WHITE = (255, 196, 137)
PORT = 55443


def _headers(data: bytes) -> dict[str, str]:
    out = {}
    for line in data.decode("utf-8", "replace").split("\r\n")[1:]:
        if ":" in line:
            k, v = line.split(":", 1)
            out[k.strip().lower()] = v.strip()
    return out


def discover(timeout: float) -> list[dict]:
    found = {}
    with udp_socket(timeout) as s:
        s.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 2)
        send_discovery(s, SEARCH, ("239.255.255.250", 1982))
        for data, ip in collect(s, timeout):
            h = _headers(data)
            if "set_rgb" not in h.get("support", ""):
                continue  # white-only bulb
            loc = h.get("location", "")
            host = loc.split("//", 1)[-1].split(":", 1)[0] or ip
            name = h.get("name") or f"Yeelight {h.get('model', '')}".strip()
            found[host] = {"type": "yeelight", "host": host, "id": h.get("id", ""), "name": name}
    return list(found.values())


class _Session:
    def __init__(self, host: str):
        self.sock = socket.create_connection((host, PORT), timeout=2)
        self.file = self.sock.makefile("rb")
        self._id = 0
        self._props: list[str] | None = None

    def call(self, method: str, *params):
        self._id += 1
        self.sock.sendall(json.dumps({"id": self._id, "method": method, "params": list(params)}).encode() + b"\r\n")
        while True:  # skip unsolicited "props" notifications
            line = self.file.readline()
            if not line:
                raise ConnectionError("Yeelight closed the connection")
            reply = json.loads(line)
            if reply.get("id") == self._id:
                if "error" in reply:
                    raise ConnectionError(f"Yeelight: {reply['error'].get('message', reply['error'])}")
                return reply.get("result")

    @property
    def props(self) -> list[str]:
        if self._props is None:
            self._props = self.call("get_prop", "power", "bright", "rgb", "color_mode")
        return self._props

    @property
    def is_on(self) -> bool:
        return self.props[0] == "on"

    @property
    def rgb(self) -> RGB:
        _power, bright, rgb, mode = self.props
        base = WARM_WHITE
        if mode == "1" and rgb:
            n = int(rgb)
            base = (n >> 16 & 255, n >> 8 & 255, n & 255)
        return join_brightness(base, int(bright or 100))

    def set_rgb(self, rgb: RGB, transition: float) -> None:
        color, level = split_brightness(rgb)
        if level == 0:
            return self.turn_off()
        ms = max(30, round(transition * 1000))
        r, g, b = color
        self.call("set_rgb", r << 16 | g << 8 | b, "smooth", ms)
        self.call("set_bright", level, "smooth", ms)

    def turn_on(self) -> None:
        self.call("set_power", "on", "smooth", 300)

    def turn_off(self) -> None:
        self.call("set_power", "off", "smooth", 300)

    def close(self) -> None:
        try:
            self.file.close()
            self.sock.close()
        except OSError:
            pass


class Yeelight(Device):
    kind, brand = "yeelight", "Yeelight"
    native_transition = True

    @contextmanager
    def session(self):
        s = _Session(self.host)
        try:
            yield s
        finally:
            s.close()
