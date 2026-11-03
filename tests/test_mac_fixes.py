import socket
import threading
import unittest
from types import SimpleNamespace
from unittest import mock
from urllib.error import HTTPError
from urllib.parse import parse_qs, urlencode, urlparse
from urllib.request import ProxyHandler, build_opener

from musicmatch import devices, gui, spotify_client, widgets
from musicmatch.config import Config
from musicmatch.devices import base, govee, magichome


class ScrollbarTests(unittest.TestCase):
    def bar(self, vertical=True, first=0.2, last=0.6):
        return SimpleNamespace(vertical=vertical, first=first, last=last, _drag=None,
                               _hover=False, winfo_height=lambda: 204, winfo_width=lambda: 204)

    def test_thumb_geometry_matches_view(self):
        start, thumb, travel, span = widgets.ThemedScrollbar._geometry(self.bar())
        self.assertAlmostEqual(start, 42)
        self.assertAlmostEqual(thumb, 80)
        self.assertAlmostEqual(travel, 120)
        self.assertAlmostEqual(span, 0.6)

    def test_small_thumb_stays_draggable(self):
        self.assertEqual(widgets.ThemedScrollbar._geometry(self.bar(last=0.201))[1], 24)

    def test_fully_visible_content_has_no_thumb(self):
        bar = mock.Mock(first=0, last=1)
        widgets.ThemedScrollbar._draw(bar)
        bar.delete.assert_called_once_with("thumb")
        bar.create_line.assert_not_called()

    def test_thumb_uses_theme_and_round_caps(self):
        bar = mock.Mock(first=0.2, last=0.6, vertical=True, _hover=False, _drag=None)
        bar._geometry.return_value = (42, 80, 120, 0.6)
        widgets.ThemedScrollbar._draw(bar)
        bar.create_line.assert_called_once_with(6, 45, 6, 119, fill=widgets.CONTROL_HI,
                                                width=6, capstyle="round", tags="thumb")

    def test_drag_both_axes_and_clamp(self):
        for vertical in (True, False):
            bar = mock.Mock(vertical=vertical, _drag=(42, 0.2))
            bar._geometry.return_value = (42, 80, 120, 0.6)
            widgets.ThemedScrollbar._motion(bar, SimpleNamespace(x=82, y=82))
            self.assertAlmostEqual(bar.command.call_args.args[1], 0.4)
            widgets.ThemedScrollbar._motion(bar, SimpleNamespace(x=999, y=999))
            bar.command.assert_called_with("moveto", 0.6)

    def test_trough_click_pages(self):
        bar = mock.Mock(first=0.2, last=0.6, vertical=True)
        bar._geometry.return_value = (42, 80, 120, 0.6)
        widgets.ThemedScrollbar._press(bar, SimpleNamespace(y=150))
        bar.command.assert_called_once_with("scroll", 1, "pages")


