import os
import runpy
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from musicmatch import config, service


ROOT = Path(__file__).resolve().parent.parent


class PackagedSettingsTests(unittest.TestCase):
    def test_source_settings_stay_in_checkout(self):
        with mock.patch.object(config, "FROZEN", False):
            self.assertEqual(config._data_dir(), config.ROOT)

    def test_packaged_settings_are_stable_across_install_locations(self):
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            for platform, expected in (
                ("win32", base / "MusicMatch"),
                ("darwin", base / "Library" / "Application Support" / "MusicMatch"),
            ):
                for installation in (base / "old-install", base / "new-install"):
                    with self.subTest(platform=platform, installation=installation), \
                         mock.patch.object(config, "FROZEN", True), \
                         mock.patch.object(config, "ROOT", installation), \
                         mock.patch.object(config.sys, "platform", platform), \
                         mock.patch.dict(os.environ, {"APPDATA": str(base)}), \
                         mock.patch.object(config.Path, "home", return_value=base):
                        self.assertEqual(config._data_dir(), expected)
                        self.assertTrue(expected.is_dir())
                        path = expected / "config.json"
                        if not path.exists():
                            config.save_config(config.Config(brightness=100, min_saturation=0.5), path)
                        self.assertEqual(config.load_config(path).brightness, 100)
                        self.assertEqual(config.load_config(path).min_saturation, 0.5)

    def test_packaged_background_uses_same_executable_as_window(self):
        executable = str(ROOT / "dist" / "MusicMatch" / "MusicMatch.exe")
        with mock.patch.object(service, "FROZEN", True), \
             mock.patch.object(service.sys, "executable", executable):
            self.assertEqual(service.command("window"), [executable])
            self.assertEqual(service.command("background"), [executable, "--background"])


class BuildRecipeTests(unittest.TestCase):
    def test_both_bundles_include_code_and_assets_not_personal_settings(self):
        for platform in ("win32", "darwin"):
            with self.subTest(platform=platform), mock.patch("sys.platform", platform):
                analysis = mock.Mock(return_value=mock.Mock())
                exe = mock.Mock()
                runpy.run_path(str(ROOT / "packaging" / "MusicMatch.spec"), init_globals={
                    "SPECPATH": str(ROOT / "packaging"),
                    "Analysis": analysis,
                    "PYZ": mock.Mock(),
                    "EXE": exe,
                    "COLLECT": mock.Mock(),
                    "BUNDLE": mock.Mock(),
                })
                self.assertEqual(analysis.call_args.args[0], [str(ROOT / "packaging" / "musicmatch_app.py")])
                self.assertEqual(analysis.call_args.kwargs["pathex"], [str(ROOT)])
                self.assertEqual(analysis.call_args.kwargs["datas"], [(str(ROOT / "assets"), "assets")])
                if platform == "win32":
                    self.assertIn("version", exe.call_args.kwargs)
                    version_path = Path(exe.call_args.kwargs["version"])
                    self.assertTrue(version_path.is_file())
                    text = version_path.read_text(encoding="utf-8")
                    self.assertIn("ProductName", text)
                    self.assertIn("MusicMatch", text)
                else:
                    self.assertNotIn("version", exe.call_args.kwargs)


if __name__ == "__main__":
    unittest.main()
