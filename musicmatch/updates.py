"""Daily, notification-only update checks against the public GitHub release."""

from __future__ import annotations

import json
import re
import sys
import time
from dataclasses import dataclass
from pathlib import Path

import requests

from . import __version__
from .config import DATA_DIR

REPOSITORY = "Ibrahim-Al-Omran/MusicMatch"
LATEST_RELEASE_URL = f"https://api.github.com/repos/{REPOSITORY}/releases/latest"
CACHE_PATH = DATA_DIR / ".musicmatch_updates.json"
CHECK_INTERVAL_SECONDS = 24 * 60 * 60
ASSETS = {"darwin": "MusicMatch-macOS.zip", "win32": "MusicMatch-Windows.zip"}


@dataclass(frozen=True)
class Update:
    version: str
    download_url: str


def _version(value) -> tuple[int, int, int] | None:
    if not isinstance(value, str):
        return None
    match = re.fullmatch(r"v?(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)", value)
    return tuple(int(part) for part in match.groups()) if match else None


def _available_update(release, current_version: str, platform: str) -> Update | None:
    if not isinstance(release, dict) or release.get("draft") or release.get("prerelease"):
        return None
    current = _version(current_version)
    tag = release.get("tag_name")
    latest = _version(tag)
    asset_name = ASSETS.get(platform)
    if current is None or latest is None or latest <= current or asset_name is None:
        return None
    assets = release.get("assets")
    if not isinstance(assets, list):
        return None
    for asset in assets:
        if (isinstance(asset, dict) and asset.get("name") == asset_name
                and asset.get("state") == "uploaded"
                and isinstance(asset.get("size"), int) and asset["size"] > 0):
            return Update(".".join(str(part) for part in latest),
                          f"https://github.com/{REPOSITORY}/releases/download/{tag}/{asset_name}")
    return None


def _read_cache(path: Path) -> dict:
    try:
        cached = json.loads(path.read_text(encoding="utf-8"))
        return cached if isinstance(cached, dict) else {}
    except (OSError, ValueError):
        return {}


def _write_cache(path: Path, cached: dict) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    try:
        temporary.write_text(json.dumps(cached) + "\n", encoding="utf-8")
        temporary.replace(path)
    except OSError:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass


def check_for_update(current_version: str = __version__, platform: str | None = None,
                     cache_path: Path = CACHE_PATH, now: float | None = None) -> Update | None:
    platform = platform or sys.platform
    if platform not in ASSETS or _version(current_version) is None:
        return None
    now = time.time() if now is None else now
    cached = _read_cache(cache_path)
    release = cached.get("release")
    checked_at = cached.get("checked_at")
    if isinstance(checked_at, (int, float)) and 0 <= now - checked_at < CHECK_INTERVAL_SECONDS:
        return _available_update(release, current_version, platform)
    try:
        response = requests.get(LATEST_RELEASE_URL, timeout=(3, 5), headers={
            "Accept": "application/vnd.github+json", "User-Agent": f"MusicMatch/{current_version}",
        })
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict) or not isinstance(payload.get("assets"), list):
            raise ValueError("Invalid release metadata")
        release = {key: payload.get(key) for key in ("tag_name", "draft", "prerelease")}
        release["assets"] = [
            {key: asset.get(key) for key in ("name", "state", "size")}
            for asset in payload["assets"]
            if isinstance(asset, dict) and asset.get("name") in ASSETS.values()
        ]
    except (requests.RequestException, ValueError):
        pass
    _write_cache(cache_path, {"checked_at": now, "release": release})
    return _available_update(release, current_version, platform)
