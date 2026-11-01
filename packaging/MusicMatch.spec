# PyInstaller recipe for the downloadable app. From the repo root:
#   pip install -r requirements.txt -r packaging/requirements-build.txt
#   pyinstaller packaging/MusicMatch.spec --noconfirm
# Windows -> dist/MusicMatch/MusicMatch.exe, macOS -> dist/MusicMatch.app
import sys
from pathlib import Path

ROOT = Path(SPECPATH).parent
VERSION = (ROOT / "VERSION").read_text().strip()
ICON = ROOT / "assets" / ("MusicMatch.icns" if sys.platform == "darwin" else "MusicMatch.ico")
TRAY_BACKEND = "pystray._darwin" if sys.platform == "darwin" else "pystray._win32"

a = Analysis(
    [str(ROOT / "packaging" / "musicmatch_app.py")],
    pathex=[str(ROOT)],
    datas=[(str(ROOT / "assets"), "assets")],
    hiddenimports=[TRAY_BACKEND, "PIL._tkinter_finder"],
    excludes=["pytest"],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="MusicMatch",
    console=False,
    icon=str(ICON),
)
coll = COLLECT(exe, a.binaries, a.datas, name="MusicMatch")

if sys.platform == "darwin":
    app = BUNDLE(
        coll,
        name="MusicMatch.app",
        icon=str(ICON),
        bundle_identifier="com.musicmatch.app",
        version=VERSION,
        info_plist={
            "CFBundleDisplayName": "MusicMatch",
            "CFBundleShortVersionString": VERSION,
            "NSHighResolutionCapable": True,
            # Menu-bar app: the note lives in the top bar (like 1Password). The window
            # still opens from that icon; the Dock stays clear while only the service runs.
            "LSUIElement": True,
            # macOS asks before an app may talk to devices on the home network.
            "NSLocalNetworkUsageDescription": "MusicMatch finds and controls the smart lights on your network.",
        },
    )
