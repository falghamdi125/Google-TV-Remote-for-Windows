"""Settings file round trips, device memory and app-shortcut parsing."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from gtvremote import config


class SettingsFileTest(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.directory = Path(tmp.name)
        patcher = mock.patch.object(config, "config_dir", return_value=self.directory)
        patcher.start()
        self.addCleanup(patcher.stop)

    def settings_file(self) -> Path:
        return self.directory / config.SETTINGS_FILE

    def test_missing_file_gives_defaults(self):
        self.assertEqual(config.load(), config.DEFAULTS)

    def test_defaults_are_not_shared_with_callers(self):
        config.load()["devices"].append({"host": "x"})
        self.assertEqual(config.DEFAULTS["devices"], [])

    def test_round_trip_drops_unknown_keys(self):
        data = {**config.DEFAULTS, "last_host": "10.0.0.7", "ui_scale": 1.2, "bogus": 1}
        config.save(data)
        stored = json.loads(self.settings_file().read_text())
        self.assertNotIn("bogus", stored)
        loaded = config.load()
        self.assertEqual(loaded["last_host"], "10.0.0.7")
        self.assertEqual(loaded["ui_scale"], 1.2)
        self.assertNotIn("bogus", loaded)

    def test_corrupt_file_gives_defaults(self):
        self.settings_file().write_text("{not json")
        self.assertEqual(config.load(), config.DEFAULTS)

    def test_utf8_bom_is_tolerated(self):
        self.settings_file().write_text(json.dumps({"last_host": "tv"}), encoding="utf-8-sig")
        self.assertEqual(config.load()["last_host"], "tv")


class RememberDeviceTest(unittest.TestCase):
    def test_most_recent_first_without_duplicates(self):
        data = config.load()
        config.remember_device(data, "10.0.0.1", "Bedroom")
        config.remember_device(data, "10.0.0.2", "Living room")
        config.remember_device(data, "10.0.0.1", "Bedroom")
        self.assertEqual([d["host"] for d in data["devices"]], ["10.0.0.1", "10.0.0.2"])
        self.assertEqual(data["devices"][0]["name"], "Bedroom")
        self.assertEqual(data["last_host"], "10.0.0.1")

    def test_list_is_capped(self):
        data = config.load()
        for index in range(config.MAX_REMEMBERED_DEVICES + 5):
            config.remember_device(data, f"10.0.0.{index}")
        self.assertEqual(len(data["devices"]), config.MAX_REMEMBERED_DEVICES)
        self.assertEqual(data["devices"][0]["name"], data["devices"][0]["host"])


class AppShortcutsTest(unittest.TestCase):
    def test_empty_setting_uses_defaults(self):
        self.assertEqual(config.app_shortcuts({"apps": []}), config.DEFAULT_APPS)
        self.assertEqual(config.app_shortcuts({}), config.DEFAULT_APPS)

    def test_accepts_dicts_pairs_and_bare_packages(self):
        apps = config.app_shortcuts({"apps": [
            {"name": "Plex", "link": "plex://"},
            ["Twitch", "tv.twitch.android.app"],
            {"name": "", "link": "x://"},               # skipped: no name
            {"name": "Nothing"},                        # skipped: no link
            "garbage",                                  # skipped: wrong shape
        ]})
        self.assertEqual(apps, [("Plex", "plex://"),
                                ("Twitch", "market://launch?id=tv.twitch.android.app")])


if __name__ == "__main__":
    unittest.main()
