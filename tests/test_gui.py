import unittest
from types import SimpleNamespace
from unittest import mock

from musicmatch import gui, widgets
from musicmatch.config import Config


class ScrollingTests(unittest.TestCase):
    def setUp(self):
        self.frame = SimpleNamespace(
            canvas=mock.Mock(), content=mock.Mock(), _window=1,
            _wheel_remainder={"x": 0.0, "y": 0.0}, update_idletasks=mock.Mock(),
            _sync_job=None, after_idle=mock.Mock(return_value="idle-job"), _sync_content=mock.Mock(),
        )
        self.frame.canvas.xview.return_value = (0.0, 0.5)
        self.frame.canvas.yview.return_value = (0.0, 0.5)
        self.event = SimpleNamespace(
            widget=SimpleNamespace(master=self.frame), state=0, delta=-120, num=None,
        )

    def scroll(self, platform="win32"):
        with mock.patch.object(widgets.sys, "platform", platform):
            return widgets.ScrollableFrame._mousewheel(self.frame, self.event)

    def test_windows_mouse_wheel_scrolls_vertically(self):
        self.assertEqual(self.scroll(), "break")
        self.frame.canvas.yview_scroll.assert_called_once_with(3, "units")
        self.frame.canvas.xview_scroll.assert_not_called()

    def test_mac_trackpad_uses_small_native_deltas(self):
        self.event.delta = -2
        self.scroll("darwin")
        self.frame.canvas.yview_scroll.assert_called_once_with(2, "units")

    def test_high_resolution_windows_wheel_accumulates(self):
        self.event.delta = -15
        for _ in range(8):
            self.scroll()
        self.assertEqual(sum(call.args[0] for call in self.frame.canvas.yview_scroll.call_args_list), 3)
        self.assertEqual(self.frame._wheel_remainder["y"], 0)

    def test_shift_wheel_scrolls_horizontally(self):
        self.event.state = 1
        self.scroll()
        self.frame.canvas.xview_scroll.assert_called_once_with(3, "units")
        self.frame.canvas.yview_scroll.assert_not_called()

    def test_linux_wheel_buttons(self):
        for button, direction in ((4, -3), (5, 3)):
            self.event.num = button
            self.scroll("linux")
            self.frame.canvas.yview_scroll.assert_called_with(direction, "units")

    def test_wheel_does_not_intercept_other_windows(self):
        self.event.widget = SimpleNamespace(master=None)
        self.assertIsNone(self.scroll())
        self.frame.canvas.yview_scroll.assert_not_called()

    def test_wheel_does_not_scroll_when_content_fits(self):
        self.frame.canvas.yview.return_value = (0.0, 1.0)
        self.assertIsNone(self.scroll())
        self.frame.canvas.yview_scroll.assert_not_called()

    def test_scroll_region_preserves_content_in_small_window(self):
        self.frame.content.winfo_reqwidth.return_value = 744
        self.frame.content.winfo_reqheight.return_value = 950
        self.frame.canvas.winfo_width.return_value = 400
        self.frame.canvas.winfo_height.return_value = 300
        widgets.ScrollableFrame._sync_content(self.frame)
        self.frame.canvas.coords.assert_called_once_with(1, 0, 0)
        self.frame.canvas.configure.assert_called_once_with(scrollregion=(0, 0, 744, 950))

    def test_content_fills_larger_window(self):
        self.frame.content.winfo_reqwidth.return_value = 744
        self.frame.content.winfo_reqheight.return_value = 600
        self.frame.canvas.winfo_width.return_value = 1000
        self.frame.canvas.winfo_height.return_value = 800
        widgets.ScrollableFrame._sync_content(self.frame)
        self.frame.canvas.configure.assert_called_once_with(scrollregion=(0, 0, 1000, 800))
        self.frame.canvas.coords.assert_called_once_with(1, 128, 0)

    def test_scroll_region_updates_when_content_shrinks(self):
        self.frame.content.winfo_reqwidth.return_value = 744
        self.frame.content.winfo_reqheight.return_value = 950
        self.frame.canvas.winfo_width.return_value = 400
        self.frame.canvas.winfo_height.return_value = 300
        widgets.ScrollableFrame._sync_content(self.frame)
        self.frame.content.winfo_reqheight.return_value = 100
        widgets.ScrollableFrame._sync_content(self.frame)
        self.frame.canvas.configure.assert_called_with(scrollregion=(0, 0, 744, 300))
        self.frame.canvas.itemconfigure.assert_not_called()

    def test_initial_size_is_limited_to_available_screen(self):
        self.frame.content.winfo_reqwidth.return_value = 744
        self.frame.content.winfo_reqheight.return_value = 1200
        widgets.ScrollableFrame.fit_content(self.frame, max_width=900, max_height=700)
        self.frame.canvas.configure.assert_called_once_with(width=744, height=700)

    def test_nested_content_changes_schedule_one_bounds_update(self):
        event = SimpleNamespace(widget=SimpleNamespace(master=self.frame.content))
        widgets.ScrollableFrame._content_changed(self.frame, event)
        widgets.ScrollableFrame._content_changed(self.frame, event)
        self.frame.after_idle.assert_called_once_with(self.frame._sync_content)

    def test_other_windows_do_not_schedule_bounds_updates(self):
        event = SimpleNamespace(widget=SimpleNamespace(master=None))
        widgets.ScrollableFrame._content_changed(self.frame, event)
        self.frame.after_idle.assert_not_called()


class DiscoveryGuidanceTests(unittest.TestCase):
    def test_no_results_explains_vpn_and_mac_permissions(self):
        application = mock.Mock(cfg=Config())
        gui.App._lights_found(application, [], [])
        message = application._set_status.call_args.args[0]
        self.assertIn("VPN", message)
        self.assertIn("Local Network", message)
        self.assertIn("LAN Control", message)
        self.assertIn("guest Wi-Fi", message)

    def test_long_status_is_not_truncated(self):
        application = mock.Mock()
        message = "Network troubleshooting. " * 20
        gui.App._set_status(application, message)
        application.status.config.assert_called_once_with(text=message)


if __name__ == "__main__":
    unittest.main()
