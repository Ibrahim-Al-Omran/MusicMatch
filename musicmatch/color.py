from __future__ import annotations

import colorsys
import re
from dataclasses import dataclass
from io import BytesIO

import requests
from PIL import Image

from .config import RGB

PALETTE_SIZE = 8
# Swatches below this "colorfulness" (saturation * value) are treated as black/white/grey.
MIN_CHROMA = 0.15
# A colorful swatch must cover at least this share of the cover to beat a grey background.
MIN_VIBRANT_SHARE = 0.03


@dataclass(frozen=True)
class Swatch:
    rgb: RGB
    share: float
    hue: float
    saturation: float
    value: float

    @property
    def chroma(self) -> float:
        return self.saturation * self.value

    @property
    def hex(self) -> str:
        return "#{:02x}{:02x}{:02x}".format(*self.rgb)


SPOTIFY_EMBED_URL = "https://open.spotify.com/embed/{kind}/{item_id}"
# The public embed player is tinted with Spotify's own extracted album color, the same
# one the apps use behind the Now Playing view. It's exposed as a CSS variable.
_EMBED_BACKGROUND_RE = re.compile(r"--background-base:\s*rgba?\(\s*(\d+),\s*(\d+),\s*(\d+)")
# Spotify's background colors are deliberately dark, so judge greyness by saturation only.
MIN_SPOTIFY_SATURATION = 0.15

# Keep-alive session: skips a fresh TLS handshake on every song change.
_http = requests.Session()
_http.headers["User-Agent"] = "Mozilla/5.0"


def parse_embed_background(html: str) -> RGB | None:
    m = _EMBED_BACKGROUND_RE.search(html)
    return (int(m[1]), int(m[2]), int(m[3])) if m else None


def spotify_background_color(kind: str, item_id: str) -> RGB | None:
    """Spotify's own background color for a track/episode, or None if unavailable."""
    if kind not in ("track", "episode") or not item_id.isalnum():
        return None
    try:
        resp = _http.get(SPOTIFY_EMBED_URL.format(kind=kind, item_id=item_id), timeout=10)
        resp.raise_for_status()
    except requests.RequestException:
        return None
    return parse_embed_background(resp.text)


def led_color_from_spotify(
    rgb: RGB, min_saturation: float, brightness: int, neutral_color: RGB
) -> RGB:
    _, s, _ = colorsys.rgb_to_hsv(*(c / 255 for c in rgb))
    if s < MIN_SPOTIFY_SATURATION:
        return scale(neutral_color, brightness)
    return to_led(rgb, min_saturation, brightness)


def fetch_image(url: str) -> bytes:
    resp = _http.get(url, timeout=10)
    resp.raise_for_status()
    return resp.content


def load_image(source: str) -> bytes:
    if source.startswith(("http://", "https://")):
        return fetch_image(source)
    with open(source, "rb") as fh:
        return fh.read()


def palette(image_bytes: bytes, size: int = PALETTE_SIZE) -> list[Swatch]:
    """Quantize the cover into `size` colors, most common first."""
    img = Image.open(BytesIO(image_bytes)).convert("RGB")
    img.thumbnail((150, 150))
    quantized = img.quantize(colors=size, method=Image.Quantize.MEDIANCUT)
    pal = quantized.getpalette() or []
    counts = quantized.getcolors() or []
    total = sum(c for c, _ in counts) or 1

    swatches = []
    for count, idx in sorted(counts, reverse=True):
        rgb = (pal[idx * 3], pal[idx * 3 + 1], pal[idx * 3 + 2])
        h, s, v = colorsys.rgb_to_hsv(*(c / 255 for c in rgb))
        swatches.append(Swatch(rgb, count / total, h, s, v))
    return swatches


def pick_swatch(swatches: list[Swatch], mode: str) -> Swatch | None:
    """Returns None when the cover has no usable color (caller uses neutral_color)."""
    if not swatches:
        return None
    if mode == "dominant":
        top = swatches[0]
        return top if top.chroma >= MIN_CHROMA else None

    candidates = [
        s for s in swatches if s.chroma >= MIN_CHROMA and s.share >= MIN_VIBRANT_SHARE
    ]
    if not candidates:
        return None
    # Coverage matters most, but a vivid accent should beat a large muddy area.
    return max(candidates, key=lambda s: s.share * (0.2 + s.saturation) * (0.3 + s.value))


def to_led(rgb: RGB, min_saturation: float, brightness: int) -> RGB:
    """LED strips can't show dark or muddy colors: they just look dim or white.
    Keep the hue, push saturation up and drive at full value, then apply brightness."""
    h, s, _ = colorsys.rgb_to_hsv(*(c / 255 for c in rgb))
    s = max(s, min_saturation)
    r, g, b = colorsys.hsv_to_rgb(h, s, 1.0)
    return scale((round(r * 255), round(g * 255), round(b * 255)), brightness)


def scale(rgb: RGB, brightness: int) -> RGB:
    f = max(1, min(100, brightness)) / 100
    return tuple(round(c * f) for c in rgb)  # type: ignore[return-value]


def rebrighten(rgb: RGB, brightness: int) -> RGB | None:
    """Same hue at a new brightness. None for black, which has no hue to keep."""
    peak = max(rgb)
    if peak == 0:
        return None
    return scale(tuple(round(c * 255 / peak) for c in rgb), brightness)  # type: ignore[arg-type]


def led_color_for_image(
    image_bytes: bytes,
    mode: str,
    min_saturation: float,
    brightness: int,
    neutral_color: RGB,
) -> tuple[RGB, Swatch | None]:
    swatch = pick_swatch(palette(image_bytes), mode)
    if swatch is None:
        return scale(neutral_color, brightness), None
    return to_led(swatch.rgb, min_saturation, brightness), swatch
