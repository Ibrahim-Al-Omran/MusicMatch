import json
import socket
import struct
import threading
import unittest
from unittest import mock

from musicmatch import devices
from musicmatch.devices import hue, lifx, wiz, yeelight
from musicmatch.devices.base import join_brightness, split_brightness
from musicmatch.lights import LightGroup


def _udp_server(handler):
    """Fake light on 127.0.0.1: handler(request bytes) -> reply bytes or None."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind(("127.0.0.1", 0))
    sock.settimeout(5)

    def serve():
        try:
            while True:
                data, addr = sock.recvfrom(4096)
                reply = handler(data)
                if reply is not None:
                    sock.sendto(reply, addr)
        except OSError:
            pass

    threading.Thread(target=serve, daemon=True).start()
    return sock, sock.getsockname()[1]


class ConfigEntries(unittest.TestCase):
    def test_bare_ip_is_magic_home(self):
        self.assertEqual(devices.normalize("10.0.0.5"),
                         {"type": "magichome", "host": "10.0.0.5", "name": "Magic Home", "enabled": True})

    def test_merge_keeps_choice_and_follows_new_ip(self):
        known = [{"type": "wiz", "host": "10.0.0.5", "id": "aabb", "enabled": False}]
        found = [{"type": "wiz", "host": "10.0.0.9", "id": "AABB", "name": "Desk"},
                 {"type": "lifx", "host": "10.0.0.7", "id": "cc", "name": "Strip"}]
        merged = devices.merge(known, found)
        self.assertEqual(len(merged), 2)
        self.assertEqual((merged[0]["host"], merged[0]["enabled"], merged[0]["name"]), ("10.0.0.9", False, "Desk"))
        self.assertTrue(merged[1]["enabled"])

    def test_merge_upgrades_old_ip_only_entry(self):
        found = [{"type": "magichome", "host": "10.0.0.5", "id": "a1b2", "name": "Magic Home"}]
        merged = devices.merge(["10.0.0.5"], found)
        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0]["id"], "a1b2")

    def test_disabled_lights_are_not_driven(self):
        group = LightGroup(["10.0.0.5", {"type": "wiz", "host": "10.0.0.6", "enabled": False}], 0, False)
        self.assertEqual([light.device.host for light in group.lights], ["10.0.0.5"])


class Colour(unittest.TestCase):
    def test_brightness_split_round_trips(self):
        color, level = split_brightness((89, 44, 0))
        self.assertEqual(max(color), 255)
        self.assertEqual(level, 35)
        back = join_brightness(color, level)
        for a, b in zip(back, (89, 44, 0)):
            self.assertLessEqual(abs(a - b), 2)

    def test_hue_xy_round_trip_keeps_hue(self):
        import colorsys

        for rgb in ((255, 0, 0), (0, 128, 255), (255, 160, 40), (40, 255, 90)):
            xy, bri = hue.rgb_to_xy_bri(rgb)
            back = hue.xy_bri_to_rgb(xy, bri)
            h1 = colorsys.rgb_to_hsv(*(c / 255 for c in rgb))[0]
            h2 = colorsys.rgb_to_hsv(*(c / 255 for c in back))[0]
            self.assertLess(min(abs(h1 - h2), 1 - abs(h1 - h2)), 0.04, (rgb, back))


class Wiz(unittest.TestCase):
    def test_session_reads_and_sets(self):
        sent = []

        def handle(data):
            msg = json.loads(data)
            sent.append(msg)
            if msg["method"] == "getPilot":
                return json.dumps({"result": {"state": True, "r": 255, "g": 0, "b": 0, "dimming": 50}}).encode()
            return json.dumps({"result": {"success": True}}).encode()

        sock, port = _udp_server(handle)
        with sock, mock.patch.object(wiz, "PORT", port):
            with wiz.Wiz({"type": "wiz", "host": "127.0.0.1"}).session() as s:
                self.assertTrue(s.is_on)
                self.assertEqual(s.rgb, (128, 0, 0))
                s.set_rgb((0, 64, 128), 0)
        self.assertEqual(sent[-1], {"method": "setPilot", "params": {"r": 0, "g": 128, "b": 255, "dimming": 50}})


class Lifx(unittest.TestCase):
    def test_state_parse_and_set_colour(self):
        got = []

        def handle(data):
            msg_type, _target, payload = lifx._parse(data)
            got.append((msg_type, payload))
            label = b"Strip".ljust(32, b"\0")
            state = struct.pack("<HHHHhH", 0, 65535, 32768, 3500, 0, 65535) + label + b"\0" * 8
            reply = bytearray(lifx._packet(lifx.LIGHT_STATE, state))
            return bytes(reply)

        sock, port = _udp_server(handle)
        with sock, mock.patch.object(lifx, "PORT", port):
            with lifx.Lifx({"type": "lifx", "host": "127.0.0.1", "id": "d073d5000001"}).session() as s:
                self.assertTrue(s.is_on)
                self.assertEqual(s.rgb, (128, 0, 0))
                s.set_rgb((0, 0, 255), 0.4)
        msg_type, payload = got[-1]
        self.assertEqual(msg_type, lifx.LIGHT_SET_COLOR)
        _res, h, sat, bri, _k, duration = struct.unpack("<BHHHHI", payload)
        self.assertEqual((round(h / 65535 * 360), sat, bri, duration), (240, 65535, 65535, 400))


class Yeelight(unittest.TestCase):
    def test_session_skips_notifications(self):
        server = socket.socket()
        server.bind(("127.0.0.1", 0))
        server.listen(1)
        calls = []

        def serve():
            conn, _ = server.accept()
            with conn, conn.makefile("rb") as f:
                for line in f:
                    req = json.loads(line)
                    calls.append(req)
                    conn.sendall(b'{"method":"props","params":{"power":"on"}}\r\n')
                    result = ["on", "40", str(0x00FF00), "1"] if req["method"] == "get_prop" else ["ok"]
                    conn.sendall(json.dumps({"id": req["id"], "result": result}).encode() + b"\r\n")

        threading.Thread(target=serve, daemon=True).start()
        with server, mock.patch.object(yeelight, "PORT", server.getsockname()[1]):
            with yeelight.Yeelight({"type": "yeelight", "host": "127.0.0.1"}).session() as s:
                self.assertTrue(s.is_on)
                self.assertEqual(s.rgb, (0, 102, 0))
                s.set_rgb((255, 0, 0), 0)
        self.assertEqual([c["method"] for c in calls], ["get_prop", "set_rgb", "set_bright"])
        self.assertEqual(calls[1]["params"], [0xFF0000, "smooth", 30])


if __name__ == "__main__":
    unittest.main()
