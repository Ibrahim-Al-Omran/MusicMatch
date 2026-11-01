"""Background service: keeps the lights synced after the window is closed.

Shows a tray icon (Windows) / menu-bar icon (macOS) when pystray is available, and
talks to the window through two small files next to config.json:
  .musicmatch_state.json  written here: what's playing, status, last log line
  .musicmatch_quit        touched by the window to ask this process to exit
Settings changes arrive through config.json, which the engine already re-reads."""

from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

from PIL import Image

from . import logo
from .app import AlreadyRunning, MusicMatch, _acquire_instance_lock
from .config import DATA_DIR, FROZEN, LOG_PATH, ROOT, ConfigError, load_config, save_setting

log = logging.getLogger(__name__)

STATE_PATH = DATA_DIR / ".musicmatch_state.json"
QUIT_PATH = DATA_DIR / ".musicmatch_quit"
BACKGROUND_FLAG = "--background"
SETUP_RETRY_SECONDS = 3
DEFAULT_ICON_COLOR = (255, 159, 90)


# ---------- helpers shared with the window ----------

def read_state() -> dict | None:
    try:
        return json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def is_running() -> bool:
    try:
        lock = _acquire_instance_lock()
    except AlreadyRunning:
        return True
    lock.close()
    return False


def pid_alive(pid: int) -> bool:
    if sys.platform == "win32":
        import ctypes

        # os.kill(pid, 0) would *terminate* the process on Windows.
        handle = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid)  # QUERY_LIMITED_INFORMATION
        if not handle:
            return False
        code = ctypes.c_ulong()
        ctypes.windll.kernel32.GetExitCodeProcess(handle, ctypes.byref(code))
        ctypes.windll.kernel32.CloseHandle(handle)
        return code.value == 259  # STILL_ACTIVE
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


def background_state() -> dict | None:
    """Current state if the background service is running, else None. Never touches
    the lock, so it can't race a service that's starting up."""
    state = read_state()
    if not state or state.get("status") == "stopped" or not state.get("pid"):
        return None
    return state if pid_alive(int(state["pid"])) else None


def python_for_background() -> str:
    exe = Path(sys.executable)
    if sys.platform == "win32" and exe.with_name("pythonw.exe").exists():
        return str(exe.with_name("pythonw.exe"))
    return str(exe)


def command(role: str) -> list[str]:
    """How to start the window ("window") or the background service ("background"),
    for both a packaged app and a cloned repo."""
    if FROZEN:
        return [sys.executable] + ([BACKGROUND_FLAG] if role == "background" else [])
    py = python_for_background()
    return [py, "-m", "musicmatch.gui"] if role == "window" else [py, "-m", "musicmatch", "service"]


def spawn(role: str) -> None:
    """Start the window or the service fully detached, so it outlives whoever started it."""
    kwargs: dict = {
        "cwd": str(ROOT),
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
        "close_fds": True,
    }
    if sys.platform == "win32":
        kwargs["creationflags"] = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        kwargs["start_new_session"] = True
    subprocess.Popen(command(role), **kwargs)


def start_background() -> None:
    if not is_running():
        QUIT_PATH.unlink(missing_ok=True)
        spawn("background")


def stop_background() -> None:
    QUIT_PATH.touch()


def tray_image(color, size: int = 64, muted: bool = False) -> Image.Image:
    if sys.platform == "darwin":
        return logo.menu_bar_icon(max(size, 44))
    return logo.tray_icon(color, size, muted=muted)


# ---------- the service ----------

class _StateLogHandler(logging.Handler):
    def __init__(self, service: Service):
        super().__init__(logging.INFO)
        self.service = service

    def emit(self, record: logging.LogRecord) -> None:
        self.service.update_state(message=record.getMessage())


