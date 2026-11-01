"""Philips Hue through the bridge's local REST API. Pairing = press the bridge's button
once; after that each colour bulb behind the bridge is its own light entry."""

from __future__ import annotations

import re
import socket
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager

import requests

from .base import RGB, Device, collect, udp_socket

SSDP = (b"M-SEARCH * HTTP/1.1\r\nHOST: 239.255.255.250:1900\r\nMAN: \"ssdp:discover\"\r\n"
        b"MX: 2\r\nST: urn:schemas-upnp-org:device:basic:1\r\n\r\n")
CLOUD_DISCOVERY = "https://discovery.meethue.com/"
DEVICE_TYPE = "musicmatch#" + (socket.gethostname()[:19] or "computer")
_http = requests.Session()


def find_bridges(timeout: float) -> dict[str, str]:
    """{bridge id: ip} via SSDP on the LAN, plus Philips' discovery service as a backup."""
    bridges: dict[str, str] = {}

    def cloud():
        try:
            for b in _http.get(CLOUD_DISCOVERY, timeout=timeout).json():
                bridges.setdefault(b["id"].lower(), b["internalipaddress"])
        except Exception:
            pass

    with ThreadPoolExecutor(1) as pool:
        job = pool.submit(cloud)
        with udp_socket(timeout) as s:
            s.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 2)
            s.sendto(SSDP, ("239.255.255.250", 1900))
            for data, ip in collect(s, timeout):
                m = re.search(rb"hue-bridgeid:\s*([0-9A-Fa-f]+)", data)
                if m:
                    bridges[m.group(1).decode().lower()] = ip
        job.result()
    return bridges


def pair(host: str) -> str | None:
    """App key if the bridge's link button was pressed in the last 30 s, else None."""
    reply = _http.post(f"http://{host}/api", json={"devicetype": DEVICE_TYPE}, timeout=3).json()
    for item in reply:
        if "success" in item:
            return item["success"]["username"]
    return None


def lights_on_bridge(bridge_id: str, host: str, key: str) -> list[dict]:
    lights = _http.get(f"http://{host}/api/{key}/lights", timeout=4).json()
    if isinstance(lights, list):  # [{"error": ...}]: key no longer valid
        raise PermissionError(f"Hue bridge {host} no longer accepts this app; pair it again")
    return [
        {"type": "hue", "host": host, "id": light_id, "bridge": bridge_id, "key": key,
         "name": info.get("name") or f"Hue {light_id}"}
        for light_id, info in lights.items()
        if "xy" in info.get("state", {})  # colour-capable only
    ]


def discover(timeout: float, keys: dict[str, str]) -> tuple[list[dict], list[dict]]:
    """(lights on bridges we're paired with, bridges that still need their button pressed)"""
    found, unpaired = [], []
    for bridge_id, host in find_bridges(timeout).items():
        key = keys.get(bridge_id)
        if key:
            try:
                found += lights_on_bridge(bridge_id, host, key)
                continue
            except PermissionError:
                pass
            except Exception:
                continue
        unpaired.append({"bridge": bridge_id, "host": host})
    return found, unpaired


# ---------- colour conversion (Philips' wide-gamut RGB <-> CIE xy) ----------

def _lin(c: float) -> float:
    return ((c + 0.055) / 1.055) ** 2.4 if c > 0.04045 else c / 12.92


def _gamma(c: float) -> float:
    return 12.92 * c if c <= 0.0031308 else 1.055 * c ** (1 / 2.4) - 0.055


def rgb_to_xy_bri(rgb: RGB) -> tuple[list[float], int]:
    top = max(rgb)
    if top == 0:
        return [0.3227, 0.329], 1
    r, g, b = (_lin(c / top) for c in rgb)
    x = r * 0.664511 + g * 0.154324 + b * 0.162028
    y = r * 0.283881 + g * 0.668433 + b * 0.047685
    z = r * 0.000088 + g * 0.072310 + b * 0.986039
    total = x + y + z
    return [round(x / total, 4), round(y / total, 4)], max(1, round(top / 255 * 254))


def xy_bri_to_rgb(xy: list[float], bri: int) -> RGB:
    x, y = xy
    if y == 0:
        return (0, 0, 0)
    Y = 1.0
    X, Z = Y / y * x, Y / y * (1 - x - y)
    r = X * 1.656492 - Y * 0.354851 - Z * 0.255038
    g = -X * 0.707196 + Y * 1.655397 + Z * 0.036152
    b = X * 0.051713 - Y * 0.121364 + Z * 1.011530
    rgb = [_gamma(max(0.0, c)) for c in (r, g, b)]
    top = max(rgb) or 1
    scale = bri / 254 * 255
    return tuple(round(c / top * scale) for c in rgb)


class _Session:
    def __init__(self, base: str):
        self.base = base
        self._state: dict | None = None

    @property
    def state(self) -> dict:
        if self._state is None:
            info = _http.get(self.base, timeout=3).json()
            if isinstance(info, list):
                raise ConnectionError(f"Hue: {info[0].get('error', {}).get('description', info)}")
            self._state = info["state"]
        return self._state

    def _put(self, body: dict) -> None:
        reply = _http.put(self.base + "/state", json=body, timeout=3).json()
        errors = [i["error"]["description"] for i in reply if "error" in i]
        if errors:
            raise ConnectionError(f"Hue: {errors[0]}")

    @property
    def is_on(self) -> bool:
        return bool(self.state.get("on")) and self.state.get("reachable", True)

    @property
    def rgb(self) -> RGB:
        return xy_bri_to_rgb(self.state.get("xy", [0.3227, 0.329]), self.state.get("bri", 254))

    def set_rgb(self, rgb: RGB, transition: float) -> None:
        xy, bri = rgb_to_xy_bri(rgb)
        self._put({"xy": xy, "bri": bri, "transitiontime": round(transition * 10)})

    def turn_on(self) -> None:
        self._put({"on": True})

    def turn_off(self) -> None:
        self._put({"on": False})


class Hue(Device):
    kind, brand = "hue", "Philips Hue"
    native_transition = True

    @contextmanager
    def session(self):
        yield _Session(f"http://{self.host}/api/{self.entry['key']}/lights/{self.entry['id']}")
