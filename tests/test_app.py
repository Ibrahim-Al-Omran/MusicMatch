import tempfile
import unittest
from pathlib import Path
from unittest import mock

from musicmatch import app
from musicmatch.app import AlreadyRunning, _acquire_instance_lock, read_color_cache, write_color_cache
from musicmatch.config import Config


class InstanceLockTests(unittest.TestCase):
    def test_second_sync_is_refused_until_first_stops(self):
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(
            app, "INSTANCE_LOCK_PATH", Path(tmp) / "test.lock"
        ):
            first = _acquire_instance_lock()
            try:
                with self.assertRaises(AlreadyRunning):
                    _acquire_instance_lock()
            finally:
                first.close()
            _acquire_instance_lock().close()


class ColorCacheTests(unittest.TestCase):
    def test_round_trip_and_settings_mismatch(self):
        cfg = Config(color_mode="spotify", min_saturation=0.8, neutral_color=(255, 190, 120))
        other = Config(color_mode="vibrant", min_saturation=0.8, neutral_color=(255, 190, 120))
        colors = {"https://example/cover": ((201, 40, 138), "Spotify background #c9288a")}
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(
            app, "COLOR_CACHE_PATH", Path(tmp) / "colors.json"
        ):
            write_color_cache(cfg, colors)
            loaded, _ = read_color_cache(cfg)
            self.assertEqual(loaded, colors)
            self.assertEqual(read_color_cache(other)[0], {})


if __name__ == "__main__":
    unittest.main()
