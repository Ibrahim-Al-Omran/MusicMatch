import os
import runpy
import tempfile
import unittest
from pathlib import Path
from unittest import mock

release = runpy.run_path(str(Path(__file__).resolve().parent.parent / "packaging" / "release_version.py"))


class ReleaseVersionTests(unittest.TestCase):
    def choose(self, base="1.0.4", tags=None, ref="refs/heads/main"):
        return release["choose_version"](base, ["v1.0.0", "v1.0.3"] if tags is None else tags, ref)

    def test_main_uses_next_available_stable_version(self):
        self.assertEqual(self.choose(), ("1.0.4", True))

    def test_later_main_builds_increment_version_without_source_commits(self):
        self.assertEqual(self.choose(tags=["v1.0.3", "v1.0.4"]), ("1.0.5", True))

    def test_numeric_version_order(self):
        self.assertEqual(self.choose(tags=["v1.0.9", "v1.0.10"]), ("1.0.11", True))

    def test_source_version_can_request_a_new_major_release(self):
        self.assertEqual(self.choose(base="2.0.0", tags=["v1.9.9"]), ("2.0.0", True))

    def test_first_release_uses_source_version(self):
        self.assertEqual(self.choose(tags=[]), ("1.0.4", True))

    def test_nonrelease_and_prerelease_tags_are_ignored(self):
        self.assertEqual(self.choose(tags=["v1.0.3", "v99.0.0-beta.1", "test", "99.0.0"]),
                         ("1.0.4", True))

    def test_explicit_version_tag_is_reproducible(self):
        self.assertEqual(self.choose(tags=["v9.0.0"], ref="refs/tags/v1.2.3"), ("1.2.3", True))

    def test_other_branches_create_artifacts_without_publishing(self):
        self.assertEqual(self.choose(tags=["v9.0.0"], ref="refs/heads/test"), ("1.0.4", False))

    def test_invalid_release_tag_fails(self):
        with self.assertRaises(ValueError):
            self.choose(ref="refs/tags/v1.2.3-beta.1")

    def test_version_written_to_bundle_recipe_input_and_runtime(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "musicmatch").mkdir()
            (root / "VERSION").write_text("1.0.4\n")
            module = root / "musicmatch" / "__init__.py"
            module.write_text('"""MusicMatch."""\n\n__version__ = "1.0.4"\n')
            release["write_version"](root, "1.0.5")
            self.assertEqual((root / "VERSION").read_text(), "1.0.5\n")
            self.assertIn('__version__ = "1.0.5"', module.read_text())

    def test_invalid_version_never_written(self):
        with tempfile.TemporaryDirectory() as temporary:
            with self.assertRaises(ValueError):
                release["write_version"](Path(temporary), "not-a-version")

    def test_missing_runtime_version_fails_without_changing_version_file(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "musicmatch").mkdir()
            (root / "VERSION").write_text("1.0.4\n")
            (root / "musicmatch" / "__init__.py").write_text('"""Missing version."""\n')
            with self.assertRaises(ValueError):
                release["write_version"](root, "1.0.5")
            self.assertEqual((root / "VERSION").read_text(), "1.0.4\n")

    def test_prepare_outputs_match_both_builds_and_publication(self):
        main = release["main"]
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "VERSION").write_text("1.0.4\n")
            output = root / "output.txt"
            with mock.patch.dict(main.__globals__, {"ROOT": root}), \
                 mock.patch.dict(os.environ, {"GITHUB_REF": "refs/heads/main", "GITHUB_OUTPUT": str(output)}), \
                 mock.patch("sys.argv", ["release_version.py"]), \
                 mock.patch("subprocess.check_output", return_value="v1.0.3\nv1.0.4\n"):
                main()
            self.assertEqual(output.read_text(), "version=1.0.5\ntag=v1.0.5\npublish=true\n")


if __name__ == "__main__":
    unittest.main()
