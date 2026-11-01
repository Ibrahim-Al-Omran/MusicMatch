"""What every light driver provides.

A light is stored in config.json as a dict: {"type": "wiz", "host": "192.168.1.20",
"name": "Desk lamp", "enabled": true, ...}. Older configs list bare IP strings; those
are Magic Home controllers."""

from __future__ import annotations

import socket
from contextlib import contextmanager
from typing import Iterator, Protocol

RGB = tuple[int, int, int]


class Session(Protocol):
    """One conversation with a light. Drivers may cache what they read for its duration."""

    @property
    def is_on(self) -> bool: ...

    @property
    def rgb(self) -> RGB:
        """Current colour with brightness baked in (what set_rgb would take to reproduce it)."""

    def set_rgb(self, rgb: RGB, transition: float) -> None:
        """Show `rgb`. `transition` is a fade-time hint in seconds for lights that can fade
        natively; drivers without native fades ignore it."""

    def turn_on(self) -> None: ...

    def turn_off(self) -> None: ...


class Device:
    kind = "?"
    brand = "?"
    # True: set_rgb honours `transition`, so a fade is one command, not a stream.
    native_transition = False

    def __init__(self, entry: dict):
        self.entry = entry
        self.host: str = entry["host"]

    @property
    def label(self) -> str:
        return self.entry.get("name") or self.host

    @contextmanager
    def session(self) -> Iterator[Session]:
        raise NotImplementedError
        yield


def split_brightness(rgb: RGB) -> tuple[RGB, int]:
    """(full-brightness colour, brightness 0-100) for lights that take them separately."""
    top = max(rgb)
    if top == 0:
        return (0, 0, 0), 0
    return tuple(round(c * 255 / top) for c in rgb), max(1, round(top / 255 * 100))


def join_brightness(rgb: RGB, percent: float) -> RGB:
    return tuple(round(c * max(0.0, min(100.0, percent)) / 100) for c in rgb)


def udp_socket(timeout: float, broadcast: bool = False) -> socket.socket:
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
    s.settimeout(timeout)
    if broadcast:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
    return s


def collect(sock: socket.socket, seconds: float) -> Iterator[tuple[bytes, str]]:
    """Yield (data, sender_ip) until `seconds` have passed."""
    import time

    end = time.monotonic() + seconds
    while (left := end - time.monotonic()) > 0:
        sock.settimeout(left)
        try:
            data, (ip, _port) = sock.recvfrom(4096)
        except ConnectionResetError:  # Windows reports an ICMP "port unreachable" this way
            continue
        except OSError:
            return
        yield data, ip
