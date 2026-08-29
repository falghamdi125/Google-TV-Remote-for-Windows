"""Builds the whole window, pushes every event kind through the pump and
rescales it. Skipped when no display is available (headless CI)."""

from __future__ import annotations

import tkinter as tk
import unittest
from unittest import mock

from gtvremote import config
from gtvremote.ui import RemoteApp, icons, theme, window

SETTINGS = {**config.DEFAULTS, "auto_connect": False, "window_geometry": ""}

EVENTS = [
    ("state", "connected", "Connected to 10.0.0.5"),
    ("volume", 12, 100, False),
    ("volume", 0, 100, True),
    ("power", True),
    ("power", False),
    ("app", "com.netflix.ninja"),
    ("state", "error", "Cannot reach host"),
    ("state", "reconnecting", "Reconnecting in 2s..."),
    ("state", "unpaired", "The TV does not recognise this remote."),
    ("state", "disconnected", "Disconnected"),
]


class WindowTest(unittest.TestCase):
    def setUp(self) -> None:
        # Keep the tests off the real TV, out of the saved settings, and free
        # of modal dialogs.
        for patcher in (
            mock.patch.object(config, "load", return_value=dict(SETTINGS)),
            mock.patch.object(config, "save"),
            mock.patch.object(window.messagebox, "askyesno", return_value=False),
            mock.patch.object(window.messagebox, "showinfo"),
            mock.patch.object(window.messagebox, "showerror"),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)
        self.addCleanup(theme.apply_scale, theme.DEFAULT_SCALE)

        try:
            self.root = tk.Tk()
        except tk.TclError as exc:
            self.skipTest(f"no display available: {exc}")
        self.app: RemoteApp | None = None
        self.addCleanup(self.close_window)
        self.root.withdraw()
        self.app = RemoteApp(self.root)
        self.root.update()

    def close_window(self) -> None:
        # Go through on_close so the event pump is cancelled with the window.
        try:
            if self.app is not None:
                self.app.on_close()
            else:
                self.root.destroy()
        except tk.TclError:
            pass

    def enabled_buttons(self) -> int:
        return sum(1 for button in self.app._buttons if button._enabled)

    def test_builds_every_control_disabled(self):
        self.assertGreaterEqual(len(self.app._buttons), 20)
        self.assertEqual(self.enabled_buttons(), 0)
        self.assertEqual(self.app.status_var.get(), "Not connected")
        self.assertIsNone(self.app.client)

    def test_event_pump_handles_every_event_kind(self):
        for event in EVENTS:
            with self.subTest(event=event[0:2]):
                self.app._handle_event(event)
                self.root.update()
        self.assertEqual(self.app.status_var.get(), "Disconnected")
        self.assertEqual(self.enabled_buttons(), 0)

    def test_connected_state_enables_controls(self):
        self.app._handle_event(("state", "connected", "Connected to 10.0.0.5"))
        self.assertEqual(self.enabled_buttons(), len(self.app._buttons))
        self.app._handle_event(("volume", 5, 100, False))
        self.assertEqual(self.app.volume_var.get(), "vol 5/100")
        self.app._handle_event(("volume", 5, 100, True))
        self.assertEqual(self.app.volume_var.get(), "muted")
        self.app._handle_event(("app", "com.example." + "x" * 60))
        self.assertLessEqual(len(self.app.app_var.get()), window.MAX_APP_LABEL)
        self.app._handle_event(("state", "error", "Cannot reach host"))
        self.assertEqual(self.enabled_buttons(), 0)

    def test_unpaired_offers_to_pair(self):
        self.app.host_var.set("10.0.0.5")
        window.messagebox.askyesno.return_value = True
        with mock.patch.object(self.app, "repair") as repair:
            self.app._handle_event(("state", "unpaired", "not paired"))
        repair.assert_called_once_with(confirm=False)

    def test_sending_without_a_connection_is_harmless(self):
        self.app.send_key(3)
        self.app.toggle_power()
        self.app.launch_app("netflix://", "Netflix")
        self.app.text_var.set("hello")
        self.app.send_text()
        self.app._show_input_menu()
        self.root.update()
        self.assertEqual(self.app.status_var.get(), "Not connected.")
        self.assertEqual(self.app.text_var.get(), "hello", "text kept for a retry")

    def test_typing_goes_to_the_client(self):
        client = mock.Mock(is_connected=True)
        self.app.client = client
        self.app.text_var.set("hello")
        self.app.send_text()
        client.send_text.assert_called_once_with("hello")
        self.assertEqual(self.app.text_var.get(), "")
        self.assertEqual(self.app.status_var.get(), "Text sent to the TV")

    def test_rows_line_up(self):
        self.root.deiconify()
        self.root.update()
        rows = [button for button in self.app._buttons
                if isinstance(button.master, window.EvenRow)]
        self.assertGreaterEqual(len(rows), 15)
        lefts = {button.winfo_x() for button in rows if button.winfo_x() < 5}
        self.assertEqual(lefts, {0}, "every row starts at the same edge")
        right_edges = {button.master.winfo_width() - (button.winfo_x() + button.winfo_width())
                       for button in rows}
        self.assertIn(0, right_edges, "rows end flush with their container")

    def test_app_buttons_carry_icons_and_names(self):
        app_buttons = self.app._buttons[-len(config.DEFAULT_APPS):]
        for button, (name, _link) in zip(app_buttons, config.DEFAULT_APPS):
            with self.subTest(app=name):
                self.assertTrue(button.find_withtag("icon"), "icon drawn")
                self.assertEqual(button.itemcget(button._label, "text"), "")
        self.app._set_controls_enabled(True)
        self.assertTrue(all(b.find_withtag("icon") for b in app_buttons))

    def test_rescale_rebuilds_the_window_and_keeps_state(self):
        before = len(self.app._buttons)
        self.app.host_var.set("192.168.1.9")
        self.app._handle_event(("volume", 7, 100, False))
        self.app.set_scale(theme.DEFAULT_SCALE + 0.3)
        self.root.update()
        self.assertEqual(len(self.app._buttons), before)
        self.assertEqual(self.app.host_var.get(), "192.168.1.9")
        self.assertEqual(self.app.volume_var.get(), "vol 7/100")
        self.assertAlmostEqual(self.app.settings["ui_scale"], theme.DEFAULT_SCALE + 0.3)
        config.save.assert_called()


