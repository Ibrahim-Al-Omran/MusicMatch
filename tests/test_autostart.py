import tempfile
import unittest
from pathlib import Path
from unittest import mock

from musicmatch import autostart


class WindowsAutostartTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.base = Path(self.temporary.name)
        self.patches = [
            mock.patch.object(autostart.sys, "platform", "win32"),
            mock.patch.object(autostart, "FROZEN", True),
            mock.patch.object(autostart, "ROOT", self.base / "install"),
            mock.patch.dict("os.environ", {"APPDATA": str(self.base / "AppData")}),
            mock.patch.object(
                autostart,
                "command",
                side_effect=lambda role="window": (
                    [str(self.base / "install" / "MusicMatch.exe")]
                    if role == "window"
                    else [str(self.base / "install" / "MusicMatch.exe"), "--background"]
                ),
            ),
            mock.patch.object(autostart, "icon_file", return_value=self.base / "MusicMatch.ico"),
        ]
        for patcher in self.patches:
            patcher.start()
            self.addCleanup(patcher.stop)
        (self.base / "install").mkdir()

    def test_start_at_login_uses_startup_folder_not_run_key(self):
        written = []

        def fake_write(path, target, arguments, workdir, icon):
            written.append((path, target, arguments))
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("shortcut", encoding="utf-8")

        with mock.patch.object(autostart, "_write_shortcut", side_effect=fake_write), \
             mock.patch.object(autostart, "_shortcut_target", return_value=None), \
             mock.patch.object(autostart, "_clear_legacy_run_key") as clear_run, \
             mock.patch.object(autostart, "_legacy_run_key_present", return_value=False):
            autostart.set_enabled(True)

        self.assertEqual(len(written), 1)
        path, target, arguments = written[0]
        self.assertEqual(path, autostart.startup_shortcut())
        self.assertTrue(str(path).replace("\\", "/").endswith("Start Menu/Programs/Startup/MusicMatch.lnk"))
        self.assertTrue(target.endswith("MusicMatch.exe"))
        self.assertEqual(arguments, "--background")
        clear_run.assert_called_once_with()

    def test_set_enabled_false_removes_startup_and_legacy_run(self):
        path = autostart.startup_shortcut()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("shortcut", encoding="utf-8")
        with mock.patch.object(autostart, "_clear_legacy_run_key") as clear_run:
            autostart.set_enabled(False)
        self.assertFalse(path.exists())
        clear_run.assert_called_once_with()

    def test_install_windows_app_does_not_register_uninstall_key(self):
        written = []

        def fake_write(path, target, arguments, workdir, icon):
            written.append(path)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("shortcut", encoding="utf-8")

        with mock.patch.object(autostart, "_write_shortcut", side_effect=fake_write), \
             mock.patch.object(autostart, "_shortcut_target", return_value=None), \
             mock.patch("winreg.CreateKey") as create_key, \
             mock.patch("winreg.SetValueEx") as set_value:
            autostart.install_windows_app()

        self.assertTrue(any(path == autostart.start_menu_shortcut() for path in written))
        create_key.assert_not_called()
        set_value.assert_not_called()

    def test_write_shortcut_never_uses_execution_policy_bypass(self):
        calls = []

        def fake_run(cmd, **kwargs):
            calls.append(cmd)
            return mock.Mock(returncode=0, stdout="", stderr="")

        with mock.patch.object(autostart.subprocess, "run", side_effect=fake_run):
            autostart._write_shortcut(
                self.base / "MusicMatch.lnk",
                str(self.base / "MusicMatch.exe"),
                "",
                str(self.base),
                str(self.base / "MusicMatch.ico"),
            )

        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0][:3], ["powershell", "-NoProfile", "-Command"])
        self.assertNotIn("-ExecutionPolicy", calls[0])
        self.assertNotIn("Bypass", " ".join(calls[0]))


if __name__ == "__main__":
    unittest.main()
