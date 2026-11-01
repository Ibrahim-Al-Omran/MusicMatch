"""The MusicMatch mark: two beamed eighth notes, drawn with Pillow so there are no
image files to ship. Geometry is on a 100x100 grid, painted 4x and downsampled."""

from __future__ import annotations

import functools
import math

from PIL import Image, ImageDraw, ImageFilter

SS = 4
ACCENT = (255, 159, 90)
TILE = (28, 28, 31)
MUTED = (136, 136, 144)

HEADS = ((27, 78), (71, 70))  # note head centres
HEAD_RX, HEAD_RY, HEAD_TILT = 17, 12.5, -24
STEM_W = 9.0
STEM_TOP = (20, 11)  # stem top y for each note; the beam joins them
BEAM_H = 16.0


def _ellipse(cx: float, cy: float, k: float) -> list[tuple[float, float]]:
    t = math.radians(HEAD_TILT)
    pts = []
    for i in range(64):
        a = 2 * math.pi * i / 64
        x, y = HEAD_RX * math.cos(a), HEAD_RY * math.sin(a)
        pts.append(((cx + x * math.cos(t) - y * math.sin(t)) * k, (cy + x * math.sin(t) + y * math.cos(t)) * k))
    return pts


@functools.lru_cache(maxsize=32)
def _mask(size: int) -> Image.Image:
    """Alpha mask of the note at `size` px, with ~6% padding."""
    big = size * SS
    m = Image.new("L", (big, big), 0)
    d = ImageDraw.Draw(m)
    k = big / 100
    stems = []
    for (cx, cy), top in zip(HEADS, STEM_TOP):
        d.polygon(_ellipse(cx, cy, k), fill=255)
        # Stem rises from the head's right edge.
        right = cx + HEAD_RX * 0.86
        stems.append(right)
        d.rectangle(((right - STEM_W) * k, top * k, right * k, (cy - 2) * k), fill=255)
    (x0, x1), (y0, y1) = stems, STEM_TOP
    d.polygon([((x0 - STEM_W) * k, y0 * k), (x1 * k, y1 * k),
               (x1 * k, (y1 + BEAM_H) * k), ((x0 - STEM_W) * k, (y0 + BEAM_H) * k)], fill=255)
    return m.resize((size, size), Image.LANCZOS)


def _vivid(color) -> tuple[int, int, int]:
    """Full-brightness version of a light colour (a dim light still gets a bright icon)."""
    if color is None:
        return ACCENT
    r, g, b = (float(v) for v in color)
    top = max(r, g, b)
    if top < 8:
        return ACCENT
    return tuple(round(v * 255 / top) for v in (r, g, b))


def _gradient(size: int, top, bottom) -> Image.Image:
    col = Image.new("RGBA", (1, size))
    for y in range(size):
        t = y / max(1, size - 1)
        col.putpixel((0, y), tuple(round(a + (b - a) * t) for a, b in zip(top, bottom)) + (255,))
    return col.resize((size, size))


def _lighten(color, amount: float) -> tuple[int, int, int]:
    return tuple(round(c + (255 - c) * amount) for c in color)


def mark(size: int, color=None) -> Image.Image:
    """Just the note, filled with a soft gradient of `color`. Transparent background."""
    c = tuple(color) if color == MUTED else _vivid(color)
    img = _gradient(size, _lighten(c, 0.45), c)
    img.putalpha(_mask(size))
    return img


def tray_icon(color=None, size: int = 64, muted: bool = False) -> Image.Image:
    """Note with a soft dark halo, for the tray / menu bar: the halo vanishes on a dark
    taskbar and keeps pale colours readable on a light one. `muted` = paused (grey)."""
    c = MUTED if muted else _vivid(color)
    note = mark(round(size * 0.94), c)
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    off = (size - note.width) // 2
    halo = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    halo.putalpha(_padded(_mask(note.width), size, off).filter(ImageFilter.GaussianBlur(size / 24)).point(lambda a: a * 0.5))
    img.alpha_composite(halo)
    img.alpha_composite(note, (off, off))
    return img


def app_icon(size: int = 256, color=None) -> Image.Image:
    """Dark rounded tile with the glowing note: window, taskbar and Dock icon."""
    c = _vivid(color)
    big = size * SS
    tile = Image.new("RGBA", (big, big), (0, 0, 0, 0))
    ImageDraw.Draw(tile).rounded_rectangle((0, 0, big - 1, big - 1), radius=big * 0.23, fill=TILE + (255,))
    tile = tile.resize((size, size), Image.LANCZOS)
    shape = tile.getchannel("A")

    glow = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    r = size * 0.36
    ImageDraw.Draw(glow).ellipse((size / 2 - r, size / 2 - r * 0.9, size / 2 + r, size / 2 + r * 1.1), fill=c + (210,))
    glow = glow.filter(ImageFilter.GaussianBlur(size / 7))
    glow.putalpha(Image.composite(glow.getchannel("A"), Image.new("L", (size, size), 0), shape))
    tile.alpha_composite(glow)

    note_px = round(size * (0.64 if size > 32 else 0.8))  # tiny icons need a bigger note to read
    off = (size - note_px) // 2
    tile.alpha_composite(mark(note_px, c), (off, off + round(size * 0.01)))
    return tile


def _padded(mask: Image.Image, size: int, off: int) -> Image.Image:
    out = Image.new("L", (size, size), 0)
    out.paste(mask, (off, off))
    return out
