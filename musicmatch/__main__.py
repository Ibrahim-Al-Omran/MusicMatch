from __future__ import annotations

import argparse
import logging
import sys

from .config import ConfigError, load_config, save_lights, save_setting


def cmd_discover(args) -> int:
    from . import devices

    try:
        cfg = load_config()
    except ConfigError:
        cfg = None
    print(f"Scanning your network for {', '.join(devices.BRANDS)} lights...")
    found, unpaired = devices.discover(timeout=args.timeout, hue_keys=cfg.hue_keys if cfg else {})
    for bridge in unpaired:
        print(f"  Philips Hue bridge at {bridge['host']}: pair it from the app (Search again, then press its button)")
    if not found:
        print(
            "No lights found. Make sure this computer is on the same Wi-Fi as the lights "
            "(not a guest network). Govee and Yeelight need LAN Control switched on in their apps."
        )
        return 1
    for entry in found:
        print(f"  {devices.DRIVERS[entry['type']].brand:<12} {entry['host']:<16} {entry.get('name', '')}")
    if args.save:
        merged = devices.merge(cfg.lights if cfg else [], found)
        save_lights(merged)
        print(f"Saved {len(merged)} light(s) to config.json")
    return 0


def cmd_auth(args) -> int:
    from .spotify_client import SpotifyWatcher

    cfg = load_config()
    cfg.validate(need_lights=False)
    print(f"Logged in to Spotify as {SpotifyWatcher(cfg).whoami()}. Token cached.")
    return 0


def cmd_test(args) -> int:
    from .lights import LightGroup

    cfg = load_config()
    cfg.validate(need_spotify=False)
    print("Flashing red, green, blue...")
    LightGroup(cfg.lights, fade_seconds=0, auto_turn_on=True).identify()
    print("Done. If nothing changed, check the IPs in config.json against `discover`.")
    return 0


def cmd_preview(args) -> int:
    from .color import led_color_for_image, load_image, palette, pick_swatch

    try:
        cfg = load_config()
    except ConfigError:
        from .config import Config

        cfg = Config()
    data = load_image(args.image)
    swatches = palette(data)
    chosen = pick_swatch(swatches, cfg.color_mode)
    print(f"Palette ({cfg.color_mode} mode):")
    for s in swatches:
        mark = "  <== picked" if s is chosen else ""
        print(f"  {s.hex}  {s.share:6.1%}  sat={s.saturation:.2f} val={s.value:.2f}{mark}")
    led, _ = led_color_for_image(
        data, cfg.color_mode, cfg.min_saturation, cfg.brightness, cfg.neutral_color
    )
    print(f"LED color: RGB{led}")
    if args.apply:
        from .lights import LightGroup

        cfg.validate(need_spotify=False)
        LightGroup(cfg.lights, cfg.fade_seconds, auto_turn_on=True).set_color(led)
    return 0


def cmd_brightness(args) -> int:
    from .lights import LightGroup

    cfg = load_config()
    if args.level is None:
        print(f"Brightness is {cfg.brightness}%")
        return 0

    text = args.level.strip().rstrip("%")
    try:
        amount = int(text)
    except ValueError:
        print("Brightness must be a number like 50, +10 or -10", file=sys.stderr)
        return 2
    new = cfg.brightness + amount if text[0] in "+-" else amount
    new = max(1, min(100, new))

    save_setting("brightness", new)
    print(f"Brightness {cfg.brightness}% -> {new}%")
    if cfg.lights:
        LightGroup(cfg.lights, cfg.fade_seconds, auto_turn_on=False).set_brightness(new)
    return 0


def cmd_preload(args) -> int:
    from .app import warm_colors
    from .spotify_client import SpotifyWatcher

    cfg = load_config()
    cfg.validate(need_lights=False)
    watcher = SpotifyWatcher(cfg, open_browser=False)
    items = []
    now = watcher.now_playing()
    if now:
        items.append(now)
    items.extend(watcher.upcoming(args.limit))
    seen, unique = set(), []
    for item in items:
        if item.item_id in seen:
            continue
        seen.add(item.item_id)
        unique.append(item)
    if not unique:
        print("Nothing playing and the queue is empty. Start a song, then try again.")
        return 1
    print(f"Preparing colors for {len(unique)} song(s)...")
    for item, color, desc, already in warm_colors(cfg, unique):
        mark = "already ready" if already else "prepared"
        print(f"  {item.artist} - {item.title}  -> RGB{color} ({desc})  [{mark}]")
    print("Ready. Song changes will use these colors instantly.")
    return 0


def cmd_uninstall(args) -> int:
    from .autostart import uninstall_windows_app

    uninstall_windows_app()
    print("Removed MusicMatch from the Start menu and Apps list. Your project folder is unchanged.")
    return 0


def cmd_service(args) -> int:
    from .service import main as service_main

    return service_main()


def cmd_gui(args) -> int:
    from .gui import main as gui_main

    gui_main()
    return 0


def cmd_run(args) -> int:
    from .app import AlreadyRunning, MusicMatch

    cfg = load_config()
    cfg.validate()
    try:
        MusicMatch(cfg).run()
    except AlreadyRunning as exc:
        print(exc, file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\nStopped.")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="musicmatch",
        description="Sync your smart lights to Spotify album art.",
    )
    parser.add_argument("-v", "--verbose", action="store_true")
    parser.add_argument("--log-file", help="write logs to a file (needed when run via pythonw)")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("discover", help="find lights on your network (all supported brands)")
    p.add_argument("--save", action="store_true", help="add the lights found to config.json")
    p.add_argument("--timeout", type=float, default=3)
    p.set_defaults(func=cmd_discover)

    p = sub.add_parser("auth", help="log in to Spotify (one time)")
    p.set_defaults(func=cmd_auth)

    p = sub.add_parser("test", help="flash red/green/blue on your lights")
    p.set_defaults(func=cmd_test)

    p = sub.add_parser("preview", help="show which color an image would produce")
    p.add_argument("image", help="path or URL of an image")
    p.add_argument("--apply", action="store_true", help="also send it to the lights")
    p.set_defaults(func=cmd_preview)

    p = sub.add_parser("brightness", help="show or change brightness (applies right away)")
    p.add_argument("level", nargs="?", help="1-100, or +10 / -10 to nudge it")
    p.set_defaults(func=cmd_brightness)

    p = sub.add_parser("preload", help="prepare colors for the current song and queue")
    p.add_argument("--limit", type=int, default=12, help="how many upcoming songs to prepare")
    p.set_defaults(func=cmd_preload)

    p = sub.add_parser("uninstall", help="remove the Windows Start menu shortcut and Apps entry")
    p.set_defaults(func=cmd_uninstall)

    p = sub.add_parser("service", help="run in the background with a tray / menu-bar icon")
    p.set_defaults(func=cmd_service)

    p = sub.add_parser("gui", help="open the desktop app")
    p.set_defaults(func=cmd_gui)

    p = sub.add_parser("run", help="start syncing lights to Spotify")
    p.set_defaults(func=cmd_run)

    args = parser.parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s  %(message)s",
        datefmt="%H:%M:%S",
        filename=args.log_file,
    )
    for noisy in ("flux_led", "urllib3", "spotipy"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    try:
        return args.func(args)
    except ConfigError as exc:
        print(f"Config problem: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
