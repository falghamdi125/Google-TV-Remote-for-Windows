"""Which input-switching entries the Input menu offers."""

from __future__ import annotations

import unittest

from gtvremote import inputs, keycodes


class InputSourcesTest(unittest.TestCase):
    def test_tcl_uses_passthrough_links(self):
        sources = inputs.input_sources("TCL")
        labels = [label for label, _ in sources]
        self.assertEqual(labels[:4], ["HDMI 1", "HDMI 2", "HDMI 3", "HDMI 4"])
        kind, link = dict(sources)["HDMI 1"]
        self.assertEqual(kind, "link")
        self.assertEqual(link, "content://android.media.tv/passthrough/"
                               "com.tcl.tvinput%2F.passthroughinput.TvPassThroughService"
                               "%2FHW1413744128")
        self.assertEqual(dict(sources)["Input picker"], ("key", keycodes.KEYCODE_TV_INPUT))
        self.assertEqual(inputs.input_sources(" tcl "), sources, "vendor match is lenient")

    def test_unknown_vendor_falls_back_to_key_codes(self):
        for vendor in ("Sony", "", None):
            with self.subTest(vendor=vendor):
                sources = inputs.input_sources(vendor)
                self.assertEqual(sources, inputs.KEY_SOURCES)
                self.assertTrue(all(kind == "key" for _, (kind, _) in sources))

    def test_settings_override_everything(self):
        settings = {"inputs": [
            {"name": "Console", "link": "com.sony.dtv.tvinput/.HDMI1"},     # bare id
            {"name": "Sky", "link": "content://android.media.tv/passthrough/x"},
            {"name": "Picker", "key": "KEYCODE_TV_INPUT"},
            {"name": "Raw", "key": 178},
            {"name": "Bogus", "key": "KEYCODE_NOPE"},                       # skipped
            {"link": "no-name"},                                            # skipped
            "garbage",                                                      # skipped
        ]}
        self.assertEqual(inputs.input_sources("TCL", settings), [
            ("Console", ("link", "content://android.media.tv/passthrough/"
                                 "com.sony.dtv.tvinput%2F.HDMI1")),
            ("Sky", ("link", "content://android.media.tv/passthrough/x")),
            ("Picker", ("key", keycodes.KEYCODE_TV_INPUT)),
            ("Raw", ("key", 178)),
        ])

    def test_empty_settings_do_not_override(self):
        self.assertEqual(inputs.input_sources("TCL", {"inputs": []}),
                         inputs.input_sources("TCL"))


if __name__ == "__main__":
    unittest.main()
