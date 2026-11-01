"""Light drivers and network discovery across brands.

Everything here talks to lights directly on the home network: no cloud accounts."""

from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor

from . import govee, hue, lifx, magichome, wiz, yeelight
from .base import Device, split_brightness

log = logging.getLogger(__name__)

DRIVERS: dict[str, type[Device]] = {
    "magichome": magichome.MagicHome,
    "wiz": wiz.Wiz,
    "lifx": lifx.Lifx,
    "govee": govee.Govee,
    "yeelight": yeelight.Yeelight,
    "hue": hue.Hue,
}
_SCANNERS = {
    "magichome": magichome.discover,
    "wiz": wiz.discover,
    "lifx": lifx.discover,
    "govee": govee.discover,
    "yeelight": yeelight.discover,
}
BRANDS = [DRIVERS[k].brand for k in DRIVERS]


def normalize(entry) -> dict:
    """Config entry -> dict. A bare string is a Magic Home controller's IP (older configs)."""
    if isinstance(entry, str):
        return {"type": "magichome", "host": entry, "name": "Magic Home", "enabled": True}
    out = dict(entry)
    out.setdefault("enabled", True)
    return out


def identity(entry: dict) -> tuple:
    """Stays the same when a light's IP address changes (DHCP), so re-scans merge cleanly."""
    if entry["type"] == "hue":
        return ("hue", entry.get("bridge"), str(entry.get("id")))
    return (entry["type"], (entry.get("id") or entry["host"]).lower())


def make(entry) -> Device:
    entry = normalize(entry)
    try:
        return DRIVERS[entry["type"]](entry)
    except KeyError:
        raise ValueError(f"Unknown light type {entry['type']!r}") from None


def enabled(entries) -> list[dict]:
    return [e for e in map(normalize, entries) if e.get("enabled", True)]


def describe(entry) -> str:
    e = normalize(entry)
    return f"{e.get('name') or DRIVERS[e['type']].brand} ({e['host']})"


def discover(timeout: float = 3.0, hue_keys: dict[str, str] | None = None) -> tuple[list[dict], list[dict]]:
    """Scan for every supported brand at once.

    Returns (lights found, Hue bridges that need their link button pressed)."""
    lights: list[dict] = []
    with ThreadPoolExecutor(len(_SCANNERS) + 1) as pool:
        jobs = {name: pool.submit(scan, timeout) for name, scan in _SCANNERS.items()}
        hue_job = pool.submit(hue.discover, timeout, hue_keys or {})
        for name, job in jobs.items():
            try:
                lights += job.result()
            except Exception as exc:
                log.debug("%s scan failed: %s", name, exc)
        try:
            hue_lights, unpaired = hue_job.result()
        except Exception as exc:
            log.debug("Hue scan failed: %s", exc)
            hue_lights, unpaired = [], []
    return lights + hue_lights, unpaired


def merge(known: list, found: list[dict]) -> list[dict]:
    """Known lights keep their on/off choice (and pick up new IPs/names); new ones are
    added switched on."""
    out = [normalize(e) for e in known]
    index: dict[tuple, int] = {}
    for i, e in enumerate(out):
        index[identity(e)] = i
        index.setdefault((e["type"], "host", e["host"]), i)  # older entries have no id yet
    for entry in found:
        i = index.get(identity(entry), index.get((entry["type"], "host", entry["host"])))
        if i is not None:
            old = out[i]
            out[i] = {**old, **entry, "enabled": old.get("enabled", True)}
        else:
            i = len(out)
            out.append({**entry, "enabled": True})
        index[identity(entry)] = i
        index[(entry["type"], "host", entry["host"])] = i
    return out


__all__ = ["BRANDS", "DRIVERS", "Device", "describe", "discover", "enabled", "hue", "identity",
           "make", "merge", "normalize", "split_brightness"]
