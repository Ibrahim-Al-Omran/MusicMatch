"""Hand-drawn controls for the MusicMatch window.

Tk's canvas doesn't anti-alias, so every control is painted with Pillow at SS× size
and downsampled, then shown as an image. Sizes passed in are device pixels (already
multiplied by the window's scale factor `s`)."""

from __future__ import annotations

import functools
import sys
import tkinter as tk
import tkinter.font as tkfont
from collections.abc import Callable

from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageOps, ImageTk

SS = 4

BG = "#121214"
CARD = "#1c1c1f"
CONTROL = "#2a2a2f"
CONTROL_HI = "#3a3a40"
HAIRLINE = "#29292e"
TEXT = "#f2f2f4"
TEXT_2 = "#a2a2a9"
TEXT_3 = "#6c6c74"
DEFAULT_ACCENT = (255, 159, 90)

RGBf = tuple[float, float, float]


class ThemedScrollbar(tk.Canvas):
    def __init__(self, parent, orient, command):
        super().__init__(parent, width=12, height=12, bg=BG,
                         highlightthickness=0, bd=0, takefocus=True)
        self.vertical = orient == "vertical"
        self.command = command
        self.first, self.last = 0.0, 1.0
        self._drag = None
        self._hover = False
        self.bind("<Configure>", self._draw)
        self.bind("<Enter>", lambda _event: self._highlight(True))
        self.bind("<Leave>", lambda _event: self._highlight(False))
        self.bind("<Button-1>", self._press)
        self.bind("<B1-Motion>", self._motion)
        self.bind("<ButtonRelease-1>", self._release)
        for key, amount in (("<Up>", -1), ("<Down>", 1), ("<Left>", -1), ("<Right>", 1)):
            self.bind(key, lambda _event, amount=amount: self.command("scroll", amount, "units"))
        for key, amount in (("<Prior>", -1), ("<Next>", 1)):
            self.bind(key, lambda _event, amount=amount: self.command("scroll", amount, "pages"))
        self.bind("<Home>", lambda _event: self.command("moveto", 0))
        self.bind("<End>", lambda _event: self.command("moveto", 1))

    def set(self, first, last):
        self.first, self.last = float(first), float(last)
        self._draw()

    def _geometry(self):
        length = self.winfo_height() if self.vertical else self.winfo_width()
        length = max(1, length - 4)
        thumb = min(length, max(24, length * (self.last - self.first)))
        travel = length - thumb
        span = 1 - (self.last - self.first)
        start = 2 + (travel * self.first / span if span > 0 else 0)
        return start, thumb, travel, span

    def _draw(self, _event=None):
        self.delete("thumb")
        if self.last - self.first >= 1:
            return
        start, thumb, _travel, _span = self._geometry()
        color = TEXT_3 if self._hover or self._drag is not None else CONTROL_HI
        coords = (6, start + 3, 6, start + thumb - 3) if self.vertical else (start + 3, 6, start + thumb - 3, 6)
        self.create_line(*coords, fill=color, width=6, capstyle="round", tags="thumb")

    def _highlight(self, active):
        self._hover = active
        self._draw()

    def _press(self, event):
        self.focus_set()
        if self.last - self.first >= 1:
            return
        position = event.y if self.vertical else event.x
        start, thumb, _travel, _span = self._geometry()
        if start <= position <= start + thumb:
            self._drag = (position, self.first)
        else:
            self.command("scroll", -1 if position < start else 1, "pages")
        self._draw()

    def _motion(self, event):
        if self._drag is None:
            return
        position = event.y if self.vertical else event.x
        origin, first = self._drag
        _start, _thumb, travel, span = self._geometry()
        if travel > 0:
            self.command("moveto", max(0, min(span, first + (position - origin) * span / travel)))

    def _release(self, _event):
        self._drag = None
        self._draw()