class Service:
    def __init__(self):
        self.stop = threading.Event()
        self.engine_stop = threading.Event()
        self.engine: MusicMatch | None = None
        self.icon = None
        self._icon_look = None
        self.state: dict = {"status": "starting", "message": "Starting..."}
        self._state_lock = threading.Lock()

    def run(self) -> int:
        # A few tries: the window briefly holds the lock while checking is_running().
        for attempt in range(4):
            try:
                lock = _acquire_instance_lock()
                break
            except AlreadyRunning:
                if attempt == 3:
                    return 0
                time.sleep(0.4)
        QUIT_PATH.unlink(missing_ok=True)
        logging.getLogger().addHandler(_StateLogHandler(self))
        self.update_state()
        threading.Thread(target=self._watch, daemon=True).start()
        worker = threading.Thread(target=self._work, daemon=True)
        worker.start()
        try:
            if not self._run_tray():
                while worker.is_alive() and not self.stop.is_set():
                    worker.join(0.5)
        finally:
            self.stop.set()
            self.engine_stop.set()
            self.update_state(status="stopped", message="Background stopped")
            lock.close()
        return 0

    def quit(self) -> None:
        log.info("Quitting background service")
        self.stop.set()
        self.engine_stop.set()
        if self.icon:
            self.icon.stop()

    # ----- state for the window and the tray icon -----

    def update_state(self, **changes) -> None:
        with self._state_lock:
            self.state.update(changes, pid=os.getpid(), updated=time.time())
            tmp = STATE_PATH.with_name(STATE_PATH.name + ".tmp")
            try:
                tmp.write_text(json.dumps(self.state), encoding="utf-8")
                os.replace(tmp, STATE_PATH)
            except OSError:
                pass
        self._refresh_icon()

    def _on_track(self, np, color) -> None:
        if np is None:
            paused = self.engine is not None and not self.engine.cfg.sync_enabled
            self.update_state(status="paused" if paused else "idle", title=None, artist=None,
                              album=None, device=None, image_url=None, color=None, item_id=None)
            return
        self.update_state(status="playing", title=np.title, artist=np.artist, album=np.album,
                          device=np.device, image_url=np.image_url, item_id=np.item_id,
                          color=list(color))

    # ----- background threads -----

    def _watch(self) -> None:
        """Quit requests from the window, and Spotify keys changing under a running engine."""
        while not self.stop.wait(0.5):
            if QUIT_PATH.exists():
                QUIT_PATH.unlink(missing_ok=True)
                self.quit()
                return
            engine = self.engine
            if engine is None:
                continue
            try:
                cfg = load_config()
            except (ConfigError, ValueError):
                continue
            if (cfg.spotify_client_id, cfg.spotify_client_secret) != (
                engine.cfg.spotify_client_id, engine.cfg.spotify_client_secret
            ):
                log.info("Spotify keys changed, reconnecting")
                self.engine_stop.set()

    def _work(self) -> None:
        from .spotify_client import cached_login

        while not self.stop.is_set():
            try:
                cfg = load_config()
                cfg.validate()
            except (ConfigError, ValueError) as exc:
                self.update_state(status="setup", message=str(exc))
                self.stop.wait(SETUP_RETRY_SECONDS)
                continue
            try:
                user = cached_login(cfg)
            except Exception as exc:
                user = None
                log.debug("login check failed: %s", exc)
            if not user:
                self.update_state(status="setup", message="Connect Spotify in the MusicMatch window")
                self.stop.wait(SETUP_RETRY_SECONDS)
                continue

            self.engine_stop.clear()
            try:
                self.engine = MusicMatch(cfg, on_track=self._on_track)
                self.update_state(status="paused" if not cfg.sync_enabled else "idle", user=user)
                self.engine.run(self.engine_stop, use_lock=False)
            except Exception as exc:
                log.warning("Sync error: %s", exc)
                self.update_state(status="error", message=str(exc))
                self.stop.wait(SETUP_RETRY_SECONDS)
            finally:
                self.engine = None

    # ----- tray / menu-bar icon -----

    def _run_tray(self) -> bool:
        try:
            if sys.platform == "darwin":
                _hide_dock_icon()
            import pystray
            if sys.platform == "darwin":
                _use_template_menu_bar_icon()
        except Exception as exc:
            log.info("No tray icon (%s); running headless", exc)
            return False

        item = pystray.MenuItem
        menu = pystray.Menu(
            item(lambda _i: self._headline(), None, enabled=False),
            pystray.Menu.SEPARATOR,
            item("Open MusicMatch", lambda: spawn("window"), default=True),
            item("Pause syncing", self._toggle_pause, checked=lambda _i: not self._sync_enabled()),
            pystray.Menu.SEPARATOR,
            item("Quit MusicMatch", lambda: self.quit()),
        )
        self.icon = pystray.Icon("MusicMatch", tray_image(DEFAULT_ICON_COLOR), "MusicMatch", menu)
        try:
            self.icon.run()
        except Exception as exc:
            log.warning("Tray icon failed (%s); running headless", exc)
            self.icon = None
            return False
        return True

    def _headline(self) -> str:
        s = self.state
        if s.get("status") == "playing":
            text = f"{s.get('title')} — {s.get('artist')}"
            return text if len(text) <= 60 else text[:57] + "..."
        return {
            "paused": "Paused",
            "idle": "Waiting for music",
            "setup": "Needs setup: open MusicMatch",
            "error": "Having trouble, retrying",
        }.get(s.get("status", ""), "Starting...")

    def _refresh_icon(self) -> None:
        icon = self.icon
        if icon is None:
            return
        try:
            look = (tuple(self.state.get("color") or DEFAULT_ICON_COLOR), self.state.get("status") == "paused")
            if look != self._icon_look:
                self._icon_look = look
                if sys.platform != "darwin":  # Mac uses a template glyph that follows the menu bar
                    icon.icon = tray_image(look[0], muted=look[1])
            icon.title = f"MusicMatch: {self._headline()}"[:120]
            icon.update_menu()
        except Exception as exc:
            log.debug("icon refresh failed: %s", exc)

    def _sync_enabled(self) -> bool:
        try:
            return load_config().sync_enabled
        except (ConfigError, ValueError):
            return True

    def _toggle_pause(self) -> None:
        save_setting("sync_enabled", not self._sync_enabled())