class LanDiscoveryTests(unittest.TestCase):
    IFCONFIG = """lo0: flags=8049<UP,LOOPBACK,RUNNING,MULTICAST> mtu 16384
    inet 127.0.0.1 netmask 0xff000000
en0: flags=8863<UP,BROADCAST,SMART,RUNNING,SIMPLEX,MULTICAST> mtu 1500
    inet 192.168.1.5 netmask 0xffffff00 broadcast 192.168.1.255
    status: active
en1: flags=8863<UP,BROADCAST,RUNNING,MULTICAST> mtu 1500
    inet 10.0.0.2 netmask 0xffffff00 broadcast 10.0.0.255
    status: inactive
utun9: flags=8051<UP,POINTOPOINT,RUNNING,MULTICAST> mtu 1380
    inet 172.16.0.2 netmask 0xffffffff
"""

    def test_only_active_lan_interfaces_on_mac(self):
        with mock.patch.object(base.sys, "platform", "darwin"), mock.patch.object(base.subprocess, "run") as run:
            run.return_value.stdout = self.IFCONFIG
            self.assertEqual(base.lan_interfaces(), [("en0", "192.168.1.5", "192.168.1.255")])
            self.assertEqual(run.call_args.args[0], ["/sbin/ifconfig", "-a"])

    def test_other_platforms_keep_default_route(self):
        with mock.patch.object(base.sys, "platform", "win32"), mock.patch.object(base.subprocess, "run") as run:
            self.assertEqual(base.lan_interfaces(), [])
            run.assert_not_called()

    def test_interface_detection_failure_falls_back(self):
        with mock.patch.object(base.sys, "platform", "darwin"), mock.patch.object(base.subprocess, "run", side_effect=OSError):
            self.assertEqual(base.lan_interfaces(), [])

    def test_broadcast_and_multicast_use_lan_and_restore_socket(self):
        for host in ("255.255.255.255", "239.255.255.250"):
            sock = mock.Mock()
            with mock.patch.object(base, "lan_interfaces", return_value=[("en0", "192.168.1.5", "192.168.1.255")]), mock.patch.object(base.socket, "if_nametoindex", return_value=4):
                base.send_discovery(sock, b"scan", (host, 123))
            destination = "192.168.1.255" if host == "255.255.255.255" else host
            self.assertEqual(sock.sendto.call_args_list, [mock.call(b"scan", (destination, 123)), mock.call(b"scan", (host, 123))])
            sock.setsockopt.assert_any_call(socket.IPPROTO_IP, getattr(socket, "IP_BOUND_IF", 25), 4)
            sock.setsockopt.assert_any_call(socket.IPPROTO_IP, getattr(socket, "IP_BOUND_IF", 25), 0)
            if host.startswith("239"):
                sock.setsockopt.assert_any_call(socket.IPPROTO_IP, socket.IP_MULTICAST_IF, socket.inet_aton("192.168.1.5"))
                sock.setsockopt.assert_any_call(socket.IPPROTO_IP, socket.IP_MULTICAST_IF, socket.inet_aton("0.0.0.0"))

    def test_failed_default_route_does_not_discard_successful_lan_send(self):
        sock = mock.Mock()
        sock.sendto.side_effect = [None, OSError("no route")]
        with mock.patch.object(base, "lan_interfaces", return_value=[("en0", "192.168.1.5", "192.168.1.255")]), mock.patch.object(base.socket, "if_nametoindex", return_value=4):
            base.send_discovery(sock, b"scan", ("255.255.255.255", 123))

    def test_permission_failure_is_not_silenced(self):
        sock = mock.Mock()
        sock.recvfrom.side_effect = PermissionError("Local Network denied")
        with self.assertRaises(PermissionError):
            list(base.collect(sock, 1))
        with mock.patch.object(devices, "_SCANNERS", {"wiz": mock.Mock(side_effect=PermissionError("Local Network denied"))}), mock.patch.object(devices.hue, "discover", return_value=([], [])):
            with self.assertRaisesRegex(RuntimeError, "Local Network denied"):
                devices.discover(0.01)

    def test_partial_scan_failure_preserves_found_lights(self):
        light = {"type": "wiz", "host": "192.168.1.20"}
        with mock.patch.object(devices, "_SCANNERS", {"wiz": mock.Mock(return_value=[light]), "lifx": mock.Mock(side_effect=OSError("denied"))}), mock.patch.object(devices.hue, "discover", return_value=([], [])):
            self.assertEqual(devices.discover(0.01), ([light], []))

    def test_govee_port_failure_is_reported_and_socket_closed(self):
        sock = mock.Mock()
        sock.bind.side_effect = OSError("port busy")
        with mock.patch.object(govee, "udp_socket", return_value=sock):
            with self.assertRaisesRegex(OSError, "port busy"):
                govee.discover(0.01)
        sock.close.assert_called_once()

    def test_magichome_scans_directed_broadcast_and_deduplicates(self):
        scanner = mock.Mock()
        scanner.scan.return_value = [{"ipaddr": "192.168.1.20", "id": "lamp"}]
        with mock.patch.object(magichome, "lan_interfaces", return_value=[("en0", "192.168.1.5", "192.168.1.255")]), mock.patch.object(magichome, "BulbScanner", return_value=scanner):
            self.assertEqual(len(magichome.discover(1)), 1)
        scanner.scan.assert_any_call(timeout=1, address="192.168.1.255")


