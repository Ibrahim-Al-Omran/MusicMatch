"""Start at login, and on Windows optionally add a Start-menu shortcut.

The downloadable Windows build is a portable ZIP. It must not look like a stealth
installer: no HKCU Run-key writes, no Apps & Features uninstall registration, and
no PowerShell ExecutionPolicy Bypass. Start-at-login uses a Startup-folder
shortcut the user can see and remove.
"""

from __future__ import annotations

import os
import plistlib
import subprocess
import sys
from pathlib import Path

from .config import DATA_DIR, FROZEN, RESOURCES, ROOT
from .service import command, python_for_background

APP_NAME = "MusicMatch"
LAUNCHER = ROOT / "background.pyw"
# Legacy Run-key path from older builds; cleared on disable/uninstall, never written.
_LEGACY_RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
_LEGACY_UNINSTALL_KEY = r"Software\Microsoft\Windows\CurrentVersion\Uninstall\MusicMatch"
PLIST_PATH = Path.home() / "Library" / "LaunchAgents" / "com.musicmatch.background.plist"


def icon_file() -> Path:
    return RESOURCES / "assets" / "MusicMatch.ico"


def _appdata() -> Path:
    return Path(os.environ.get("APPDATA") or Path.home() / "AppData" / "Roaming")


def start_menu_shortcut() -> Path:
    return _appdata() / "Microsoft" / "Windows" / "Start Menu" / "Programs" / f"{APP_NAME}.lnk"


def startup_shortcut() -> Path:
    return _appdata() / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup" / f"{APP_NAME}.lnk"


def supported() -> bool:
    return sys.platform in ("win32", "darwin")


def _command() -> list[str]:
    if FROZEN:
        return command("background")
    # A login item has no working directory, so `-m musicmatch` can't be found; Python
    # puts a script's own folder on sys.path, and this one sits next to the package.
    return [python_for_background(), str(LAUNCHER)]


def _windows_launch(role: str = "window") -> tuple[str, str, str, str]:
    argv = command(role) if role == "window" else _command()
    target, args = argv[0], subprocess.list2cmdline(argv[1:])
    workdir = str(ROOT if not FROZEN else Path(sys.executable).parent)
    return target, args, workdir, str(icon_file())


def is_enabled() -> bool:
    if sys.platform == "win32":
        return startup_shortcut().is_file() or _legacy_run_key_present()
    if sys.platform == "darwin":
        return PLIST_PATH.exists()
    return False


def set_enabled(enabled: bool) -> None:
    if sys.platform == "win32":
        if enabled:
            target, args, workdir, icon = _windows_launch("background")
            path = startup_shortcut()
            if not path.is_file() or _shortcut_target(path) != (target, args):
                _write_shortcut(path, target, args, workdir, icon)
            _clear_legacy_run_key()
        else:
            startup_shortcut().unlink(missing_ok=True)
            _clear_legacy_run_key()
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


def install_windows_app() -> None:
    """Put MusicMatch in the Start menu with the app icon (portable; no Apps list entry)."""
    if sys.platform != "win32":
        return
    target, args, workdir, icon = _windows_launch("window")
    for path in (start_menu_shortcut(), ROOT / f"{APP_NAME}.lnk"):
        if path.is_file() and _shortcut_target(path) == (target, args):
            continue
        _write_shortcut(path, target, args, workdir, icon)


def uninstall_windows_app() -> None:
    if sys.platform != "win32":
        return
    start_menu_shortcut().unlink(missing_ok=True)
    startup_shortcut().unlink(missing_ok=True)
    (ROOT / f"{APP_NAME}.lnk").unlink(missing_ok=True)
    _clear_legacy_run_key()
    _clear_legacy_uninstall_key()


def _legacy_run_key_present() -> bool:
    import winreg

    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _LEGACY_RUN_KEY) as key:
            winreg.QueryValueEx(key, APP_NAME)
        return True
    except OSError:
        return False


def _clear_legacy_run_key() -> None:
    import winreg

    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _LEGACY_RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
            winreg.DeleteValue(key, APP_NAME)
    except FileNotFoundError:
        pass
    except OSError:
        pass


def _clear_legacy_uninstall_key() -> None:
    import winreg

    try:
        winreg.DeleteKey(winreg.HKEY_CURRENT_USER, _LEGACY_UNINSTALL_KEY)
    except FileNotFoundError:
        pass
    except OSError:
        pass


def _shortcut_target(path: Path) -> tuple[str, str] | None:
    """Return (target, arguments) for an existing .lnk, or None if unreadable."""
    q = str(path).replace("'", "''")
    script = (
        f"$s = (New-Object -ComObject WScript.Shell).CreateShortcut('{q}'); "
        f"Write-Output $s.TargetPath; Write-Output $s.Arguments"
    )
    try:
        result = subprocess.run(
            ["powershell", "-NoProfile", "-Command", script],
            capture_output=True, text=True, timeout=10, check=False,
        )
    except (OSError, subprocess.SubprocessError, TimeoutError):
        return None
    if result.returncode != 0:
        return None
    lines = [line.rstrip("\r") for line in result.stdout.splitlines()]
    if not lines:
        return None
    return lines[0], (lines[1] if len(lines) > 1 else "")


def _write_shortcut(path: Path, target: str, arguments: str, workdir: str, icon: str) -> None:
    """Create a .lnk with WScript.Shell. Avoids ExecutionPolicy Bypass."""
    path.parent.mkdir(parents=True, exist_ok=True)
    q = lambda s: "'" + s.replace("'", "''") + "'"  # noqa: E731
    script = (
        f"$s = (New-Object -ComObject WScript.Shell).CreateShortcut({q(str(path))}); "
        f"$s.TargetPath = {q(target)}; $s.Arguments = {q(arguments)}; "
        f"$s.WorkingDirectory = {q(workdir)}; $s.IconLocation = {q(icon + ',0')}; "
        f"$s.Description = '{APP_NAME}'; $s.Save()"
    )
    subprocess.run(
        ["powershell", "-NoProfile", "-Command", script],
        check=False, capture_output=True, timeout=15,
    )