def _use_template_menu_bar_icon() -> None:
    """Make pystray's status item a template image so macOS tints it like 1Password."""
    import io

    import AppKit
    import Foundation
    from PIL import Image as PILImage
    from pystray import _darwin

    def _assert_image(self):
        thickness = float(self._status_bar.thickness() or 22)
        pixel = max(44, int(thickness * 2))
        source = self._icon.convert("RGBA").resize((pixel, pixel), PILImage.LANCZOS)
        buf = io.BytesIO()
        source.save(buf, "PNG")
        ns = AppKit.NSImage.alloc().initWithData_(Foundation.NSData(buf.getvalue()))
        if ns is None:
            return
        ns.setSize_((thickness, thickness))
        ns.setTemplate_(True)
        self._icon_image = ns
        self._status_item.button().setImage_(ns)

    _darwin.Icon._assert_image = _assert_image


def _hide_dock_icon() -> None:
    """Menu-bar-only on macOS: no Dock icon for the background process."""
    try:
        import AppKit

        AppKit.NSBundle.mainBundle().infoDictionary()["LSUIElement"] = "1"
        AppKit.NSApplication.sharedApplication().setActivationPolicy_(1)  # accessory
    except Exception:
        pass
    if FROZEN:
        _open_window_on_reopen()


_reopen_handler = None


def _open_window_on_reopen() -> None:
    """The packaged Mac app runs the window and this service from one bundle, so
    double-clicking MusicMatch.app while only the service runs "reopens" this
    process. Answer by opening the window."""
    global _reopen_handler
    try:
        import Foundation

        class ReopenHandler(Foundation.NSObject):
            def handleReopen_withReplyEvent_(self, _event, _reply):
                spawn("window")

        four = lambda code: int.from_bytes(code.encode(), "big")  # noqa: E731
        _reopen_handler = ReopenHandler.alloc().init()
        Foundation.NSAppleEventManager.sharedAppleEventManager().setEventHandler_andSelector_forEventClass_andEventID_(
            _reopen_handler, b"handleReopen:withReplyEvent:", four("aevt"), four("rapp"))
    except Exception as exc:
        log.debug("Reopen handler not installed: %s", exc)


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s  %(message)s",
        filename=str(LOG_PATH),
        force=True,  # `python -m musicmatch service` has already set up console logging
    )
    for noisy in ("flux_led", "urllib3", "spotipy", "PIL"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    return Service().run()


if __name__ == "__main__":
    sys.exit(main())
