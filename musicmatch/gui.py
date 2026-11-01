"""Desktop window for MusicMatch (Tkinter + Pillow: works on Windows and macOS)."""

from __future__ import annotations

import io
import logging
import queue
import subprocess
import sys
import threading
import time
import tkinter as tk
import webbrowser
from dataclasses import replace
from tkinter import colorchooser, messagebox

from PIL import Image, ImageTk

from . import autostart, logo
from . import service as svc
from . import widgets as w
from . import devices
from .config import (
    CONFIG_PATH,
    DATA_DIR,
    LOG_PATH,
    RESOURCES,
    TOKEN_CACHE_PATH,
    RGB,
    Config,
    ConfigError,
    load_config,
    save_config,
)

log = logging.getLogger(__name__)

DASHBOARD_URL = "https://developer.spotify.com/dashboard"
SAVE_DELAY_MS = 400
TICK_MS = 150  # how quickly the window reflects a song change (the lights don't wait on it)
HIDDEN_TICK_MS = 1000
LIVENESS_CHECK_S = 3
CARD_W = 344
GAP = 16
GUI_LOCK_PATH = DATA_DIR / ".musicmatch_gui.lock"
SHOW_PATH = DATA_DIR / ".musicmatch_show"
HUE_PAIR_SECONDS = 30
IDLE_STEPS = [10, 15, 30, 45, 60, 90, 120, 180, 300, 600, 900, 1800, 3600]


def _fmt_seconds(sec: int) -> str:
    return f"{sec} s" if sec < 60 else f"{sec // 60} min"


def _blank_placeholder(value: str) -> str:
    return "" if value.startswith("PASTE_") else value


class QueueLogHandler(logging.Handler):
    def __init__(self, events: queue.Queue):
        super().__init__(logging.INFO)
        self.events = events

    def emit(self, record: logging.LogRecord) -> None:
        self.events.put(("log", record.getMessage()))


