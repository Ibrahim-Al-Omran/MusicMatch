import tempfile
import threading
import unittest
from dataclasses import replace
from pathlib import Path
from unittest import mock

from musicmatch import app
from musicmatch.app import AlreadyRunning, _acquire_instance_lock, read_color_cache, write_color_cache
from musicmatch.config import Config
from musicmatch.logo import menu_bar_icon
from musicmatch.spotify_client import NowPlaying


class SongBrightnessTests(unittest.TestCase):
    def setUp(self):
        self.engine = object.__new__(app.MusicMatch)
        self.engine.cfg = Config(brightness=35)
        self.engine.lights = mock.Mock()
        self.engine._settings_lock = threading.Lock()
        self.engine._current_key = None
        self.engine._color_cache = {}
        self.engine._cache_generation = 0
        self.engine._save_color_cache = mock.Mock()
        self.engine._remember = mock.Mock()
        self.engine._prefetch_upcoming = mock.Mock()
        self.engine.on_track = None
        self.song = NowPlaying('track', 'first', 'Song', 'Artist', 'Album',
                               'https://example/cover', 'Player', True, 10000)

    def test_new_and_cached_songs_preserve_live_brightness(self):
        color = (255, 90, 0)
        with mock.patch.object(app, 'compute_color', return_value=(color, 'test')) as compute:
            self.engine._on_playing(self.song)
            self.engine._color_cache[app.cache_key(self.song)] = (color, 'cached')
            self.engine._current_key = None
            self.engine._on_playing(replace(self.song, item_id='second'))
        compute.assert_called_once()
        self.assertEqual(self.engine.lights.set_color.call_args_list, [
            mock.call((89, 31, 0), preserve_brightness=True),
            mock.call((89, 31, 0), preserve_brightness=True),
        ])

    def test_explicit_brightness_change_applies_without_recolouring(self):
        self.engine._current_key = 'current'
        self.engine.update_settings(replace(self.engine.cfg, brightness=100))
        self.engine.lights.set_brightness.assert_called_once_with(100)
        self.engine.lights.set_color.assert_not_called()
        self.assertEqual(self.engine._current_key, 'current')
        self.assertEqual(self.engine.cfg.brightness, 100)

    def test_colour_boost_change_does_not_reset_brightness(self):
        self.engine.update_settings(replace(self.engine.cfg, min_saturation=1.0))
        self.engine.lights.set_brightness.assert_not_called()
        self.assertEqual(self.engine.cfg.brightness, 35)
        self.assertEqual(self.engine._cache_generation, 1)


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


class LogoTests(unittest.TestCase):
    def test_menu_bar_icon_is_a_black_template_glyph(self):
        img = menu_bar_icon(44)
        self.assertEqual(img.size, (44, 44))
        self.assertGreater(img.getextrema()[3][1], 0)  # has some opaque pixels
        opaque = [px[:3] for px in img.getdata() if px[3] > 0]
        self.assertTrue(opaque)
        self.assertTrue(all(px == (0, 0, 0) for px in opaque))


if __name__ == "__main__":
    unittest.main()