class SpotifyConnectionTests(unittest.TestCase):
    def setUp(self):
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
        self.cfg = Config(spotify_redirect_uri=f"http://127.0.0.1:{port}/callback")
        self.auth = mock.Mock()
        self.auth.get_authorize_url.side_effect = lambda state: "https://accounts.spotify.com/authorize?" + urlencode({"state": state})
        self.auth.get_access_token.return_value = "test-token"
        self.opener = build_opener(ProxyHandler({}))
        self.threads = []
        self.responses = []

    def tearDown(self):
        for thread in self.threads:
            thread.join(3)
            self.assertFalse(thread.is_alive())

    def browser(self, url, error=False, invalid_first=False):
        state = parse_qs(urlparse(url).query)["state"][0]

        def reply():
            if invalid_first:
                for path in ("/favicon.ico", "/callback?code=bad&state=wrong"):
                    try:
                        self.opener.open(self.cfg.spotify_redirect_uri.replace("/callback", path), timeout=2)
                    except HTTPError as exc:
                        self.responses.append(exc.code)
                        exc.close()
            values = {"state": state, "error": "access_denied"} if error else {"state": state, "code": "test-code"}
            with self.opener.open(self.cfg.spotify_redirect_uri + "?" + urlencode(values), timeout=2) as response:
                self.responses.append(response.read().decode())

        thread = threading.Thread(target=reply)
        thread.start()
        self.threads.append(thread)
        return True

    def test_login_uses_state_and_survives_unrelated_or_invalid_requests(self):
        with mock.patch.object(spotify_client, "_make_auth", return_value=self.auth), mock.patch.object(spotify_client.webbrowser, "open", side_effect=lambda url: self.browser(url, invalid_first=True)), mock.patch.object(spotify_client.spotipy, "Spotify") as spotify:
            spotify.return_value.current_user.return_value = {"id": "listener", "display_name": "Listener"}
            self.assertEqual(spotify_client.connect_spotify(self.cfg, timeout=3), "Listener")
        for thread in self.threads:
            thread.join(3)
        self.auth.get_access_token.assert_called_once_with(code="test-code", check_cache=False, as_dict=False)
        self.assertEqual(self.responses[:2], [404, 400])
        self.assertIn("Return to MusicMatch", self.responses[2])

    def test_declined_login_does_not_exchange_token(self):
        with mock.patch.object(spotify_client, "_make_auth", return_value=self.auth), mock.patch.object(spotify_client.webbrowser, "open", side_effect=lambda url: self.browser(url, error=True)):
            with self.assertRaisesRegex(ConnectionError, "declined"):
                spotify_client.connect_spotify(self.cfg, timeout=3)
        self.auth.get_access_token.assert_not_called()

    def test_timeout_is_bounded_and_listener_is_closed(self):
        with mock.patch.object(spotify_client, "_make_auth", return_value=self.auth), mock.patch.object(spotify_client.webbrowser, "open", return_value=True):
            with self.assertRaisesRegex(TimeoutError, "redirect error"):
                spotify_client.connect_spotify(self.cfg, timeout=0.01)
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", urlparse(self.cfg.spotify_redirect_uri).port))

    def test_unavailable_browser_is_actionable(self):
        with mock.patch.object(spotify_client, "_make_auth", return_value=self.auth), mock.patch.object(spotify_client.webbrowser, "open", return_value=False):
            with self.assertRaisesRegex(ConnectionError, "default browser"):
                spotify_client.connect_spotify(self.cfg)

    def test_busy_port_is_detected_before_opening_browser(self):
        with socket.socket() as sock, mock.patch.object(spotify_client.webbrowser, "open") as browser:
            sock.bind(("127.0.0.1", urlparse(self.cfg.spotify_redirect_uri).port))
            sock.listen()
            with self.assertRaisesRegex(ConnectionError, "another MusicMatch"):
                spotify_client.connect_spotify(self.cfg)
            browser.assert_not_called()

    def test_invalid_redirect_never_opens_browser(self):
        for uri in ("http://localhost:8888/callback", "https://example.com", "http://127.0.0.1/callback", "http://127.0.0.1:bad/callback"):
            with mock.patch.object(spotify_client.webbrowser, "open") as browser:
                with self.assertRaises(ValueError):
                    spotify_client.connect_spotify(Config(spotify_redirect_uri=uri))
                browser.assert_not_called()

    def test_connect_is_not_started_twice(self):
        application = mock.Mock(_connecting_spotify=True)
        gui.App._connect_spotify(application)
        application._background.assert_not_called()
        self.assertIn("already open", application._set_status.call_args.args[0])

    def test_error_allows_retry(self):
        application = mock.Mock(_connecting_spotify=True)
        gui.App._handle(application, "spotify_error", "Login timed out")
        self.assertFalse(application._connecting_spotify)


if __name__ == "__main__":
    unittest.main()