class ScrollableFrame(tk.Frame):
    def __init__(self, parent):
        super().__init__(parent, bg=BG)
        self.rowconfigure(0, weight=1)
        self.columnconfigure(0, weight=1)
        self.canvas = tk.Canvas(self, bg=BG, highlightthickness=0, bd=0,
                                xscrollincrement=20, yscrollincrement=20, takefocus=True)
        vertical = ThemedScrollbar(self, orient="vertical", command=self.canvas.yview)
        horizontal = ThemedScrollbar(self, orient="horizontal", command=self.canvas.xview)
        self.canvas.configure(yscrollcommand=vertical.set, xscrollcommand=horizontal.set)
        self.canvas.grid(row=0, column=0, sticky="nsew")
        vertical.grid(row=0, column=1, sticky="ns")
        horizontal.grid(row=1, column=0, sticky="ew")
        self.content = tk.Frame(self.canvas, bg=BG)
        self._window = self.canvas.create_window(0, 0, window=self.content, anchor="nw")
        self._wheel_remainder = {"x": 0.0, "y": 0.0}
        self._sync_job = None
        self.winfo_toplevel().bind("<Configure>", self._content_changed, add="+")
        self.canvas.bind("<Configure>", self._sync_content)
        self.winfo_toplevel().bind("<MouseWheel>", self._mousewheel, add="+")
        if sys.platform not in ("win32", "darwin"):
            self.winfo_toplevel().bind("<Button-4>", self._mousewheel, add="+")
            self.winfo_toplevel().bind("<Button-5>", self._mousewheel, add="+")
        for key, amount in (("<Prior>", -1), ("<Next>", 1)):
            self.canvas.bind(key, lambda _event, amount=amount: self.canvas.yview_scroll(amount, "pages"))

    def fit_content(self, max_width: int, max_height: int) -> None:
        self.update_idletasks()
        self.canvas.configure(width=min(self.content.winfo_reqwidth(), max_width),
                              height=min(self.content.winfo_reqheight(), max_height))

    def _sync_content(self, _event=None) -> None:
        self._sync_job = None
        content_width = self.content.winfo_reqwidth()
        width = max(content_width, self.canvas.winfo_width())
        height = max(self.content.winfo_reqheight(), self.canvas.winfo_height())
        self.canvas.coords(self._window, (width - content_width) // 2, 0)
        self.canvas.configure(scrollregion=(0, 0, width, height))

    def _content_changed(self, event) -> None:
        widget = event.widget
        while widget is not None and widget is not self.content:
            widget = getattr(widget, "master", None)
        if widget is not None and self._sync_job is None:
            self._sync_job = self.after_idle(self._sync_content)

    def _mousewheel(self, event):
        widget = event.widget
        while widget is not None and widget is not self:
            widget = getattr(widget, "master", None)
        if widget is None:
            return
        axis = "x" if event.state & 0x1 else "y"
        view = self.canvas.xview if axis == "x" else self.canvas.yview
        if view() == (0.0, 1.0):
            return
        button = getattr(event, "num", None)
        if button in (4, 5):
            movement = -3 if button == 4 else 3
        else:
            movement = -event.delta if sys.platform == "darwin" else -event.delta / 120 * 3
        self._wheel_remainder[axis] += movement
        units = int(self._wheel_remainder[axis])
        self._wheel_remainder[axis] -= units
        scroll = self.canvas.xview_scroll if axis == "x" else self.canvas.yview_scroll
        if units:
            scroll(units, "units")
        return "break"


def to_rgb(color: str) -> RGBf:
    color = color.lstrip("#")
    return tuple(int(color[i : i + 2], 16) for i in (0, 2, 4))  # type: ignore[return-value]


def to_hex(color) -> str:
    return "#{:02x}{:02x}{:02x}".format(*(max(0, min(255, round(c))) for c in color[:3]))


def mix(a, b, t: float) -> RGBf:
    a = to_rgb(a) if isinstance(a, str) else a
    b = to_rgb(b) if isinstance(b, str) else b
    return tuple(a[i] + (b[i] - a[i]) * t for i in range(3))  # type: ignore[return-value]


def font_family(root: tk.Misc) -> str:
    available = set(tkfont.families(root))
    if sys.platform == "darwin":
        prefs = ("SF Pro Text", "SF Pro", "Helvetica Neue")
    else:
        prefs = ("Segoe UI Variable Text", "Segoe UI", "Helvetica", "Arial")
    return next((f for f in prefs if f in available), "TkDefaultFont")


def scale_factor(root: tk.Misc) -> float:
    # macOS already works in points (Retina is handled by the OS).
    if sys.platform == "darwin":
        return 1.0
    return max(1.0, root.winfo_fpixels("1i") / 96)


class Painter:
    """Supersampled RGBA drawing surface."""

    def __init__(self, w: float, h: float):
        self.w, self.h = max(1, round(w)), max(1, round(h))
        self.img = Image.new("RGBA", (self.w * SS, self.h * SS), (0, 0, 0, 0))
        self.d = ImageDraw.Draw(self.img)

    def rrect(self, x0, y0, x1, y1, r, fill, outline=None, width=1.0):
        self.d.rounded_rectangle(
            (x0 * SS, y0 * SS, x1 * SS - 1, y1 * SS - 1),
            radius=r * SS,
            fill=fill,
            outline=outline,
            width=max(1, round(width * SS)) if outline else 0,
        )

    def circle(self, cx, cy, r, fill, outline=None, width=1.0):
        self.d.ellipse(
            ((cx - r) * SS, (cy - r) * SS, (cx + r) * SS, (cy + r) * SS),
            fill=fill,
            outline=outline,
            width=max(1, round(width * SS)) if outline else 0,
        )

    def line(self, x0, y0, x1, y1, fill, width=1.0):
        self.d.line((x0 * SS, y0 * SS, x1 * SS, y1 * SS), fill=fill, width=max(1, round(width * SS)))

    def image(self) -> Image.Image:
        return self.img.resize((self.w, self.h), Image.LANCZOS)

    def photo(self) -> ImageTk.PhotoImage:
        return ImageTk.PhotoImage(self.image())


def shadowed_knob(p: Painter, cx: float, cy: float, r: float, s: float) -> None:
    p.circle(cx, cy + 0.8 * s, r + 0.4 * s, fill=(0, 0, 0, 80))
    p.circle(cx, cy, r, fill="#ffffff")


class Control(tk.Canvas):
    def __init__(self, parent: tk.Misc, w: float, h: float, bg: str = CARD):
        super().__init__(parent, width=round(w), height=round(h), bg=bg,
                         highlightthickness=0, bd=0)
        self.w, self.h = round(w), round(h)
        self._photo: ImageTk.PhotoImage | None = None
        self._img_id = self.create_image(0, 0, anchor="nw")

    def show(self, painter: Painter) -> None:
        self._photo = painter.photo()
        self.itemconfig(self._img_id, image=self._photo)


def _animate(widget: tk.Misc, start: float, end: float, apply: Callable[[float], None],
             frames: int = 7, delay: int = 14) -> None:
    def step(i: int) -> None:
        t = 1 - (1 - i / frames) ** 3
        apply(start + (end - start) * t)
        if i < frames:
            widget.after(delay, step, i + 1)

    step(1)


class Toggle(Control):
    def __init__(self, parent, s: float, var: tk.BooleanVar, command=None,
                 accent=DEFAULT_ACCENT, bg: str = CARD):
        super().__init__(parent, 40 * s, 24 * s, bg)
        self.s, self.var, self.command, self.accent = s, var, command, accent
        self.pos = 1.0 if var.get() else 0.0
        self.config(cursor="hand2")
        self.bind("<Button-1>", self._click)
        var.trace_add("write", lambda *_: self._sync())
        self.draw()

    def set_accent(self, accent) -> None:
        self.accent = accent
        self.draw()

    def _click(self, _e) -> None:
        self.var.set(not self.var.get())
        if self.command:
            self.command()

    def _sync(self) -> None:
        target = 1.0 if self.var.get() else 0.0
        if target != self.pos:
            _animate(self, self.pos, target, self._set_pos)

    def _set_pos(self, pos: float) -> None:
        self.pos = pos
        self.draw()

    def draw(self) -> None:
        s, p = self.s, Painter(self.w, self.h)
        p.rrect(0, 0, self.w, self.h, self.h / 2, fill=to_hex(mix(CONTROL_HI, self.accent, self.pos)))
        r = self.h / 2 - 2.5 * s
        cx = self.h / 2 + (self.w - self.h) * self.pos
        shadowed_knob(p, cx, self.h / 2, r, s)
        self.show(p)


class Slider(Control):
    def __init__(self, parent, s: float, width: float, var: tk.Variable, lo: float, hi: float,
                 step: float, on_change=None, on_release=None, accent=DEFAULT_ACCENT,
                 bg: str = CARD):
        super().__init__(parent, width, 22 * s, bg)
        self.s, self.var, self.lo, self.hi, self.step = s, var, lo, hi, step
        self.on_change, self.on_release, self.accent = on_change, on_release, accent
        self.knob_r = 8.5 * s
        self.config(cursor="hand2")
        self.bind("<Button-1>", self._drag)
        self.bind("<B1-Motion>", self._drag)
        self.bind("<ButtonRelease-1>", lambda _e: self.on_release and self.on_release())
        var.trace_add("write", lambda *_: self.draw())
        self.draw()

    def set_accent(self, accent) -> None:
        self.accent = accent
        self.draw()

    def _drag(self, e) -> None:
        pad = self.knob_r + 1
        frac = min(1.0, max(0.0, (e.x - pad) / (self.w - 2 * pad)))
        value = round((self.lo + frac * (self.hi - self.lo)) / self.step) * self.step
        value = int(round(value)) if isinstance(self.var, tk.IntVar) else round(value, 3)
        if value != self.var.get():
            self.var.set(value)
            if self.on_change:
                self.on_change(value)

    def draw(self) -> None:
        s, p = self.s, Painter(self.w, self.h)
        try:
            value = float(self.var.get())
        except (tk.TclError, ValueError):
            value = self.lo
        frac = (min(self.hi, max(self.lo, value)) - self.lo) / (self.hi - self.lo)
        pad, cy, th = self.knob_r + 1, self.h / 2, 4 * s
        x = pad + frac * (self.w - 2 * pad)
        p.rrect(pad - th / 2, cy - th / 2, self.w - pad + th / 2, cy + th / 2, th / 2, fill=CONTROL_HI)
        p.rrect(pad - th / 2, cy - th / 2, x + th / 2, cy + th / 2, th / 2, fill=to_hex(self.accent))
        shadowed_knob(p, x, cy, self.knob_r, s)
        self.show(p)


class Segmented(Control):
    def __init__(self, parent, s: float, options: list[tuple[str, str]], var: tk.StringVar,
                 font, command=None, bg: str = CARD, pad: float = 10):
        measure = tkfont.Font(font=font).measure
        self.seg = max(measure(label) for label, _ in options) + 2 * pad * s
        super().__init__(parent, self.seg * len(options) + 4 * s, 26 * s, bg)
        self.s, self.options, self.var, self.command = s, options, var, command
        self.values = [v for _, v in options]
        self.pos = float(self._index())
        self.texts = [
            self.create_text(2 * s + self.seg * (i + 0.5), self.h / 2, text=label, font=font)
            for i, (label, _) in enumerate(options)
        ]
        self.config(cursor="hand2")
        self.bind("<Button-1>", self._click)
        var.trace_add("write", lambda *_: _animate(self, self.pos, float(self._index()), self._set_pos))
        self.draw()

    def _index(self) -> int:
        return self.values.index(self.var.get()) if self.var.get() in self.values else 0

    def _click(self, e) -> None:
        i = int(min(len(self.values) - 1, max(0, (e.x - 2 * self.s) // self.seg)))
        if self.values[i] != self.var.get():
            self.var.set(self.values[i])
            if self.command:
                self.command()

    def _set_pos(self, pos: float) -> None:
        self.pos = pos
        self.draw()

    def draw(self) -> None:
        s, p = self.s, Painter(self.w, self.h)
        p.rrect(0, 0, self.w, self.h, 8 * s, fill=CONTROL)
        x = 2 * s + self.pos * self.seg
        p.rrect(x, 2 * s + 0.6 * s, x + self.seg, self.h - 2 * s + 0.6 * s, 6.5 * s, fill=(0, 0, 0, 70))
        p.rrect(x, 2 * s, x + self.seg, self.h - 2 * s, 6.5 * s, fill=CONTROL_HI)
        self.show(p)
        selected = self._index()
        for i, item in enumerate(self.texts):
            self.itemconfig(item, fill=TEXT if i == selected else TEXT_2)


class ColorWell(Control):
    def __init__(self, parent, s: float, color, command=None, bg: str = CARD):
        super().__init__(parent, 24 * s, 24 * s, bg)
        self.s, self.color, self.command = s, color, command
        self.config(cursor="hand2")
        self.bind("<Button-1>", lambda _e: self.command and self.command())
        self.draw()

    def set_color(self, color) -> None:
        self.color = color
        self.draw()

    def draw(self) -> None:
        s, p = self.s, Painter(self.w, self.h)
        c = self.w / 2
        p.circle(c, c, c - 0.5 * s, fill=CONTROL_HI)
        p.circle(c, c, c - 3 * s, fill=to_hex(self.color))
        self.show(p)


class PillButton(Control):
    def __init__(self, parent, s: float, text: str, font, command, bg: str = CARD,
                 primary: bool = False, accent=DEFAULT_ACCENT):
        width = tkfont.Font(font=font).measure(text) + 24 * s
        super().__init__(parent, width, 26 * s, bg)
        self.s, self.command, self.primary, self.accent = s, command, primary, accent
        self.hover = self.pressed = False
        self.text_id = self.create_text(self.w / 2, self.h / 2, text=text, font=font)
        self.config(cursor="hand2")
        self.bind("<Enter>", lambda _e: self._state(hover=True))
        self.bind("<Leave>", lambda _e: self._state(hover=False, pressed=False))
        self.bind("<Button-1>", lambda _e: self._state(pressed=True))
        self.bind("<ButtonRelease-1>", self._release)
        self.draw()

    def set_accent(self, accent) -> None:
        self.accent = accent
        self.draw()

    def set_text(self, text: str) -> None:
        """Width stays as created, so create with the longest label you'll use."""
        self.itemconfig(self.text_id, text=text)

    def _state(self, **kw) -> None:
        for k, v in kw.items():
            setattr(self, k, v)
        self.draw()

    def _release(self, _e) -> None:
        fire = self.pressed and self.hover
        self._state(pressed=False)
        if fire:
            self.command()

    def draw(self) -> None:
        s, p = self.s, Painter(self.w, self.h)
        if self.primary:
            fill = mix(self.accent, "#000000", 0.18 if self.pressed else 0.0)
            text = TEXT if sum(self.accent) < 380 else "#111114"
        else:
            fill = mix(CONTROL, CONTROL_HI, 1.0 if self.hover else 0.0)
            fill = mix(fill, "#000000", 0.2) if self.pressed else fill
            text = TEXT
        p.rrect(0, 0, self.w, self.h, 7 * s, fill=to_hex(fill))
        self.show(p)
        self.itemconfig(self.text_id, fill=text)


class Stepper(Control):
    def __init__(self, parent, s: float, var: tk.IntVar, values: list[int],
                 fmt: Callable[[int], str], font, command=None, bg: str = CARD):
        super().__init__(parent, 116 * s, 26 * s, bg)
        self.s, self.var, self.values, self.fmt, self.command = s, var, values, fmt, command
        self.minus = self.create_text(14 * s, self.h / 2, text="−", font=font, fill=TEXT_2)
        self.plus = self.create_text(self.w - 14 * s, self.h / 2, text="+", font=font, fill=TEXT_2)
        self.label = self.create_text(self.w / 2, self.h / 2, font=font, fill=TEXT)
        self.config(cursor="hand2")
        self.bind("<Button-1>", self._click)
        var.trace_add("write", lambda *_: self.draw())
        self.draw()

    def _index(self) -> int:
        try:
            current = int(self.var.get())
        except (tk.TclError, ValueError):
            current = self.values[0]
        return min(range(len(self.values)), key=lambda i: abs(self.values[i] - current))

    def _click(self, e) -> None:
        i = self._index()
        if e.x < self.w / 3:
            i = max(0, i - 1)
        elif e.x > self.w * 2 / 3:
            i = min(len(self.values) - 1, i + 1)
        else:
            return
        if self.values[i] != self.var.get():
            self.var.set(self.values[i])
            if self.command:
                self.command()

    def draw(self) -> None:
        s, p = self.s, Painter(self.w, self.h)
        p.rrect(0, 0, self.w, self.h, 8 * s, fill=CONTROL)
        p.line(self.w / 3 - 4 * s, 7 * s, self.w / 3 - 4 * s, self.h - 7 * s, fill=CONTROL_HI)
        p.line(self.w * 2 / 3 + 4 * s, 7 * s, self.w * 2 / 3 + 4 * s, self.h - 7 * s, fill=CONTROL_HI)
        self.show(p)
        self.itemconfig(self.label, text=self.fmt(self.values[self._index()]))


class Card(tk.Canvas):
    """Grouped rows on a rounded panel, like macOS settings lists."""

    def __init__(self, parent, s: float, width: float, row_height: float = 42):
        super().__init__(parent, width=round(width), height=1, bg=BG, highlightthickness=0, bd=0)
        self.s, self.w, self.row_h = s, round(width), row_height * s
        self.y = 0.0
        self._lines: list[float] = []
        self._photo: ImageTk.PhotoImage | None = None

    def row(self, label: str | None = None, font=None) -> float:
        if self.y:
            self._lines.append(self.y)
        cy = self.y + self.row_h / 2
        if label:
            self.create_text(14 * self.s, cy, text=label, anchor="w", font=font, fill=TEXT)
        self.y += self.row_h
        return cy

    def put(self, widget: tk.Widget, x: float, y: float, anchor: str = "e") -> None:
        self.create_window(x, y, window=widget, anchor=anchor)

    def finish(self) -> None:
        s, h = self.s, round(self.y)
        self.config(height=h)
        p = Painter(self.w, h)
        p.rrect(0, 0, self.w, h, 12 * s, fill=CARD)
        for y in self._lines:
            p.line(14 * s, y, self.w, y, fill=HAIRLINE, width=0.8 * s)
        self._photo = p.photo()
        self.tag_lower(self.create_image(0, 0, image=self._photo, anchor="nw"))


def _soft_blur(img: Image.Image, radius: float) -> Image.Image:
    """Gaussian blur done at reduced size: same look for wide blurs, a fraction of the work."""
    f = max(1, min(4, int(radius // 4)))
    if f == 1:
        return img.filter(ImageFilter.GaussianBlur(radius))
    small = img.resize((img.width // f, img.height // f), Image.BILINEAR)
    return small.filter(ImageFilter.GaussianBlur(radius / f)).resize(img.size, Image.BICUBIC)


@functools.lru_cache(maxsize=4)
def _rounded_mask(size: int, radius: float) -> Image.Image:
    mask = Image.new("L", (size * SS, size * SS), 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, size * SS - 1, size * SS - 1), radius=radius * SS, fill=255)
    return mask.resize((size, size), Image.LANCZOS)


def rounded_cover(img: Image.Image, size: int, radius: float) -> Image.Image:
    cover = ImageOps.fit(img.convert("RGBA"), (size, size), Image.LANCZOS)
    cover.putalpha(_rounded_mask(size, radius))
    return cover


class Hero(tk.Canvas):
    """Now-playing banner: album cover floating in a glow of the light's color."""

    def __init__(self, parent, s: float, width: float, fonts: dict):
        self.s, self.W, self.H = s, round(width), round(132 * s)
        super().__init__(parent, width=self.W, height=self.H, bg=BG, highlightthickness=0, bd=0)
        self.cover_px = round(100 * s)
        self.pad = 16 * s
        self.text_x = self.pad + self.cover_px + 20 * s
        self.fonts = fonts
        self._img_id = self.create_image(0, 0, anchor="nw")
        cy = self.H / 2
        self.title_id = self.create_text(self.text_x, cy - 18 * s, anchor="w", font=fonts["title"], fill=TEXT)
        self.artist_id = self.create_text(self.text_x, cy + 6 * s, anchor="w", font=fonts["body"], fill=TEXT_2)
        self.device_id = self.create_text(self.text_x, cy + 28 * s, anchor="w", font=fonts["caps"], fill=TEXT_3)
        self._current: Image.Image | None = None
        self._photo: ImageTk.PhotoImage | None = None
        self._fade_token = 0
        self._layers: tuple[Image.Image, Image.Image, Image.Image] | None = None
        self.set_content("", "", "", None, None)

    def set_content(self, title: str, subtitle: str, device: str, color, cover: Image.Image | None) -> None:
        self.set_text(title, subtitle, device)
        self.show(self.compose(color, cover))

    def set_text(self, title: str, subtitle: str, device: str) -> None:
        max_w = self.W - self.text_x - self.pad
        self.itemconfig(self.title_id, text=self._fit(title, self.fonts["title"], max_w))
        self.itemconfig(self.artist_id, text=self._fit(subtitle, self.fonts["body"], max_w))
        self.itemconfig(self.device_id, text=self._fit(device.upper(), self.fonts["caps"], max_w))

    def show(self, artwork: Image.Image) -> None:
        """Crossfade to artwork made by compose() (which is safe to call off the Tk thread)."""
        self._crossfade(artwork)

    def _fit(self, text: str, font, max_w: float) -> str:
        f = tkfont.Font(font=font)
        if f.measure(text) <= max_w:
            return text
        while text and f.measure(text + "…") > max_w:
            text = text[:-1]
        return text.rstrip() + "…"

    def _static_layers(self) -> tuple[Image.Image, Image.Image, Image.Image]:
        """Card background, its shape mask and the cover shadow: identical for every
        song, so they're drawn once."""
        if self._layers is None:
            s, W, H = self.s, self.W, self.H
            base = Image.new("RGBA", (W, H), to_hex(to_rgb(BG)))
            card = Painter(W, H)
            card.rrect(0, 0, W, H, 16 * s, fill=CARD)
            card_img = card.image()
            base.alpha_composite(card_img)
            x0, y0 = round(self.pad), round((H - self.cover_px) / 2)
            shadow = Image.new("RGBA", (W, H), (0, 0, 0, 0))
            ImageDraw.Draw(shadow).rounded_rectangle(
                (x0, y0 + 4 * s, x0 + self.cover_px, y0 + self.cover_px + 4 * s), radius=12 * s, fill=(0, 0, 0, 120))
            self._layers = (base, card_img.getchannel("A"), _soft_blur(shadow, 8 * s))
        return self._layers

    def compose(self, color, cover: Image.Image | None) -> Image.Image:
        s, W, H = self.s, self.W, self.H
        card, card_mask, shadow = self._static_layers()
        base = card.copy()

        cx, cy = self.pad + self.cover_px / 2, H / 2
        if color is not None:
            glow = Image.new("RGBA", (W, H), (0, 0, 0, 0))
            g = ImageDraw.Draw(glow)
            c = tuple(round(v) for v in color)
            g.ellipse((cx - 78 * s, cy - 64 * s, cx + 78 * s, cy + 64 * s), fill=c + (200,))
            g.ellipse((cx - 40 * s, cy - 90 * s, W * 0.85, cy + 90 * s), fill=c + (40,))
            glow = _soft_blur(glow, 34 * s)
            glow.putalpha(ImageChops.multiply(glow.getchannel("A"), card_mask))
            base.alpha_composite(glow)

        x0, y0 = round(self.pad), round((H - self.cover_px) / 2)
        if cover is not None:
            base.alpha_composite(shadow)
            base.alpha_composite(rounded_cover(cover, self.cover_px, 10 * s), (x0, y0))
        else:
            ph = Painter(self.cover_px, self.cover_px)
            tint = mix(CONTROL, color, 0.35) if color is not None else to_rgb(CONTROL)
            ph.rrect(0, 0, self.cover_px, self.cover_px, 10 * s, fill=to_hex(tint))
            mid = self.cover_px / 2
            for r, a in ((34, 60), (24, 45), (6, 160)):
                ph.circle(mid, mid, r * s, fill=None, outline=(255, 255, 255, a), width=1.2 * s)
            base.alpha_composite(ph.image(), (x0, y0))
        return base

    def _crossfade(self, new: Image.Image, frames: int = 9) -> None:
        self._fade_token += 1
        token, old = self._fade_token, self._current
        self._current = new
        if old is None:
            self._photo = ImageTk.PhotoImage(new)
            self.itemconfig(self._img_id, image=self._photo)
            return

        def step(i: int) -> None:
            if token != self._fade_token:
                return
            t = i / frames
            frame = new if i == frames else Image.blend(old, new, 1 - (1 - t) ** 2)
            self._photo = ImageTk.PhotoImage(frame)
            self.itemconfig(self._img_id, image=self._photo)
            if i < frames:
                self.after(22, step, i + 1)

        step(1)
