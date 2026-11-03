import queue
import unittest
from types import SimpleNamespace
from unittest import mock

from musicmatch import gui, updates, widgets
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


class UpdateBannerTests(unittest.TestCase):
    def test_update_check_uses_worker_and_schedules_daily_check(self):
        application = mock.Mock()
        gui.App._check_updates(application)
        application.root.after.assert_called_once_with(
            updates.CHECK_INTERVAL_SECONDS * 1000, application._check_updates)
        work = application._background.call_args.args[0]
        update = updates.Update("1.0.4", "https://github.com/example")
        with mock.patch.object(updates, "check_for_update", return_value=update):
            work()
        application.events.put.assert_called_once_with(("update", update))

    def test_banner_is_above_scrollable_controls(self):
        application = mock.Mock()
        update = updates.Update("1.0.4", "https://github.com/example")
        gui.App._show_update(application, update)
        self.assertEqual(application._update, update)
        application.update_label.configure.assert_called_once_with(text="MusicMatch 1.0.4 is available.")
        application.update_banner.pack.assert_called_once_with(side="top", fill="x", before=application.viewport)

    def test_no_update_hides_banner(self):
        application = mock.Mock()
        gui.App._show_update(application, None)
        self.assertIsNone(application._update)
        application.update_banner.pack_forget.assert_called_once()

    def test_download_opens_browser_without_installing(self):
        update = updates.Update("1.0.4", "https://github.com/example")
        application = mock.Mock(_update=update)
        with mock.patch.object(gui.webbrowser, "open") as browser:
            gui.App._download_update(application)
        browser.assert_called_once_with(update.download_url)

    def test_download_does_nothing_without_an_update(self):
        with mock.patch.object(gui.webbrowser, "open") as browser:
            gui.App._download_update(mock.Mock(_update=None))
        browser.assert_not_called()

    def test_worker_event_updates_ui_on_main_thread(self):
        application = mock.Mock()
        update = updates.Update("1.0.4", "https://github.com/example")
        gui.App._handle(application, "update", update)
        application._show_update.assert_called_once_with(update)


class DiscoveryGuidanceTests(unittest.TestCase):
    def test_no_results_explains_vpn_and_mac_permissions(self):
        application = mock.Mock(cfg=Config())
        gui.App._lights_found(application, [], [])
        message = application._set_status.call_args.args[0]
        self.assertIn("VPN", message)
        self.assertIn("Local Network", message)
        self.assertIn("LAN Control", message)
        self.assertIn("guest Wi-Fi", message)
        self.assertIn("Private networks", message)

    def test_long_status_is_not_truncated(self):
        application = mock.Mock()
        message = "Network troubleshooting. " * 20
        gui.App._set_status(application, message)
        application.status.config.assert_called_once_with(text=message)


class BackgroundPollingTests(unittest.TestCase):
    def test_setup_state_does_not_crash_ui_polling(self):
        application = mock.Mock(_bg_running=True, _state_seen=None)
        state = {"status": "setup", "message": "No lights configured"}
        with mock.patch.object(gui.svc, "background_state", return_value=state):
            gui.App._poll_background(application)
        self.assertEqual(application._state_seen, ("setup", None, (), "No lights configured"))
        application._set_status.assert_called_once_with("No lights configured")
        application.hero.set_content.assert_called_once_with("Almost there", "No lights configured", "", None, None)

    def test_setup_message_changes_are_reflected(self):
        application = mock.Mock(_bg_running=True, _state_seen=("setup", None, (), "Old message"))
        with mock.patch.object(gui.svc, "background_state", return_value={"status": "setup", "message": "Connect Spotify"}):
            gui.App._poll_background(application)
        application.hero.set_content.assert_called_once_with("Almost there", "Connect Spotify", "", None, None)

    def test_event_loop_keeps_polling_after_setup_and_handles_scan_results(self):
        application = mock.Mock(_bg_running=True, _state_seen=None, _ticks=0)
        application.events = queue.Queue()
        application._handle.side_effect = lambda kind, data: gui.App._handle(application, kind, data)
        application.root.state.return_value = "normal"
        application._poll_background_if_changed.side_effect = lambda: gui.App._poll_background(application)
        state = {"status": "setup", "message": "No lights configured"}
        with mock.patch.object(gui.svc, "background_state", return_value=state):
            gui.App._drain_events(application)
            application.events.put(("lights", ([{"type": "wiz", "host": "192.168.1.20"}], [])))
            gui.App._drain_events(application)
        self.assertEqual(application.root.after.call_count, 2)
        self.assertEqual(application._handle.call_args.args[0], "lights")
        application._lights_found.assert_called_once_with([{"type": "wiz", "host": "192.168.1.20"}], [])


class ResponsiveLayoutTests(unittest.TestCase):
    def test_wide_screen_uses_capped_expanded_dashboard(self):
        self.assertEqual(gui.dashboard_layout(1920, 1), (2, 1120, 552))

    def test_medium_window_has_two_equal_columns(self):
        self.assertEqual(gui.dashboard_layout(900, 1), (2, 860, 422))

    def test_small_window_reflows_to_single_column(self):
        self.assertEqual(gui.dashboard_layout(600, 1), (1, 560, 560))

    def test_very_narrow_window_preserves_control_minimum(self):
        self.assertEqual(gui.dashboard_layout(320, 1), (1, 344, 344))

    def test_breakpoint_scales_with_display_density(self):
        self.assertEqual(gui.dashboard_layout(1000, 1.25), (2, 950, 465))
        self.assertEqual(gui.dashboard_layout(800, 1.25), (1, 750, 750))


class ResizingWidgetTests(unittest.TestCase):
    def test_stale_hero_render_is_ignored_after_resize(self):
        hero = mock.Mock(W=1120, H=132)
        widgets.Hero.show(hero, mock.Mock(size=(704, 132)))
        hero._crossfade.assert_not_called()

    def test_matching_hero_render_is_shown(self):
        hero = mock.Mock(W=1120, H=132)
        artwork = mock.Mock(size=(1120, 132))
        widgets.Hero.show(hero, artwork)
        hero._crossfade.assert_called_once_with(artwork)

    def test_card_resize_preserves_right_alignment_and_stretches_slider(self):
        card = mock.Mock(w=344)
        card.find_all.return_value = [1, 2, 3]
        card.type.side_effect = lambda item: {1: "text", 2: "window", 3: "image"}[item]
        card.itemcget.side_effect = lambda item, option: {(1, "anchor"): "e", (2, "anchor"): "w", (2, "window"): ".slider"}[(item, option)]
        slider = mock.Mock(spec=widgets.Slider, w=148)
        card.nametowidget.return_value = slider
        widgets.Card._resize(card, SimpleNamespace(width=552))
        card.move.assert_called_once_with(1, 208, 0)
        slider.configure.assert_called_once_with(width=356)
        slider.draw.assert_called_once()
        card._paint.assert_called_once()


if __name__ == "__main__":
    unittest.main()