class App:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.events: queue.Queue = queue.Queue()
        self.cfg = self._load()
        self._save_job: str | None = None
        self._loading = False
        self._config_mtime = self._read_mtime()
        self.accent = w.DEFAULT_ACCENT
        self._accented: list = []
        self._track_key: str | None = None
        self._track_color = None
        self._cover: Image.Image | None = None
        self._state_seen: tuple | None = None
        self._state_sig: tuple | None = None
        self._state_checked = 0.0
        self._ticks = 0
        self._bg_running: bool | None = None

        self.s = w.scale_factor(root)
        family = w.font_family(root)
        k = 1.3 if sys.platform == "darwin" else 1.0
        self.fonts = {
            "brand": (family, round(13 * k), "bold"),
            "title": (family, round(15 * k), "bold"),
            "body": (family, round(10 * k)),
            "small": (family, round(9 * k)),
            "caps": (family, round(8 * k), "bold"),
        }

        root.title("MusicMatch")
        root.configure(bg=w.BG)
        root.resizable(False, False)
        self._build()
        _style_title_bar(root)
        _set_icon(root)
        logging.getLogger().addHandler(QueueLogHandler(self.events))
        root.protocol("WM_DELETE_WINDOW", self._quit)
        root.bind("<Map>", self._on_map, add="+")
        root.after(100, self._drain_events)

        if sys.platform == "darwin":  # Dock icon clicked / app opened again
            root.createcommand("::tk::mac::ReopenApplication", self._show_window)

        self._check_spotify_login()
        self._sync_login_item()
        svc.start_background()
        self._poll_background()
        if not self.cfg.lights:  # first run: go look for lights straight away
            root.after(400, self._discover)

    # ---------- layout ----------

    def _build(self) -> None:
        s, f = self.s, self.fonts
        full_w = CARD_W * 2 + GAP
        main = tk.Frame(self.root, bg=w.BG, padx=round(20 * s), pady=round(16 * s))
        main.pack()

        # Header: wordmark + sync switch
        header = tk.Canvas(main, width=round(full_w * s), height=round(40 * s), bg=w.BG, highlightthickness=0)
        header.grid(row=0, column=0, columnspan=2, sticky="w")
        self.brand_mark = header.create_image(0, round(18 * s), anchor="w")
        header.create_text(round(24 * s), round(18 * s), text="MusicMatch", anchor="w", font=f["brand"], fill=w.TEXT)
        self.header = header
        self.sync_var = tk.BooleanVar(value=self.cfg.sync_enabled)
        self.sync_toggle = self._accent(w.Toggle(header, s, self.sync_var, command=self._sync_toggled, bg=w.BG))
        header.create_window(round(full_w * s), round(18 * s), window=self.sync_toggle, anchor="e")
        self.sync_lbl = header.create_text(round((full_w - 50) * s), round(18 * s), anchor="e",
                                           font=f["body"], fill=w.TEXT_2, text="Off")
        self._draw_brand_mark()

        self.hero = w.Hero(main, s, full_w * s, f)
        self.hero.grid(row=1, column=0, columnspan=2, pady=(round(6 * s), round(GAP * s)))

        left = tk.Frame(main, bg=w.BG)
        left.grid(row=2, column=0, sticky="n", padx=(0, round(GAP * s)))
        right = tk.Frame(main, bg=w.BG)
        right.grid(row=2, column=1, sticky="n")

        # Light
        self._section(left, "LIGHT")
        card = w.Card(left, s, CARD_W * s)
        self.brightness = tk.IntVar(value=self.cfg.brightness)
        self.boost = tk.DoubleVar(value=self.cfg.min_saturation)
        self.fade = tk.DoubleVar(value=self.cfg.fade_seconds)
        self._value_labels: list = []
        self._slider_row(card, "Brightness", self.brightness, 1, 100, 1,
                         lambda v: f"{int(v)}%", on_release=self._brightness_released)
        self._slider_row(card, "Color boost", self.boost, 0, 1, 0.05, lambda v: f"{float(v):.0%}")
        self._slider_row(card, "Extra fade", self.fade, 0, 3, 0.1,
                         lambda v: "Built-in" if float(v) < 0.05 else f"{float(v):.1f} s")
        self.mode = tk.StringVar(value=self.cfg.color_mode)
        y = card.row("Colors from", f["body"])
        card.put(w.Segmented(card, s, [("Spotify", "spotify"), ("Vibrant", "vibrant"),
                                       ("Dominant", "dominant")], self.mode, f["small"],
                             command=self._changed, pad=9), (CARD_W - 10) * s, y)
        y = card.row("Black & white covers", f["body"])
        self.neutral_well = w.ColorWell(card, s, self.cfg.neutral_color, lambda: self._pick_color("neutral_color"))
        card.put(self.neutral_well, (CARD_W - 12) * s, y)
        self.auto_on = tk.BooleanVar(value=self.cfg.auto_turn_on)
        y = card.row("Turn lights on with music", f["body"])
        card.put(self._accent(w.Toggle(card, s, self.auto_on, command=self._changed)), (CARD_W - 12) * s, y)
        card.finish()
        card.pack()

        # Every light found on the network, each with its own switch
        self._section(left, "YOUR LIGHTS")
        self.lights_holder = tk.Frame(left, bg=w.BG)
        self.lights_holder.pack()
        self._light_widgets: list = []
        self._scanning = False
        self._build_lights_card()

        # When music stops
        self._section(right, "WHEN MUSIC STOPS")
        card = w.Card(right, s, CARD_W * s)
        self.idle_action = tk.StringVar(value=self.cfg.idle_action)
        y = card.row("Lights", f["body"])
        card.put(w.Segmented(card, s, [("Keep color", "keep"), ("Turn off", "off"), ("Idle color", "color")],
                             self.idle_action, f["small"], command=self._changed, pad=9),
                 (CARD_W - 10) * s, y)
        self.idle_after = tk.IntVar(value=int(self.cfg.idle_after))
        y = card.row("After", f["body"])
        card.put(w.Stepper(card, s, self.idle_after, IDLE_STEPS, _fmt_seconds, f["body"],
                           command=self._changed), (CARD_W - 10) * s, y)
        y = card.row("Idle color", f["body"])
        self.idle_well = w.ColorWell(card, s, self.cfg.idle_color, lambda: self._pick_color("idle_color"))
        card.put(self.idle_well, (CARD_W - 12) * s, y)
        card.finish()
        card.pack()

        # Setup
        self._section(right, "SETUP")
        card = self._setup_card = w.Card(right, s, CARD_W * s)
        y = card.row("Lights", f["body"])
        self.lights_text = card.create_text(78 * s, y, anchor="w", font=f["small"], fill=w.TEXT_2,
                                            text=self._lights_text())
        test_btn = w.PillButton(card, s, "Test", f["small"], self._test_lights)
        card.put(test_btn, (CARD_W - 10) * s, y)
        card.put(w.PillButton(card, s, "Find", f["small"], self._discover),
                 (CARD_W - 16) * s - test_btn.w, y)
        y = card.row("Spotify", f["body"])
        self.spotify_text = card.create_text(78 * s, y, anchor="w", font=f["small"], fill=w.TEXT_3,
                                             text="Not connected")
        card.put(w.PillButton(card, s, "Keys", f["small"], self._toggle_keys), (CARD_W - 10) * s, y)
        y = card.row("Background", f["body"])
        self.bg_text = card.create_text(98 * s, y, anchor="w", font=f["small"], fill=w.TEXT_3, text="Checking...")
        self.bg_btn = w.PillButton(card, s, "Start", f["small"], self._toggle_background)
        card.put(self.bg_btn, (CARD_W - 10) * s, y)
        self.autostart = tk.BooleanVar(value=self.cfg.start_at_login)
        y = card.row("Start at login", f["body"])
        login_toggle = self._accent(w.Toggle(card, s, self.autostart, command=self._login_toggled))
        card.put(login_toggle, (CARD_W - 12) * s, y)
        if not autostart.supported():
            login_toggle.unbind("<Button-1>")
        card.finish()
        card.pack()

        self.keys_card = self._build_keys_card(right)
        if not (self.cfg.spotify_client_id and not self.cfg.spotify_client_id.startswith("PASTE_")):
            self._toggle_keys()

        self.status = tk.Label(main, text="", font=f["small"], fg=w.TEXT_3, bg=w.BG, anchor="w")
        self.status.grid(row=3, column=0, columnspan=2, sticky="ew", pady=(round(14 * s), 0))

    def _build_keys_card(self, parent) -> tk.Frame:
        s, f = self.s, self.fonts
        holder = tk.Frame(parent, bg=w.BG)
        card = w.Card(holder, s, CARD_W * s)
        y = card.row()
        link = card.create_text(14 * s, y, anchor="w", text="1. Create an app on Spotify  ↗",
                                font=f["small"], fill=w.TEXT)
        card.tag_bind(link, "<Button-1>", lambda _e: webbrowser.open(DASHBOARD_URL))
        card.tag_bind(link, "<Enter>", lambda _e: card.config(cursor="hand2"))
        card.tag_bind(link, "<Leave>", lambda _e: card.config(cursor=""))
        card.create_text((CARD_W - 14) * s, y, anchor="e", text="needs Spotify Premium",
                         font=f["small"], fill=w.TEXT_3)
        y = card.row()
        redirect = self.cfg.spotify_redirect_uri
        card.create_text(14 * s, y, anchor="w", font=f["small"], fill=w.TEXT_2,
                         text=f"2. Redirect URI  {redirect}")
        card.put(w.PillButton(card, s, "Copy", f["small"], lambda: self._copy(redirect)), (CARD_W - 10) * s, y)
        self.client_id = tk.StringVar(value=_blank_placeholder(self.cfg.spotify_client_id))
        self.client_secret = tk.StringVar(value=_blank_placeholder(self.cfg.spotify_client_secret))
        for label, var, show in (("Client ID", self.client_id, ""), ("Secret", self.client_secret, "•")):
            y = card.row(label, f["body"])
            entry = tk.Entry(card, textvariable=var, show=show, font=f["small"], width=26,
                             bg=w.CONTROL, fg=w.TEXT, insertbackground=w.TEXT, relief="flat",
                             highlightthickness=1, highlightbackground=w.CONTROL,
                             highlightcolor=w.CONTROL_HI)
            card.put(entry, (CARD_W - 12) * s, y)
        y = card.row()
        card.create_text(14 * s, y, anchor="w", text="3. Paste its keys above, then",
                         font=f["small"], fill=w.TEXT_2)
        card.put(self._accent(w.PillButton(card, s, "Connect", f["small"], self._connect_spotify,
                                           primary=True)), (CARD_W - 10) * s, y)
        card.finish()
        card.pack(pady=(round(10 * s), 0))
        return holder

    def _build_lights_card(self) -> None:
        s, f = self.s, self.fonts
        for widget in self._light_widgets:
            if widget in self._accented:
                self._accented.remove(widget)
        for child in self.lights_holder.winfo_children():
            child.destroy()
        self._light_widgets = []

        card = w.Card(self.lights_holder, s, CARD_W * s)
        entries = [devices.normalize(e) for e in self.cfg.lights]
        for i, entry in enumerate(entries):
            name = entry.get("name") or devices.DRIVERS[entry["type"]].brand
            y = card.row(name if len(name) <= 22 else name[:21] + "…", f["body"])
            brand = devices.DRIVERS[entry["type"]].brand
            detail = entry["host"] if name.startswith(brand) else f"{brand} · {entry['host']}"
            card.create_text((CARD_W - 64) * s, y, anchor="e", font=f["small"], fill=w.TEXT_3, text=detail)
            var = tk.BooleanVar(value=entry.get("enabled", True))
            toggle = w.Toggle(card, s, var, command=lambda i=i, v=var: self._light_toggled(i, v.get()),
                              accent=self.accent)
            self._light_widgets.append(self._accent(toggle))
            card.put(toggle, (CARD_W - 12) * s, y)
        y = card.row()
        hint = ("Searching your network..." if self._scanning else
                "No lights found yet" if not entries else "Switch off any you don't want synced")
        card.create_text(14 * s, y, anchor="w", font=f["small"], fill=w.TEXT_3, text=hint)
        find = w.PillButton(card, s, "Search again" if entries else "Find lights", f["small"], self._discover)
        self._light_widgets.append(find)
        card.put(find, (CARD_W - 10) * s, y)
        card.finish()
        card.pack()

    def _light_toggled(self, index: int, on: bool) -> None:
        lights = [devices.normalize(e) for e in self.cfg.lights]
        lights[index] = {**lights[index], "enabled": on}
        self.cfg = replace(self.cfg, lights=lights)
        self._refresh_lights_text()
        self._changed()

    def _copy(self, text: str) -> None:
        self.root.clipboard_clear()
        self.root.clipboard_append(text)
        self._set_status(f"Copied {text}")

    def _section(self, parent, text: str) -> None:
        top = 18 if parent.winfo_children() else 4
        tk.Label(parent, text=text, font=self.fonts["caps"], fg=w.TEXT_3, bg=w.BG).pack(
            anchor="w", padx=round(14 * self.s), pady=(round(top * self.s), round(6 * self.s)))

    def _slider_row(self, card: w.Card, label, var, lo, hi, step, fmt, on_release=None) -> None:
        s = self.s
        y = card.row(label, self.fonts["body"])
        value_id = card.create_text((CARD_W - 14) * s, y, anchor="e", font=self.fonts["small"],
                                    fill=w.TEXT_2, text=fmt(var.get()))
        refresh = lambda *_: card.itemconfig(value_id, text=fmt(var.get()))  # noqa: E731
        var.trace_add("write", refresh)

        def released():
            self._changed()
            if on_release:
                on_release()

        slider = w.Slider(card, s, (CARD_W - 196) * s, var, lo, hi, step, on_release=released)
        card.put(self._accent(slider), 124 * s, y, anchor="w")

    def _accent(self, widget):
        self._accented.append(widget)
        return widget

    def _set_accent(self, color) -> None:
        self.accent = tuple(color) if color is not None else w.DEFAULT_ACCENT
        for widget in self._accented:
            widget.set_accent(self.accent)
        self._draw_brand_mark()

    def _draw_brand_mark(self) -> None:
        self._brand_photo = ImageTk.PhotoImage(logo.mark(round(19 * self.s), self.accent))
        self.header.itemconfig(self.brand_mark, image=self._brand_photo)

    # ---------- now playing ----------

    def _poll_background(self) -> None:
        """Mirror the background service's state file in the window."""
        state = svc.background_state()
        running = state is not None
        if running != self._bg_running:
            self._bg_running = running
            self.card_text(self.bg_text, "Running, keeps going when closed" if running else "Stopped",
                           w.TEXT_2 if running else w.TEXT_3)
            self.bg_btn.set_text("Stop" if running else "Start")
        status = state.get("status") if state else "stopped"
        seen = (status, state.get("item_id"), tuple(state.get("color") or ()),
                message if status == "setup" else None) if state else ("stopped",)
        message = state.get("message") if state else None
        if message and message != getattr(self, "_last_message", None):
            self._last_message = message
            self._set_status(message)
        if seen == self._state_seen:
            return
        self._state_seen = seen

        label = {"playing": "Syncing", "idle": "Syncing", "paused": "Paused"}.get(status, "Off")
        self.header.itemconfig(self.sync_lbl, text=label, fill=w.TEXT if label == "Syncing" else w.TEXT_2)
        if status == "playing":
            self._show_track(state)
            return
        self._track_key = None
        title, sub = {
            "idle": ("Nothing playing", "Play something on Spotify, on any device"),
            "paused": ("Paused", "Flip the switch in the corner to resume"),
            "setup": ("Almost there", (state or {}).get("message", "").split(" (")[0]),
            "error": ("Having trouble", "Retrying in a moment..."),
            "starting": ("Starting...", ""),
        }.get(status, ("Background is off", "Click Start under Setup to keep your lights synced"))
        self.hero.set_content(title, sub, "", None, None)
        self._set_accent(None)

    def _show_track(self, state: dict) -> None:
        key, color = state.get("image_url") or state.get("item_id"), tuple(state["color"])
        if key != self._track_key:
            self._cover = None
        self._track_key, self._track_color = key, color
        self.hero.set_text(state.get("title", ""), f"{state.get('artist', '')}  —  {state.get('album', '')}",
                           f"Playing on {state.get('device', '')}")
        self._set_accent(color)
        url, cover = state.get("image_url"), self._cover
        threading.Thread(target=self._render_hero, args=(key, color, url, cover), daemon=True).start()

    def _render_hero(self, key, color, url: str | None, cover: Image.Image | None) -> None:
        """Off the Tk thread: download the cover if needed and draw the banner once."""
        if cover is None and url:
            from .color import fetch_image

            try:
                cover = Image.open(io.BytesIO(fetch_image(url)))
                cover.load()
            except Exception as exc:
                log.debug("Cover download failed: %s", exc)
        self.events.put(("hero", (key, color, cover, self.hero.compose(color, cover))))

    # ---------- settings ----------

    def _load(self) -> Config:
        try:
            return load_config()
        except (ConfigError, ValueError) as exc:
            if CONFIG_PATH.exists():
                messagebox.showwarning("MusicMatch", f"Couldn't read config.json:\n{exc}\n\nUsing defaults.")
            return Config()

    def _settings_from_ui(self) -> Config:
        try:
            idle_after = max(5, int(self.idle_after.get()))
        except (tk.TclError, ValueError):
            idle_after = int(self.cfg.idle_after)
        return replace(
            self.cfg,
            spotify_client_id=self.client_id.get().strip(),
            spotify_client_secret=self.client_secret.get().strip(),
            brightness=int(self.brightness.get()),
            min_saturation=round(float(self.boost.get()), 2),
            fade_seconds=round(float(self.fade.get()), 1),
            color_mode=self.mode.get(),
            auto_turn_on=bool(self.auto_on.get()),
            idle_action=self.idle_action.get(),
            idle_after=idle_after,
            sync_enabled=bool(self.sync_var.get()),
            start_at_login=bool(self.autostart.get()),
        )

    def _changed(self) -> None:
        if self._loading:
            return
        self.cfg = self._settings_from_ui()
        if self._save_job:
            self.root.after_cancel(self._save_job)
        self._save_job = self.root.after(SAVE_DELAY_MS, self._save)

    def _save(self) -> None:
        self._save_job = None
        try:
            save_config(self.cfg)
            self._config_mtime = self._read_mtime()
        except OSError as exc:
            self._set_status(f"Couldn't save settings: {exc}")

    def _read_mtime(self) -> float | None:
        try:
            return CONFIG_PATH.stat().st_mtime
        except OSError:
            return None

    def _reload_if_changed_on_disk(self) -> None:
        """config.json edited elsewhere (CLI brightness command, by hand): show those
        values instead of later saving stale ones over them."""
        mtime = self._read_mtime()
        if mtime == self._config_mtime or self._save_job:
            return
        self._config_mtime = mtime
        try:
            fresh = load_config()
        except (ConfigError, ValueError):
            return
        lights_changed = fresh.lights != self.cfg.lights
        self.cfg = fresh
        self._loading = True
        try:
            if lights_changed:
                self._build_lights_card()
            self.brightness.set(fresh.brightness)
            self.boost.set(fresh.min_saturation)
            self.fade.set(fresh.fade_seconds)
            self.mode.set(fresh.color_mode)
            self.auto_on.set(fresh.auto_turn_on)
            self.idle_action.set(fresh.idle_action)
            self.idle_after.set(int(fresh.idle_after))
            self.autostart.set(fresh.start_at_login)
            self.sync_var.set(fresh.sync_enabled)
            self.client_id.set(_blank_placeholder(fresh.spotify_client_id))
            self.client_secret.set(_blank_placeholder(fresh.spotify_client_secret))
            self.neutral_well.set_color(fresh.neutral_color)
            self.idle_well.set_color(fresh.idle_color)
            self._refresh_lights_text()
        finally:
            self._loading = False

    def _pick_color(self, attr: str) -> None:
        current = w.to_hex(getattr(self.cfg, attr))
        rgb, _ = colorchooser.askcolor(color=current, parent=self.root)
        if not rgb:
            return
        self.cfg = replace(self.cfg, **{attr: tuple(int(c) for c in rgb)})
        (self.neutral_well if attr == "neutral_color" else self.idle_well).set_color(getattr(self.cfg, attr))
        self._changed()

    def _brightness_released(self) -> None:
        if not self.cfg.lights:
            return
        lights, level, fade = list(self.cfg.lights), self.cfg.brightness, self.cfg.fade_seconds

        def work():
            from .lights import LightGroup

            LightGroup(lights, fade_seconds=fade, auto_turn_on=False).set_brightness(level)

        self._background(work)

    # ---------- actions ----------

    def _ready(self, quiet: bool = False) -> bool:
        try:
            self.cfg.validate()
            return True
        except ConfigError as exc:
            if not quiet:
                messagebox.showinfo("MusicMatch", str(exc).split(" (")[0])
            return False

    def _sync_toggled(self) -> None:
        if self.sync_var.get() and not self._ready():
            self.sync_var.set(False)
            return
        self._changed()
        svc.start_background()

    def _toggle_background(self) -> None:
        if self._bg_running:
            svc.stop_background()
            self._set_status("Stopping background...")
        else:
            svc.start_background()
            self._set_status("Starting background...")

    def _login_toggled(self) -> None:
        self._changed()
        enabled = bool(self.autostart.get())
        self._background(lambda: autostart.set_enabled(enabled))

    def _sync_login_item(self) -> None:
        """Keep the OS login item matching the saved setting (e.g. after moving the folder)."""
        if autostart.supported() and self.cfg.start_at_login:
            self._background(lambda: autostart.set_enabled(True))

    def _toggle_keys(self) -> None:
        if self.keys_card.winfo_ismapped():
            self.keys_card.pack_forget()
        else:
            self.keys_card.pack()

    def _discover(self) -> None:
        if self._scanning:
            return
        self._scanning = True
        self._build_lights_card()
        self._set_status(f"Searching your network for {', '.join(devices.BRANDS)} lights...")
        keys = dict(self.cfg.hue_keys)

        self._background(lambda: self.events.put(("lights", devices.discover(timeout=3, hue_keys=keys))),
                         error_kind="lights_error")

    def _lights_found(self, found: list[dict], unpaired: list[dict]) -> None:
        self._scanning = False
        seen = {devices.identity(e) for e in found}
        before = {devices.identity(devices.normalize(e)) for e in self.cfg.lights}
        merged = [e for e in devices.merge(self.cfg.lights, found)
                  if e.get("enabled", True) or devices.identity(e) in seen]  # forget gone + unwanted
        self.cfg = replace(self.cfg, lights=merged)
        self._build_lights_card()
        self._refresh_lights_text()
        self._changed()
        new = len(seen - before)
        if unpaired:
            self._pair_hue(unpaired)
        elif found:
            self._set_status(f"Found {len(found)} light(s)" + (f", {new} new." if new else "."))
        else:
            self._set_status("No lights found. Is this computer on the same Wi-Fi? Govee and Yeelight "
                             "need LAN Control switched on in their apps.")

    def _pair_hue(self, bridges: list[dict]) -> None:
        self._set_status("Found a Philips Hue bridge: press the round button on top of it now...")

        def work():
            deadline = time.monotonic() + HUE_PAIR_SECONDS
            waiting = list(bridges)
            while waiting and time.monotonic() < deadline:
                for bridge in list(waiting):
                    key = devices.hue.pair(bridge["host"])
                    if key:
                        lights = devices.hue.lights_on_bridge(bridge["bridge"], bridge["host"], key)
                        self.events.put(("hue_paired", (bridge["bridge"], key, lights)))
                        waiting.remove(bridge)
                time.sleep(1)
            if waiting:
                self.events.put(("log", "The Hue bridge wasn't paired (button not pressed in time). "
                                        "Click Search again to retry."))

        self._background(work)

    def _hue_paired(self, bridge: str, key: str, lights: list[dict]) -> None:
        self.cfg = replace(self.cfg, hue_keys={**self.cfg.hue_keys, bridge: key},
                           lights=devices.merge(self.cfg.lights, lights))
        self._build_lights_card()
        self._refresh_lights_text()
        self._changed()
        self._set_status(f"Paired with Philips Hue: {len(lights)} colour light(s) added.")

    def _test_lights(self) -> None:
        if not self.cfg.lights:
            messagebox.showinfo("MusicMatch", "No lights yet. Click Find first.")
            return
        lights = list(self.cfg.lights)
        self._set_status("Flashing red, green, blue...")

        def work():
            from .lights import LightGroup

            LightGroup(lights, fade_seconds=0, auto_turn_on=True).identify()
            self.events.put(("log", "Test done."))

        self._background(work)

    def _connect_spotify(self) -> None:
        self._changed()
        if not self.cfg.spotify_client_id or not self.cfg.spotify_client_secret:
            messagebox.showinfo(
                "MusicMatch",
                "Paste your Spotify Client ID and secret first.\n\n"
                "Click 'Create an app on Spotify', choose Web API, set the redirect URI to "
                f"{self.cfg.spotify_redirect_uri}, save, then copy the Client ID and secret here.",
            )
            return
        self._save()
        TOKEN_CACHE_PATH.unlink(missing_ok=True)
        self.card_text(self.spotify_text, "Click Agree in your browser...", w.TEXT_2)
        cfg = self.cfg

        def work():
            from .spotify_client import SpotifyWatcher

            self.events.put(("spotify", SpotifyWatcher(cfg).whoami()))

        self._background(work, error_kind="spotify_error")

    def _check_spotify_login(self) -> None:
        if not (self.cfg.spotify_client_id and TOKEN_CACHE_PATH.exists()):
            return
        cfg = self.cfg

        def work():
            from .spotify_client import cached_login

            name = cached_login(cfg)
            if name:
                self.events.put(("spotify", name))

        self._background(work, error_kind="spotify_error")

    def _background(self, fn, error_kind: str = "error") -> None:
        def run():
            try:
                fn()
            except Exception as exc:
                self.events.put((error_kind, str(exc)))

        threading.Thread(target=run, daemon=True).start()

    # ---------- events from worker threads ----------

    def _drain_events(self) -> None:
        try:
            while True:
                kind, data = self.events.get_nowait()
                self._handle(kind, data)
        except queue.Empty:
            pass
        visible = self.root.state() == "normal"
        self._ticks += 1
        self._poll_background_if_changed()
        if not visible or self._ticks % 4 == 0:
            self._reload_if_changed_on_disk()
            if SHOW_PATH.exists():
                SHOW_PATH.unlink(missing_ok=True)
                self._show_window()
        self.root.after(TICK_MS if visible else HIDDEN_TICK_MS, self._drain_events)

    def _poll_background_if_changed(self) -> None:
        """The service rewrites its state file only when something changes, so one
        stat() per tick is enough; the full check (which also notices a crashed
        service) runs every few seconds."""
        try:
            st = svc.STATE_PATH.stat()
            sig = (st.st_mtime_ns, st.st_size)
        except OSError:
            sig = None
        now = time.monotonic()
        if sig != self._state_sig or now - self._state_checked > LIVENESS_CHECK_S:
            self._state_sig, self._state_checked = sig, now
            self._poll_background()

    def _show_window(self) -> None:
        self.root.deiconify()
        self.root.lift()
        self.root.focus_force()

    def _on_map(self, event) -> None:
        if event.widget is self.root:
            self._state_sig = None
            self._poll_background_if_changed()

    def _handle(self, kind: str, data) -> None:
        if kind in ("log", "error"):
            self._set_status(data)
        elif kind == "hero":
            key, color, cover, artwork = data
            if key == self._track_key and color == self._track_color:
                self._cover = cover
                self.hero.show(artwork)
        elif kind == "spotify":
            self.card_text(self.spotify_text, f"Connected as {data}", w.TEXT_2)
            if self.keys_card.winfo_ismapped():
                self._toggle_keys()
        elif kind == "spotify_error":
            self.card_text(self.spotify_text, "Not connected", "#e5735f")
            self._set_status(f"Spotify: {data}")
        elif kind == "lights":
            self._lights_found(*data)
        elif kind == "lights_error":
            self._scanning = False
            self._build_lights_card()
            self._set_status(f"Couldn't search for lights: {data}")
        elif kind == "hue_paired":
            self._hue_paired(*data)

    def card_text(self, item, text: str, color: str) -> None:
        self._setup_card.itemconfig(item, text=text, fill=color)

    def _refresh_lights_text(self) -> None:
        self._setup_card.itemconfig(self.lights_text, text=self._lights_text())

    def _lights_text(self) -> str:
        if not self.cfg.lights:
            return "None yet"
        on = len(self.cfg.enabled_lights())
        return f"{on} syncing" if on else "All switched off"

    def _set_status(self, text: str) -> None:
        self.status.config(text=text if len(text) < 120 else text[:117] + "...")

    def _quit(self) -> None:
        if self._save_job:
            self.root.after_cancel(self._save_job)
            self._save()
        self.root.destroy()  # the background service keeps syncing


