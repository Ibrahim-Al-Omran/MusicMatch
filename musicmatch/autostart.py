"""Start at login, and on Windows register MusicMatch as a real Start-menu app."""

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
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
UNINSTALL_KEY = r"Software\Microsoft\Windows\CurrentVersion\Uninstall\MusicMatch"
PLIST_PATH = Path.home() / "Library" / "LaunchAgents" / "com.musicmatch.background.plist"


def icon_file() -> Path:
    return RESOURCES / "assets" / "MusicMatch.ico"


def start_menu_shortcut() -> Path:
    base = Path(os.environ.get("APPDATA") or Path.home() / "AppData" / "Roaming")
    return base / "Microsoft" / "Windows" / "Start Menu" / "Programs" / f"{APP_NAME}.lnk"


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


def install_windows_app() -> None:
    """Put MusicMatch in the Start menu and Settings → Apps, with the app icon."""
    if sys.platform != "win32":
        return
    window = command("window")
    target, args = window[0], subprocess.list2cmdline(window[1:])
    workdir = str(ROOT if not FROZEN else Path(sys.executable).parent)
    icon = str(icon_file())
    for path in (start_menu_shortcut(), ROOT / f"{APP_NAME}.lnk"):
        _write_shortcut(path, target, args, workdir, icon)
    _register_uninstall(target, args, workdir, icon)


def uninstall_windows_app() -> None:
    if sys.platform != "win32":
        return
    start_menu_shortcut().unlink(missing_ok=True)
    (ROOT / f"{APP_NAME}.lnk").unlink(missing_ok=True)
    import winreg

    try:
        winreg.DeleteKey(winreg.HKEY_CURRENT_USER, UNINSTALL_KEY)
    except FileNotFoundError:
        pass
    set_enabled(False)


def _write_shortcut(path: Path, target: str, arguments: str, workdir: str, icon: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    q = lambda s: "'" + s.replace("'", "''") + "'"  # noqa: E731
    script = (
        f"$s = (New-Object -ComObject WScript.Shell).CreateShortcut({q(str(path))}); "
        f"$s.TargetPath = {q(target)}; $s.Arguments = {q(arguments)}; "
        f"$s.WorkingDirectory = {q(workdir)}; $s.IconLocation = {q(icon + ',0')}; "
        f"$s.Description = '{APP_NAME}'; $s.Save()"
    )
    subprocess.run(
        ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", script],
        check=False, capture_output=True,
    )


def _register_uninstall(target: str, arguments: str, workdir: str, icon: str) -> None:
    import winreg

    from musicmatch import __version__

    uninstall = subprocess.list2cmdline(
        [sys.executable.replace("pythonw.exe", "python.exe"), "-m", "musicmatch", "uninstall"]
        if not FROZEN else [sys.executable, "uninstall"]
    )
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, UNINSTALL_KEY) as key:
        winreg.SetValueEx(key, "DisplayName", 0, winreg.REG_SZ, APP_NAME)
        winreg.SetValueEx(key, "DisplayIcon", 0, winreg.REG_SZ, icon)
        winreg.SetValueEx(key, "Publisher", 0, winreg.REG_SZ, APP_NAME)
        winreg.SetValueEx(key, "InstallLocation", 0, winreg.REG_SZ, workdir)
        winreg.SetValueEx(key, "DisplayVersion", 0, winreg.REG_SZ, __version__)
        winreg.SetValueEx(key, "UninstallString", 0, winreg.REG_SZ, uninstall)
        winreg.SetValueEx(key, "NoModify", 0, winreg.REG_DWORD, 1)
        winreg.SetValueEx(key, "NoRepair", 0, winreg.REG_DWORD, 1)
