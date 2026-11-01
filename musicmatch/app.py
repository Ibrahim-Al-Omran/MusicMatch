from __future__ import annotations

import json
import logging
import sys
import threading
import time
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from typing import IO

from .color import (
    fetch_image,
    led_color_for_image,
    led_color_from_spotify,
    scale,
    spotify_background_color,
)
from . import devices
from .config import COLOR_CACHE_PATH, CONFIG_PATH, DATA_DIR, RGB, Config, ConfigError, load_config
from .lights import LightGroup
from .spotify_client import NowPlaying, SpotifyWatcher

log = logging.getLogger(__name__)

MAX_BACKOFF_SECONDS = 60
# Poll just after a track should end so the color changes with the music, not up to
# poll_interval later.
TRACK_END_SLACK_SECONDS = 0.4
# Changing these means previously computed album colors are wrong.
COLOR_SETTINGS = ("color_mode", "min_saturation", "neutral_color")
PREFETCH_COUNT = 12

TrackCallback = Callable[[NowPlaying | None, RGB | None], None]

# Held (OS file lock) while syncing; the OS releases it if the process dies.
INSTANCE_LOCK_PATH = DATA_DIR / ".musicmatch.lock"


class AlreadyRunning(Exception):
    pass


def _acquire_instance_lock(path: Path | None = None) -> IO:
    fh = open(path or INSTANCE_LOCK_PATH, "a+")
    try:
        if sys.platform == "win32":
            import msvcrt

            fh.seek(0)
            msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        fh.close()
        raise AlreadyRunning(
            "MusicMatch is already syncing in another window or terminal. "
            "Stop that one first, otherwise both will fight over the lights."
        ) from None
    return fh


