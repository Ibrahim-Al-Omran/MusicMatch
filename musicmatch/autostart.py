"""Register the background service to start at login (Windows Run key / macOS LaunchAgent)."""

from __future__ import annotations

import plistlib
import subprocess
import sys
from pathlib import Path

from .config import DATA_DIR, FROZEN, ROOT
from .service import command, python_for_background

APP_NAME = "MusicMatch"
LAUNCHER = ROOT / "background.pyw"
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
PLIST_PATH = Path.home() / "Library" / "LaunchAgents" / "com.musicmatch.background.plist"


def supported() -> bool:
    return sys.platform in ("win32", "darwin")


def _command() -> list[str]:
    if FROZEN:
        return command("background")
    # A login item has no working directory, so `-m musicmatch` can't be found; Python
    # puts a script's own folder on sys.path, and this one sits next to the package.
    return [python_for_background(), str(LAUNCHER)]


def is_enabled() -> bool:
    if sys.platform == "win32":
        import winreg

        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
                winreg.QueryValueEx(key, APP_NAME)
            return True
        except OSError:
            return False
    if sys.platform == "darwin":
        return PLIST_PATH.exists()
    return False


def set_enabled(enabled: bool) -> None:
    if sys.platform == "win32":
        import winreg

        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
            if enabled:
                winreg.SetValueEx(key, APP_NAME, 0, winreg.REG_SZ, subprocess.list2cmdline(_command()))
            else:
                try:
                    winreg.DeleteValue(key, APP_NAME)
                except FileNotFoundError:
                    pass
    elif sys.platform == "darwin":
        if enabled:
            PLIST_PATH.parent.mkdir(parents=True, exist_ok=True)
            PLIST_PATH.write_bytes(plistlib.dumps({
                "Label": "com.musicmatch.background",
                "ProgramArguments": _command(),
                "WorkingDirectory": str(DATA_DIR),
                "RunAtLoad": True,
                # Restart after a crash, but not after Quit or "already running" (exit 0).
                "KeepAlive": {"SuccessfulExit": False},
                "ProcessType": "Interactive",
            }))
            subprocess.run(["launchctl", "load", "-w", str(PLIST_PATH)], capture_output=True)
        elif PLIST_PATH.exists():
            subprocess.run(["launchctl", "unload", "-w", str(PLIST_PATH)], capture_output=True)
            PLIST_PATH.unlink(missing_ok=True)