class IconTest(unittest.TestCase):
    def setUp(self) -> None:
        try:
            self.root = tk.Tk()
        except tk.TclError as exc:
            self.skipTest(f"no display available: {exc}")
        self.addCleanup(self.root.destroy)
        self.canvas = tk.Canvas(self.root)

    def test_known_apps_get_their_own_icon(self):
        self.assertIs(icons.app_icon("YouTube"), icons.youtube)
        self.assertIs(icons.app_icon("Prime Video"), icons.prime_video)
        self.assertIs(icons.app_icon("Amazon Prime"), icons.prime_video)
        self.assertIsNot(icons.app_icon("Plex"), icons.youtube)

    def test_every_icon_draws_in_both_states(self):
        for name in ("YouTube", "Netflix", "Prime Video", "Disney+", "Spotify", "Plex", ""):
            for enabled in (True, False):
                with self.subTest(app=name, enabled=enabled):
                    self.canvas.delete("icon")
                    icons.app_icon(name)(self.canvas, 20, 20, 24, enabled)
                    self.assertTrue(self.canvas.find_withtag("icon"))

    def test_rows_are_balanced(self):
        self.assertEqual(window.balanced_rows(list(range(5)), 6), [[0, 1, 2, 3, 4]])
        self.assertEqual(window.balanced_rows(list(range(7)), 6), [[0, 1, 2, 3], [4, 5, 6]])
        self.assertEqual(window.balanced_rows([], 6), [])


if __name__ == "__main__":
    unittest.main()