def _style_title_bar(root: tk.Tk) -> None:
    """Dark native title bar so the window reads as one piece."""
    try:
        if sys.platform == "win32":
            import ctypes

            root.update_idletasks()
            hwnd = ctypes.windll.user32.GetParent(root.winfo_id())
            dark = ctypes.c_int(1)
            for attr in (20, 19):  # DWMWA_USE_IMMERSIVE_DARK_MODE (new / old builds)
                if ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, attr, ctypes.byref(dark), 4) == 0:
                    break
            r, g, b = w.to_rgb(w.BG)
            caption = ctypes.c_int(int(r) | int(g) << 8 | int(b) << 16)
            ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, 35, ctypes.byref(caption), 4)  # caption color
        elif sys.platform == "darwin":
            root.tk.call("::tk::unsupported::MacWindowStyle", "appearance", root._w, "darkaqua")
    except Exception:
        pass


def _set_icon(root: tk.Tk) -> None:
    root._icons = [ImageTk.PhotoImage(logo.app_icon(px)) for px in (256, 64, 48, 32, 16)]  # type: ignore[attr-defined]
    root.iconphoto(True, *root._icons)  # type: ignore[attr-defined]
    if sys.platform == "win32":
        try:
            _set_taskbar_identity(root)
        except Exception as exc:
            log.debug("Taskbar identity not set: %s", exc)


