import colorsys
import io
import unittest

from PIL import Image, ImageDraw

from musicmatch.color import (
    led_color_for_image,
    led_color_from_spotify,
    parse_embed_background,
    rebrighten,
)

NEUTRAL = (255, 190, 120)


def cover(bg, accent=None, share=0.25):
    im = Image.new("RGB", (300, 300), bg)
    if accent:
        side = int(300 * share**0.5)
        ImageDraw.Draw(im).rectangle([0, 0, side, side], fill=accent)
    buf = io.BytesIO()
    im.save(buf, "PNG")
    return buf.getvalue()


def hue(rgb):
    return colorsys.rgb_to_hsv(*(c / 255 for c in rgb))[0]


def led(data, mode="vibrant", brightness=100):
    return led_color_for_image(data, mode, 0.55, brightness, NEUTRAL)


class ColorTests(unittest.TestCase):
    def test_vibrant_ignores_black_background(self):
        rgb, swatch = led(cover((5, 5, 5), (200, 30, 30), 0.15))
        self.assertIsNotNone(swatch)
        self.assertEqual(rgb[0], 255)
        self.assertLess(rgb[1], 120)

    def test_vibrant_ignores_white_background(self):
        rgb, _ = led(cover((245, 245, 245), (0, 150, 140), 0.2))
        self.assertAlmostEqual(hue(rgb), hue((0, 150, 140)), delta=0.03)

    def test_dominant_on_black_cover_falls_back_to_neutral(self):
        rgb, swatch = led(cover((5, 5, 5), (200, 30, 30), 0.15), mode="dominant")
        self.assertIsNone(swatch)
        self.assertEqual(rgb, NEUTRAL)

    def test_muted_color_is_saturated_and_full_brightness(self):
        rgb, _ = led(cover((30, 40, 80)))
        h, s, v = colorsys.rgb_to_hsv(*(c / 255 for c in rgb))
        self.assertAlmostEqual(h, hue((30, 40, 80)), delta=0.03)
        self.assertGreaterEqual(s, 0.55)
        self.assertEqual(v, 1.0)

    def test_greyscale_cover_uses_neutral(self):
        rgb, swatch = led(cover((120, 120, 120), (20, 20, 20)))
        self.assertIsNone(swatch)
        self.assertEqual(rgb, NEUTRAL)

    def test_brightness_scales_output(self):
        full, _ = led(cover((0, 0, 255)))
        half, _ = led(cover((0, 0, 255)), brightness=50)
        self.assertEqual(half, tuple(round(c * 0.5) for c in full))

    def test_color_boost_never_changes_brightness(self):
        for cover_rgb in [(237, 171, 133), (82, 107, 161), (63, 61, 12)]:
            for boost in (0.0, 0.55, 1.0):
                for brightness in (35, 50, 100):
                    rgb, _ = led_color_for_image(cover(cover_rgb), "vibrant", boost, brightness, NEUTRAL)
                    self.assertEqual(max(rgb), round(255 * brightness / 100), (cover_rgb, boost))

    def test_rebrighten_keeps_hue(self):
        self.assertEqual(rebrighten((128, 64, 0), 100), (255, 128, 0))
        self.assertEqual(rebrighten((255, 128, 0), 50), (128, 64, 0))
        self.assertAlmostEqual(hue(rebrighten((46, 46, 78), 20)), hue((46, 46, 78)), delta=0.01)

    def test_parse_embed_background(self):
        html = ('<div style="--dynamic-opacity:1;--dynamic-background-base:rgba(16, 64, 72, 255);'
                '--background-base:rgba(16, 64, 72, 255);--text-base:rgba(255, 255, 255, 255)">')
        self.assertEqual(parse_embed_background(html), (16, 64, 72))
        self.assertIsNone(parse_embed_background("<html>no colors</html>"))

    def test_spotify_color_keeps_hue_and_uses_brightness(self):
        rgb = led_color_from_spotify((16, 64, 72), 0.55, 50, NEUTRAL)
        self.assertAlmostEqual(hue(rgb), hue((16, 64, 72)), delta=0.01)
        self.assertEqual(max(rgb), 128)

    def test_grey_spotify_color_uses_neutral(self):
        self.assertEqual(led_color_from_spotify((40, 40, 42), 0.55, 100, NEUTRAL), NEUTRAL)

    def test_rebrighten_black_is_none(self):
        self.assertIsNone(rebrighten((0, 0, 0), 50))


if __name__ == "__main__":
    unittest.main()
