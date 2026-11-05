"""Run light/colour regressions against bundled bytecode without starting the app."""

import argparse
import importlib.abc
import importlib.util
import os
import sys
import tempfile
import unittest
from pathlib import Path

from PyInstaller.archive.readers import CArchiveReader


class BundledMusicMatch(importlib.abc.MetaPathFinder, importlib.abc.Loader):
    def __init__(self, modules, resources):
        self.modules = modules
        self.resources = resources

    def find_spec(self, fullname, path=None, target=None):
        if (fullname == "musicmatch" or fullname.startswith("musicmatch.")) and fullname in self.modules.toc:
            return importlib.util.spec_from_loader(
                fullname, self, is_package=self.modules.toc[fullname][0] == 1,
            )

    def create_module(self, spec):
        return None

    def exec_module(self, module):
        module.__file__ = str(self.resources / (module.__name__.replace(".", "/") + ".py"))
        exec(self.modules.extract(module.__name__), module.__dict__)


def main():
    default = "dist/MusicMatch.app/Contents/MacOS/MusicMatch" if sys.platform == "darwin" else "dist/MusicMatch/MusicMatch.exe"
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("executable", nargs="?", default=default)
    executable = Path(parser.parse_args().executable).resolve()
    resources = executable.parent.parent / "Frameworks" if sys.platform == "darwin" else executable.parent / "_internal"
    bundle = executable.parents[2] if sys.platform == "darwin" else executable.parent
    archive = CArchiveReader(str(executable))
    modules = archive.open_embedded_archive("PYZ.pyz")
    private_files = {"config.json", ".spotify_token_cache", ".musicmatch_colors.json", ".musicmatch_state.json"}
    if any(path.name in private_files for path in bundle.rglob("*")):
        raise RuntimeError("Personal settings or tokens must not be included in the app bundle")
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    sys.meta_path.insert(0, BundledMusicMatch(modules, resources))
    sys.frozen = True
    sys._MEIPASS = str(resources)
    with tempfile.TemporaryDirectory() as temporary:
        os.environ.update(APPDATA=temporary, HOME=temporary, USERPROFILE=temporary)
        from musicmatch import app, config

        if not config.FROZEN or not config.DATA_DIR.is_relative_to(Path(temporary)):
            raise RuntimeError("Bundled tests must use isolated packaged-app settings")
        if not Path(app.__file__).is_relative_to(resources):
            raise RuntimeError("Tests must load application code from the built executable")
        suite = unittest.defaultTestLoader.loadTestsFromNames([
            "tests.test_app", "tests.test_devices", "tests.test_color", "tests.test_packaging",
        ])
        result = unittest.TextTestRunner(verbosity=1).run(suite)
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    raise SystemExit(main())