def _set_taskbar_identity(root: tk.Tk) -> None:
    """Give the window its own taskbar entry and icon. Python from the Microsoft Store
    is a packaged app, so Windows would otherwise group the window under Python and
    ignore SetCurrentProcessExplicitAppUserModelID; a per-window property still works."""
    import ctypes
    from ctypes import wintypes

    class GUID(ctypes.Structure):
        _fields_ = [("d1", wintypes.DWORD), ("d2", wintypes.WORD), ("d3", wintypes.WORD), ("d4", ctypes.c_ubyte * 8)]

        def __init__(self, text: str):
            import uuid

            u = uuid.UUID(text)
            super().__init__(u.time_low, u.time_mid, u.time_hi_version, (ctypes.c_ubyte * 8)(*u.bytes[8:]))

    class PROPERTYKEY(ctypes.Structure):
        _fields_ = [("fmtid", GUID), ("pid", wintypes.DWORD)]

    class PROPVARIANT(ctypes.Structure):
        _fields_ = [("vt", ctypes.c_ushort), ("r1", ctypes.c_ushort), ("r2", ctypes.c_ushort),
                    ("r3", ctypes.c_ushort), ("val", ctypes.c_void_p), ("pad", ctypes.c_void_p)]

    root.update_idletasks()
    hwnd = ctypes.windll.user32.GetParent(root.winfo_id())
    store = ctypes.c_void_p()
    iid = GUID("886d8eeb-8cf2-4446-8d02-cdba1dbdcf99")  # IID_IPropertyStore
    hr = ctypes.windll.shell32.SHGetPropertyStoreForWindow(hwnd, ctypes.byref(iid), ctypes.byref(store))
    if hr != 0:
        raise OSError(hr)
    vtable = ctypes.cast(ctypes.cast(store, ctypes.POINTER(ctypes.c_void_p))[0], ctypes.POINTER(ctypes.c_void_p))
    proto = ctypes.WINFUNCTYPE
    set_value = proto(ctypes.HRESULT, ctypes.c_void_p, ctypes.POINTER(PROPERTYKEY), ctypes.POINTER(PROPVARIANT))(vtable[6])
    commit = proto(ctypes.HRESULT, ctypes.c_void_p)(vtable[7])
    release = proto(ctypes.c_ulong, ctypes.c_void_p)(vtable[2])

    fmtid = GUID("9f4c2855-9f79-4b39-a8d0-e1d42de1d5f3")  # PKEY_AppUserModel_*
    values = {
        5: "MusicMatch.App",  # ID
        2: subprocess.list2cmdline(svc.command("window")),  # RelaunchCommand
        3: str(RESOURCES / "assets" / "MusicMatch.ico") + ",0",  # RelaunchIconResource
        4: "MusicMatch",  # RelaunchDisplayNameResource
    }
    keep = []
    try:
        for pid, text in values.items():
            buf = ctypes.create_unicode_buffer(text)
            keep.append(buf)
            pv = PROPVARIANT(vt=31, val=ctypes.cast(buf, ctypes.c_void_p))  # VT_LPWSTR
            set_value(store, ctypes.byref(PROPERTYKEY(fmtid, pid)), ctypes.byref(pv))
        commit(store)
    finally:
        release(store)


def main() -> None:
    from .app import AlreadyRunning, _acquire_instance_lock

    try:
        window_lock = _acquire_instance_lock(GUI_LOCK_PATH)
    except AlreadyRunning:
        SHOW_PATH.touch()  # the open window brings itself to the front
        return
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s  %(message)s",
        filename=str(LOG_PATH),
    )
    for noisy in ("flux_led", "urllib3", "spotipy"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    if sys.platform == "win32":
        try:
            import ctypes

            ctypes.windll.shcore.SetProcessDpiAwareness(1)  # otherwise Windows bitmap-scales the window
            # Own taskbar identity, so it shows the MusicMatch icon rather than Python's.
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("MusicMatch.App")
        except Exception:
            pass
    root = tk.Tk()
    App(root)
    root.mainloop()
    window_lock.close()


if __name__ == "__main__":
    main()
