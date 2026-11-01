from __future__ import annotations

from dataclasses import dataclass

import spotipy
from spotipy.oauth2 import SpotifyOAuth

from .config import TOKEN_CACHE_PATH, Config

SCOPE = "user-read-currently-playing user-read-playback-state"


@dataclass(frozen=True)
class NowPlaying:
    kind: str
    item_id: str
    title: str
    artist: str
    album: str
    image_url: str | None
    device: str
    is_playing: bool
    remaining_ms: int | None


def _pick_image(images: list[dict]) -> str | None:
    """Smallest cover that is still >= 200px; color extraction doesn't need 640px."""
    if not images:
        return None
    by_size = sorted(images, key=lambda i: i.get("width") or 0)
    for img in by_size:
        if (img.get("width") or 0) >= 200:
            return img["url"]
    return by_size[-1]["url"]


def _make_auth(cfg: Config, open_browser: bool = True) -> SpotifyOAuth:
    return SpotifyOAuth(
        client_id=cfg.spotify_client_id,
        client_secret=cfg.spotify_client_secret,
        redirect_uri=cfg.spotify_redirect_uri,
        scope=SCOPE,
        cache_path=str(TOKEN_CACHE_PATH),
        open_browser=open_browser,
    )


def cached_login(cfg: Config) -> str | None:
    """Display name if a saved login is still valid. Never opens a browser."""
    auth = _make_auth(cfg, open_browser=False)
    token = auth.validate_token(auth.cache_handler.get_cached_token())
    if not token:
        return None
    me = spotipy.Spotify(auth=token["access_token"], requests_timeout=10).current_user()
    return me.get("display_name") or me["id"]


class SpotifyWatcher:
    """Reads playback state for the whole Spotify account, so it sees playback on
    any device: Echo/Alexa, PlayStation, Mac, phone, TV, web player, etc."""

    def __init__(self, cfg: Config, open_browser: bool = True):
        self.sp = spotipy.Spotify(
            auth_manager=_make_auth(cfg, open_browser), requests_timeout=10, retries=3
        )

    def whoami(self) -> str:
        me = self.sp.current_user()
        return me.get("display_name") or me["id"]

    def now_playing(self) -> NowPlaying | None:
        pb = self.sp.current_playback(additional_types="track,episode")
        if not pb or not pb.get("item"):
            return None
        item = pb["item"]

        remaining = None
        if pb.get("progress_ms") is not None and item.get("duration_ms"):
            remaining = max(0, item["duration_ms"] - pb["progress_ms"])

        return _parse_item(
            item,
            device=(pb.get("device") or {}).get("name", "unknown device"),
            is_playing=bool(pb.get("is_playing")),
            remaining_ms=remaining,
        )

    def upcoming(self, limit: int = 3) -> list[NowPlaying]:
        """Next items in the play queue (autoplay/radio included), soonest first."""
        queue = self.sp.queue() or {}
        return [
            _parse_item(item, device="", is_playing=False, remaining_ms=None)
            for item in (queue.get("queue") or [])[:limit]
            if item
        ]


def _parse_item(item: dict, device: str, is_playing: bool, remaining_ms: int | None) -> NowPlaying:
    if item.get("type") == "episode":
        show = item.get("show") or {}
        images = item.get("images") or show.get("images") or []
        artist = album = show.get("name", "")
    else:
        album_obj = item.get("album") or {}
        images = album_obj.get("images") or []
        artist = ", ".join(a["name"] for a in item.get("artists", []))
        album = album_obj.get("name", "")

    return NowPlaying(
        kind=item.get("type", "track"),
        item_id=item.get("id") or item.get("uri") or item.get("name", ""),
        title=item.get("name", ""),
        artist=artist,
        album=album,
        image_url=_pick_image(images),
        device=device,
        is_playing=is_playing,
        remaining_ms=remaining_ms,
    )
