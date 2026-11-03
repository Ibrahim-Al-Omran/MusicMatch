from __future__ import annotations

from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, HTTPServer
import secrets
import time
from urllib.parse import parse_qs, urlparse
import webbrowser

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
        requests_timeout=10,
    )


def connect_spotify(cfg: Config, timeout: float = 180) -> str:
    redirect = urlparse(cfg.spotify_redirect_uri)
    try:
        port = redirect.port
    except ValueError:
        port = None
    if redirect.scheme != "http" or redirect.hostname != "127.0.0.1" or not port or redirect.query or redirect.fragment:
        raise ValueError("Set the redirect URI in MusicMatch and Spotify's dashboard to "
                         "http://127.0.0.1:8888/callback, then try Connect again.")
    state = secrets.token_urlsafe(32)
    result = {}

    class Callback(BaseHTTPRequestHandler):
        def log_message(self, _format, *args):
            pass

        def do_GET(self):
            callback = urlparse(self.path)
            if callback.path != (redirect.path or "/"):
                self.send_error(404)
                return
            values = parse_qs(callback.query)
            if values.get("state") != [state]:
                self.send_error(400, "Login could not be verified. Return to MusicMatch and try again.")
                return
            if values.get("error"):
                result["error"] = "Spotify permission was declined. Click Connect to try again."
                message = "Spotify was not connected. You can return to MusicMatch."
            elif len(values.get("code", [])) == 1:
                result["code"] = values["code"][0]
                message = "Authorization received. Return to MusicMatch to finish connecting. You can close this tab."
            else:
                self.send_error(400)
                return
            body = ("<!doctype html><meta charset='utf-8'><title>MusicMatch</title><h2>MusicMatch</h2><p>"
                    + message + "</p>").encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

    class CallbackServer(HTTPServer):
        def get_request(self):
            connection, address = super().get_request()
            connection.settimeout(2)
            return connection, address

    try:
        server = CallbackServer(("127.0.0.1", port), Callback)
    except OSError as exc:
        raise ConnectionError(f"Cannot receive Spotify's reply on port {port}. Close another MusicMatch "
                              "window or any app using that port, then try Connect again.") from exc
    with server:
        auth = _make_auth(cfg, open_browser=False)
        auth.state = state
        if not webbrowser.open(auth.get_authorize_url(state=state)):
            raise ConnectionError("Could not open your browser for Spotify login. Check your default browser and try again.")
        deadline = time.monotonic() + timeout
        while not result and time.monotonic() < deadline:
            server.timeout = min(1, max(0.01, deadline - time.monotonic()))
            server.handle_request()
    if not result:
        raise TimeoutError("Spotify login timed out. Click Connect, sign in and click Agree in your browser. "
                           f"If Spotify shows a redirect error, register exactly {cfg.spotify_redirect_uri} in its dashboard.")
    if "error" in result:
        raise ConnectionError(result["error"])
    token = auth.get_access_token(code=result["code"], check_cache=False, as_dict=False)
    me = spotipy.Spotify(auth=token, requests_timeout=10).current_user()
    return me.get("display_name") or me["id"]


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