class MusicMatch:
    def __init__(self, cfg: Config, on_track: TrackCallback | None = None):
        self.cfg = replace(cfg)
        self.on_track = on_track
        self.spotify = SpotifyWatcher(cfg)
        self.lights = LightGroup(cfg.lights, cfg.fade_seconds, cfg.auto_turn_on)
        # Full-brightness colors (+ log description); brightness is applied when sending
        # so it can change live. Shared on disk so a preload (or a previous run) is
        # ready the moment a song starts.
        self._color_cache: dict[str, tuple[RGB, str]] = {}
        self._cache_mtime = self._load_color_cache()
        # Bumped when color settings change so in-flight prefetches don't store stale colors.
        self._cache_generation = 0
        self._prefetching = False
        self._paused = False
        self._current_key: str | None = None
        self._last_active = time.monotonic()
        self._idle_applied = False
        self._config_mtime = self._read_mtime()
        self._settings_lock = threading.Lock()

    def run(self, stop: threading.Event | None = None, use_lock: bool = True) -> None:
        """use_lock=False when the caller (the background service) already holds it."""
        # Event.wait isn't interruptible by Ctrl+C on Windows, so the CLI uses time.sleep.
        wait = stop.wait if stop else time.sleep
        stop = stop or threading.Event()
        lock = _acquire_instance_lock() if use_lock else None
        try:
            log.info("Logged in to Spotify as %s", self.spotify.whoami())
            names = [devices.describe(e) for e in self.cfg.enabled_lights()]
            log.info("Controlling %d light(s): %s", len(names), ", ".join(names))
            log.info("Brightness %d%%. Waiting for music...", self.cfg.brightness)

            backoff = self.cfg.poll_interval
            while not stop.is_set():
                try:
                    sleep_for = self.tick()
                    backoff = self.cfg.poll_interval
                except Exception as exc:
                    log.warning("Spotify/network error: %s (retrying in %ds)", exc, backoff)
                    sleep_for = backoff
                    backoff = min(backoff * 2, MAX_BACKOFF_SECONDS)
                wait(sleep_for)
        finally:
            if lock:
                lock.close()

    def update_settings(self, fresh: Config) -> None:
        """Apply new settings to a running engine. Brightness changes don't re-send the
        current color; callers re-dim the lights themselves (LightGroup.set_brightness)."""
        with self._settings_lock:
            old = self.cfg
            self.cfg = replace(fresh)
            if fresh.lights != old.lights:
                self.lights = LightGroup(fresh.lights, fresh.fade_seconds, fresh.auto_turn_on)
                self._current_key = None
            self.lights.fade_seconds = fresh.fade_seconds
            self.lights.auto_turn_on = fresh.auto_turn_on
            if any(getattr(fresh, k) != getattr(old, k) for k in COLOR_SETTINGS):
                self._cache_generation += 1
                self._color_cache.clear()
                self._save_color_cache()
                self._current_key = None

    def tick(self) -> float:
        self._reload_config()
        self._reload_color_cache()
        if not self.cfg.sync_enabled:
            if not self._paused:
                self._paused = True
                self._current_key = None
                log.info("Syncing paused")
                if self.on_track:
                    self.on_track(None, None)
            return self.cfg.poll_interval
        if self._paused:
            self._paused = False
            log.info("Syncing resumed")
            if self.on_track:
                self.on_track(None, None)
        now = time.monotonic()
        playing = self.spotify.now_playing()

        if playing and playing.is_playing:
            self._last_active = now
            self._idle_applied = False
            self._on_playing(playing)
            if playing.remaining_ms is not None:
                until_end = playing.remaining_ms / 1000 + TRACK_END_SLACK_SECONDS
                return max(0.5, min(self.cfg.poll_interval, until_end))
            return self.cfg.poll_interval

        if not self._idle_applied and now - self._last_active >= self.cfg.idle_after:
            self._on_idle()
        return self.cfg.poll_interval

    def _read_mtime(self) -> float | None:
        try:
            return CONFIG_PATH.stat().st_mtime
        except OSError:
            return None

    def _reload_config(self) -> None:
        """Pick up changes from `musicmatch brightness`, the desktop app, or a hand edit."""
        mtime = self._read_mtime()
        if mtime == self._config_mtime:
            return
        self._config_mtime = mtime
        try:
            fresh = load_config()
            fresh.validate(need_spotify=False)
        except (ConfigError, ValueError) as exc:
            log.warning("Ignoring config.json change: %s", exc)
            return
        if fresh.brightness != self.cfg.brightness:
            log.info("Brightness %d%% -> %d%%", self.cfg.brightness, fresh.brightness)
        self.update_settings(fresh)

    def _remember(self, key: str, color: RGB, desc: str) -> None:
        self._color_cache[key] = (color, desc)
        self._save_color_cache()

    def _load_color_cache(self) -> float | None:
        payload, mtime = read_color_cache(self.cfg)
        self._color_cache.update(payload)
        if payload:
            log.info("Loaded %d prepared color(s)", len(payload))
        return mtime

    def _reload_color_cache(self) -> None:
        try:
            mtime = COLOR_CACHE_PATH.stat().st_mtime
        except OSError:
            mtime = None
        if mtime == self._cache_mtime:
            return
        self._cache_mtime = mtime
        payload, _ = read_color_cache(self.cfg)
        if payload:
            self._color_cache.update(payload)

    def _save_color_cache(self) -> None:
        write_color_cache(self.cfg, self._color_cache)
        try:
            self._cache_mtime = COLOR_CACHE_PATH.stat().st_mtime
        except OSError:
            pass

    def _on_playing(self, np: NowPlaying) -> None:
        key = cache_key(np)
        if key == self._current_key:
            return
        self._current_key = key

        cached = self._color_cache.get(key)
        if cached:
            color, desc = cached[0], "ready in advance"
        else:
            color, desc = compute_color(np, self.cfg)
            self._remember(key, color, desc)

        sent = scale(color, self.cfg.brightness)
        self.lights.set_color(sent)
        log.info(
            "%s - %s  [%s]  on %s  -> RGB%s (%s)",
            np.artist, np.title, np.album, np.device, sent, desc,
        )
        if self.on_track:
            self.on_track(np, color)
        self._prefetch_upcoming()

    def _prefetch_upcoming(self) -> None:
        """Work out colors for the next songs in the queue in the background, so the
        lights can change the moment a song starts instead of after a color lookup."""
        if self._prefetching:
            return
        self._prefetching = True
        cfg, generation = self.cfg, self._cache_generation

        def work() -> None:
            changed = False
            try:
                for item in self.spotify.upcoming(PREFETCH_COUNT):
                    key = cache_key(item)
                    if key in self._color_cache:
                        continue
                    result = compute_color(item, cfg)
                    if generation != self._cache_generation:
                        return
                    self._color_cache[key] = result
                    changed = True
                if changed:
                    self._save_color_cache()
            except Exception as exc:
                log.debug("Couldn't prepare upcoming colors: %s", exc)
            finally:
                self._prefetching = False

        threading.Thread(target=work, daemon=True).start()

    def _on_idle(self) -> None:
        self._idle_applied = True
        self._current_key = None
        if self.on_track:
            self.on_track(None, None)
        action = self.cfg.idle_action
        if action == "keep":
            return
        log.info("Nothing playing for %ss, idle action: %s", int(self.cfg.idle_after), action)
        if action == "off":
            self.lights.turn_off()
        elif action == "color":
            self.lights.set_color(scale(self.cfg.idle_color, self.cfg.brightness))


