from __future__ import annotations

import json
import os
import sys
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path

# A packaged app (PyInstaller) keeps settings in the usual per-user folder; a cloned
# repo keeps them next to the code, as before.
FROZEN = bool(getattr(sys, "frozen", False))
ROOT = Path(__file__).resolve().parent.parent
RESOURCES = Path(getattr(sys, "_MEIPASS", ROOT))


def _data_dir() -> Path:
    if not FROZEN:
        return ROOT
    if sys.platform == "win32":
        base = Path(os.environ.get("APPDATA") or Path.home() / "AppData" / "Roaming")
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        base = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
    path = base / "MusicMatch"
    path.mkdir(parents=True, exist_ok=True)
    return path


DATA_DIR = _data_dir()
CONFIG_PATH = DATA_DIR / "config.json"
TOKEN_CACHE_PATH = DATA_DIR / ".spotify_token_cache"
LOG_PATH = DATA_DIR / "musicmatch.log"
COLOR_CACHE_PATH = DATA_DIR / ".musicmatch_colors.json"

RGB = tuple[int, int, int]

COLOR_MODES = ("spotify", "vibrant", "dominant")
IDLE_ACTIONS = ("keep", "off", "color")


class ConfigError(Exception):
    pass


@dataclass
class Config:
    spotify_client_id: str = ""
    spotify_client_secret: str = ""
    spotify_redirect_uri: str = "http://127.0.0.1:8888/callback"

    # Each light: {"type": "wiz", "host": "192.168.1.20", "name": ..., "enabled": true, ...}
    # (see musicmatch/devices). A bare IP string is a Magic Home controller.
    lights: list = field(default_factory=list)
    # Philips Hue bridge id -> app key, created when you press the bridge's button.
    hue_keys: dict = field(default_factory=dict)

    # "spotify": the color Spotify itself shows behind the album (falls back to vibrant).
    # "vibrant": most prominent *colorful* swatch (ignores black/white/grey backgrounds).
    # "dominant": literally the most common swatch.
    color_mode: str = "spotify"
    brightness: int = 100
    # Album colors are often muted; LEDs look washed out below this saturation.
    min_saturation: float = 0.55
    # Used when a cover has no real color (black & white art, greyscale photos).
    neutral_color: RGB = (255, 190, 120)

    # 0 = send the new color once and let the controller do its own smooth transition
    # (Magic Home controllers fade by themselves). >0 adds a stepped software fade for
    # controllers that snap instead.
    fade_seconds: float = 0.0
    poll_interval: float = 1.0
    # If false, lights that were switched off (e.g. "Alexa, turn off the lights") stay off.
    auto_turn_on: bool = False

    idle_after: float = 60
    idle_action: str = "keep"
    idle_color: RGB = (255, 160, 80)

    # False = the background service stays running but leaves the lights alone.
    sync_enabled: bool = True
    # Mirrors whether the background service is registered to start at login.
    start_at_login: bool = False

    def validate(self, need_spotify: bool = True, need_lights: bool = True) -> None:
        if need_spotify and (
            not self.spotify_client_id
            or not self.spotify_client_secret
            or self.spotify_client_id.startswith("PASTE_")
        ):
            raise ConfigError(
                "Connect Spotify first: click Keys under Setup (or fill in config.json)."
            )
        if need_lights and not self.enabled_lights():
            raise ConfigError(
                "No lights yet: click Find under Setup (or run `musicmatch discover --save`)."
            )
        if self.color_mode not in COLOR_MODES:
            raise ConfigError(f"color_mode must be one of {COLOR_MODES}")
        if self.idle_action not in IDLE_ACTIONS:
            raise ConfigError(f"idle_action must be one of {IDLE_ACTIONS}")
        if not 1 <= self.brightness <= 100:
            raise ConfigError("brightness must be between 1 and 100")
        if self.poll_interval < 1:
            raise ConfigError("poll_interval must be at least 1 second")

    def enabled_lights(self) -> list:
        return [e for e in self.lights if isinstance(e, str) or e.get("enabled", True)]


def load_config(path: Path = CONFIG_PATH) -> Config:
    if not path.exists():
        raise ConfigError(
            "Not set up yet: open the MusicMatch window (or copy config.example.json to config.json)."
        )
    raw = json.loads(path.read_text(encoding="utf-8"))
    if "start_syncing_on_launch" in raw:  # renamed when the background service arrived
        raw.setdefault("start_at_login", raw.pop("start_syncing_on_launch"))
    known = {f.name for f in fields(Config)}
    unknown = set(raw) - known
    if unknown:
        raise ConfigError(f"Unknown config keys: {', '.join(sorted(unknown))}")
    for key in ("neutral_color", "idle_color"):
        if key in raw:
            raw[key] = tuple(int(c) for c in raw[key])
    return Config(**raw)


def save_config(cfg: Config, path: Path = CONFIG_PATH) -> None:
    raw = {
        k: list(v) if isinstance(v, tuple) else v for k, v in asdict(cfg).items()
    }
    path.write_text(json.dumps(raw, indent=2) + "\n", encoding="utf-8")


def save_setting(key: str, value, path: Path = CONFIG_PATH) -> None:
    if path.exists():
        raw = json.loads(path.read_text(encoding="utf-8"))
    else:
        raw = {k: list(v) if isinstance(v, tuple) else v for k, v in asdict(Config()).items()}
    raw[key] = value
    path.write_text(json.dumps(raw, indent=2) + "\n", encoding="utf-8")


def save_lights(lights: list, path: Path = CONFIG_PATH) -> None:
    save_setting("lights", lights, path)
