import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import requests

from musicmatch import updates


def release(tag="v1.0.4", platforms=("darwin", "win32")):
    return {
        "tag_name": tag, "draft": False, "prerelease": False,
        "assets": [{"name": updates.ASSETS[platform], "state": "uploaded", "size": 123}
                   for platform in platforms],
    }


class UpdateTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.cache = Path(self.temporary.name) / "updates.json"
        self.network = mock.patch.object(updates.requests, "get")
        self.get = self.network.start()
        self.addCleanup(self.network.stop)
        self.get.return_value.json.return_value = release()
        self.now = 2_000_000

    def check(self, version="1.0.3", platform="darwin", now=None):
        return updates.check_for_update(version, platform, self.cache,
                                        self.now if now is None else now)

    def test_platform_specific_downloads(self):
        for platform, asset in updates.ASSETS.items():
            with self.subTest(platform=platform):
                update = self.check(platform=platform)
                self.assertEqual(update.version, "1.0.4")
                self.assertEqual(update.download_url,
                                 f"https://github.com/{updates.REPOSITORY}/releases/download/v1.0.4/{asset}")

    def test_numeric_versions_not_lexicographical_order(self):
        self.get.return_value.json.return_value = release("v1.0.10")
        self.assertEqual(self.check(version="1.0.9").version, "1.0.10")

    def test_equal_or_older_release_is_not_an_update(self):
        for version in ("1.0.4", "1.0.5", "2.0.0"):
            self.assertIsNone(self.check(version=version))

    def test_only_stable_version_tags_are_accepted(self):
        for tag in ("v1.1.0-beta.1", "v1.1.0+build", "latest", "v01.2.3", "../../other", None):
            with self.subTest(tag=tag):
                self.assertIsNone(updates._available_update(release(tag), "1.0.3", "darwin"))

    def test_drafts_and_prereleases_are_ignored(self):
        for flag in ("draft", "prerelease"):
            payload = release()
            payload[flag] = True
            self.assertIsNone(updates._available_update(payload, "1.0.3", "darwin"))

    def test_missing_platform_zip_is_not_advertised(self):
        self.get.return_value.json.return_value = release(platforms=("win32",))
        self.assertIsNone(self.check())
        self.assertIsNotNone(self.check(platform="win32"))

    def test_upload_must_be_complete_and_nonempty(self):
        for changes in ({"state": "new"}, {"size": 0}, {"size": "123"}):
            payload = release()
            payload["assets"][0].update(changes)
            self.assertIsNone(updates._available_update(payload, "1.0.3", "darwin"))

    def test_remote_download_urls_are_not_trusted(self):
        payload = release()
        payload["assets"][0]["browser_download_url"] = "https://untrusted.example/app.zip"
        self.get.return_value.json.return_value = payload
        self.assertTrue(self.check().download_url.startswith(f"https://github.com/{updates.REPOSITORY}/"))

    def test_recent_cache_survives_restart_without_network(self):
        first = self.check()
        self.get.reset_mock()
        self.assertEqual(self.check(now=self.now + 3600), first)
        self.get.assert_not_called()

    def test_cache_refreshes_after_24_hours(self):
        self.check()
        self.get.reset_mock()
        self.check(now=self.now + updates.CHECK_INTERVAL_SECONDS)
        self.get.assert_called_once()

    def test_clock_moving_backward_does_not_disable_checks(self):
        self.check()
        self.get.reset_mock()
        self.check(now=self.now - 3600)
        self.get.assert_called_once()

    def test_newly_installed_version_does_not_show_cached_notice(self):
        self.check()
        self.assertIsNone(self.check(version="1.0.4"))

    def test_failed_checks_are_silent_and_throttled(self):
        for failure in (requests.Timeout(), requests.ConnectionError(), requests.HTTPError(), ValueError()):
            with self.subTest(failure=type(failure).__name__):
                self.cache.unlink(missing_ok=True)
                self.get.side_effect = failure
                self.assertIsNone(self.check())
                self.get.reset_mock()
                self.assertIsNone(self.check(now=self.now + 60))
                self.get.assert_not_called()

    def test_failed_check_preserves_previous_available_update(self):
        first = self.check()
        self.get.side_effect = requests.Timeout()
        self.assertEqual(self.check(now=self.now + updates.CHECK_INTERVAL_SECONDS), first)

    def test_http_failure_is_handled(self):
        self.get.return_value.raise_for_status.side_effect = requests.HTTPError()
        self.assertIsNone(self.check())

    def test_malformed_cache_is_replaced(self):
        for content in ("not json", "[]", '{"checked_at":"invalid"}'):
            with self.subTest(content=content):
                self.cache.write_text(content)
                self.assertIsNotNone(self.check())

    def test_malformed_api_results_are_ignored(self):
        for payload in ([], None, {"assets": {}}, {"assets": [None]}, release(None)):
            with self.subTest(payload=payload):
                self.cache.unlink(missing_ok=True)
                self.get.return_value.json.return_value = payload
                self.assertIsNone(self.check())

    def test_unwritable_cache_does_not_hide_update_or_raise(self):
        with mock.patch.object(Path, "write_text", side_effect=PermissionError()):
            self.assertIsNotNone(self.check())

    def test_cache_stores_only_small_release_metadata(self):
        payload = release()
        payload["body"] = "Release notes" * 1000
        self.get.return_value.json.return_value = payload
        self.check()
        cached = json.loads(self.cache.read_text())
        self.assertNotIn("body", cached["release"])
        self.assertLess(self.cache.stat().st_size, 1000)

    def test_no_request_for_unsupported_platform_or_invalid_current_version(self):
        self.assertIsNone(self.check(platform="linux"))
        self.assertIsNone(self.check(version="development"))
        self.get.assert_not_called()

    def test_request_has_short_timeouts_and_no_credentials(self):
        self.check()
        self.get.assert_called_once_with(
            updates.LATEST_RELEASE_URL, timeout=(3, 5),
            headers={"Accept": "application/vnd.github+json", "User-Agent": "MusicMatch/1.0.3"},
        )


if __name__ == "__main__":
    unittest.main()