def cache_key(np: NowPlaying) -> str:
    return np.image_url or f"noart:{np.item_id}"


def compute_color(np: NowPlaying, cfg: Config) -> tuple[RGB, str]:
    """Full-brightness LED color for a track, plus a short log description."""
    if cfg.color_mode == "spotify":
        spotify_rgb = spotify_background_color(np.kind, np.item_id)
        if spotify_rgb:
            color = led_color_from_spotify(spotify_rgb, cfg.min_saturation, 100, cfg.neutral_color)
            return color, "Spotify background #{:02x}{:02x}{:02x}".format(*spotify_rgb)
        log.info("Spotify color unavailable for %s, analysing the cover instead", np.title)
    if np.image_url:
        color, swatch = led_color_for_image(
            fetch_image(np.image_url), cfg.color_mode, cfg.min_saturation, 100, cfg.neutral_color,
        )
        return color, f"cover {swatch.hex}" if swatch else "no color in cover"
    return cfg.neutral_color, "no album art"


def _settings_key(cfg: Config) -> dict:
    return {
        "color_mode": cfg.color_mode,
        "min_saturation": cfg.min_saturation,
        "neutral_color": list(cfg.neutral_color),
    }


def read_color_cache(cfg: Config) -> tuple[dict[str, tuple[RGB, str]], float | None]:
    try:
        raw = json.loads(COLOR_CACHE_PATH.read_text(encoding="utf-8"))
        mtime = COLOR_CACHE_PATH.stat().st_mtime
    except (OSError, ValueError):
        return {}, None
    if raw.get("settings") != _settings_key(cfg):
        return {}, mtime
    out = {}
    for key, entry in (raw.get("colors") or {}).items():
        try:
            out[key] = (tuple(int(c) for c in entry["rgb"]), str(entry.get("desc", "")))
        except (KeyError, TypeError, ValueError):
            continue
    return out, mtime


def write_color_cache(cfg: Config, colors: dict[str, tuple[RGB, str]]) -> None:
    payload = {
        "settings": _settings_key(cfg),
        "colors": {key: {"rgb": list(color), "desc": desc} for key, (color, desc) in colors.items()},
    }
    tmp = COLOR_CACHE_PATH.with_name(COLOR_CACHE_PATH.name + ".tmp")
    tmp.write_text(json.dumps(payload), encoding="utf-8")
    tmp.replace(COLOR_CACHE_PATH)


def warm_colors(cfg: Config, items: list[NowPlaying]) -> list[tuple[NowPlaying, RGB, str, bool]]:
    """Compute (or reuse) LED colors for `items` and persist them for the running service."""
    cache, _ = read_color_cache(cfg)
    results = []
    for item in items:
        key = cache_key(item)
        if key in cache:
            results.append((item, *cache[key], True))
            continue
        color, desc = compute_color(item, cfg)
        cache[key] = (color, desc)
        results.append((item, color, desc, False))
    write_color_cache(cfg, cache)
    return results
