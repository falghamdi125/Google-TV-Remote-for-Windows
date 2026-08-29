"""Unit tests for the minimal protobuf implementation and the message parsers.

The TV-side fixtures were encoded by protobufjs from the real .proto files
(see make_fixtures.js), so decoding them checks against a genuine encoder.
"""

from __future__ import annotations

import json
import unittest
from pathlib import Path

from gtvremote import messages
from gtvremote.protobuf_lite import (DelimitedReader, Writer, decode, decode_varint,
                                     encode_varint, get_bytes, get_message, get_string,
                                     get_varint, has)

FIXTURES = json.loads((Path(__file__).parent / "fixtures.json").read_text())


def fixture_body(name: str) -> bytes:
    """A fixture with its length prefix stripped."""
    (body,) = DelimitedReader().feed(bytes.fromhex(FIXTURES[name]))
    return body


class VarintTest(unittest.TestCase):
    def test_round_trip(self):
        for value in (0, 1, 127, 128, 300, 16383, 16384, 2**32, 2**63 - 1):
            with self.subTest(value=value):
                encoded = encode_varint(value)
                self.assertEqual(decode_varint(encoded, 0), (value, len(encoded)))

    def test_known_encodings(self):
        self.assertEqual(encode_varint(1), b"\x01")
        self.assertEqual(encode_varint(300), b"\xac\x02")

    def test_negative_uses_ten_bytes(self):
        encoded = encode_varint(-1)
        self.assertEqual(len(encoded), 10)
        self.assertEqual(decode_varint(encoded, 0)[0], 2**64 - 1)

    def test_truncated_raises(self):
        with self.assertRaises(IndexError):
            decode_varint(b"\x80", 0)

    def test_overlong_raises(self):
        with self.assertRaises(ValueError):
            decode_varint(b"\xff" * 11, 0)


class WriterTest(unittest.TestCase):
    def test_proto3_defaults_are_omitted(self):
        raw = Writer().varint(1, 0).bool(2, False).string(3, "").bytes(4, b"").to_bytes()
        self.assertEqual(raw, b"")

    def test_empty_embedded_message_is_still_written(self):
        self.assertEqual(Writer().message(31, Writer()).to_bytes(), b"\xfa\x01\x00")
        self.assertEqual(Writer().message(1, None).to_bytes(), b"")

    def test_fields_and_tags(self):
        raw = Writer().varint(1, 2).string(2, "hi").bool(3, True).to_bytes()
        self.assertEqual(raw, b"\x08\x02\x12\x02hi\x18\x01")

    def test_delimited_adds_a_varint_length_prefix(self):
        body = Writer().string(1, "x" * 200)
        framed = body.delimited()
        length, header = decode_varint(framed, 0)
        self.assertEqual(header, 2)                     # 203 bytes needs two
        self.assertEqual(length, len(body.to_bytes()))
        self.assertEqual(framed[header:], body.to_bytes())


class DecodeTest(unittest.TestCase):
    def test_decodes_what_writer_produced(self):
        raw = (Writer().varint(1, 622).string(2, "Windows PC").bytes(3, b"\x00\x01")
               .message(4, Writer().varint(1, 7)).varint(1, 9).to_bytes())
        msg = decode(raw)
        self.assertEqual(msg[1], [622, 9])              # repeated values keep order
        self.assertEqual(get_varint(msg, 1), 622)
        self.assertEqual(get_string(msg, 2), "Windows PC")
        self.assertEqual(get_bytes(msg, 3), b"\x00\x01")
        self.assertEqual(get_varint(get_message(msg, 4), 1), 7)
        self.assertTrue(has(msg, 4))
        self.assertFalse(has(msg, 5))
        self.assertIsNone(get_message(msg, 5))
        self.assertEqual(get_string(msg, 5, "default"), "default")

    def test_fixed_width_fields_are_kept_as_raw_bytes(self):
        raw = b"\x0d\x01\x02\x03\x04" + b"\x11" + bytes(8) + b"\x18\x05"
        msg = decode(raw)
        self.assertEqual(msg[1], [b"\x01\x02\x03\x04"])
        self.assertEqual(len(msg[2][0]), 8)
        self.assertEqual(get_varint(msg, 3), 5)

    def test_truncated_length_delimited_field_raises(self):
        with self.assertRaises(IndexError):
            decode(b"\x12\x05ab")


class DelimitedReaderTest(unittest.TestCase):
    STREAM = b"".join(bytes.fromhex(FIXTURES[name])
                      for name in ("configure", "ping", "long_app_link"))

    def test_reassembles_messages_fed_one_byte_at_a_time(self):
        reader = DelimitedReader()
        out: list[bytes] = []
        for index in range(len(self.STREAM)):
            out += reader.feed(self.STREAM[index:index + 1])
        self.assertEqual(len(out), 3)
        self.assertEqual(messages.parse_remote(out[1]), {"kind": "ping", "val1": 42})
        # The long message needs a two-byte length prefix.
        link = get_message(decode(out[2]), messages.RM_APP_LINK_LAUNCH_REQUEST)
        self.assertEqual(get_string(link, 1), "https://example.com/" + "x" * 300)

    def test_several_messages_in_one_chunk(self):
        self.assertEqual(len(DelimitedReader().feed(self.STREAM)), 3)
        self.assertEqual(DelimitedReader().feed(b""), [])


class ParseRemoteTest(unittest.TestCase):
    def test_fixtures_from_protobufjs(self):
        expected = {
            "configure": {"kind": "configure", "model": "Chromecast", "vendor": "Google"},
            "set_active": {"kind": "set_active"},
            "ping": {"kind": "ping", "val1": 42},
            "start_on": {"kind": "start", "started": True},
            "volume": {"kind": "volume", "player_model": "Living Room TV",
                       "volume_max": 100, "volume_level": 37, "volume_muted": False},
            "current_app": {"kind": "current_app",
                            "app_package": "com.google.android.youtube.tv",
                            "field_counter": 1, "field_value": "", "field_label": "Search"},
            "ime_batch_edit": {"kind": "ime_batch_edit", "ime_counter": 3, "field_counter": 7},
            "ime_show_request": {"kind": "ime_show_request", "counter_field": 7,
                                 "value": "", "label": "Search"},
            "long_app_link": {"kind": None},
        }
        for name, parsed in expected.items():
            with self.subTest(fixture=name):
                self.assertEqual(messages.parse_remote(fixture_body(name)), parsed)

    def test_outgoing_messages_carry_the_right_fields(self):
        (body,) = DelimitedReader().feed(messages.remote_key_inject(23, 3))
        key = get_message(decode(body), messages.RM_KEY_INJECT)
        self.assertEqual((get_varint(key, 1), get_varint(key, 2)), (23, 3))

        (body,) = DelimitedReader().feed(messages.remote_configure("M", "V", "pkg", "1.2"))
        configure = get_message(decode(body), messages.RM_CONFIGURE)
        self.assertEqual(get_varint(configure, 1), messages.CONFIGURE_CODE)
        info = get_message(configure, 2)
        self.assertEqual([get_string(info, f) for f in (1, 2, 5, 6)], ["M", "V", "pkg", "1.2"])

        (body,) = DelimitedReader().feed(messages.remote_ime_batch_edit(3, 7, "héllo", 4, 4))
        batch = get_message(decode(body), messages.RM_IME_BATCH_EDIT)
        self.assertEqual((get_varint(batch, 1), get_varint(batch, 2)), (3, 7))
        edit = get_message(batch, 3)
        self.assertEqual(get_varint(edit, 1), 1)
        field = get_message(edit, 2)
        self.assertEqual((get_varint(field, 1), get_varint(field, 2), get_string(field, 3)),
                         (4, 4, "héllo"))


class ParsePairingTest(unittest.TestCase):
    @staticmethod
    def from_tv(field: int, body: Writer, status: int = messages.STATUS_OK) -> dict:
        framed = (Writer().varint(1, messages.PROTOCOL_VERSION).varint(2, status)
                  .message(field, body).delimited())
        (raw,) = DelimitedReader().feed(framed)
        return messages.parse_pairing(raw)

    def test_request_ack_carries_the_server_name(self):
        msg = self.from_tv(messages.PM_PAIRING_REQUEST_ACK, Writer().string(1, "Bedroom TV"))
        self.assertEqual(msg["status"], messages.STATUS_OK)
        self.assertTrue(msg["request_ack"])
        self.assertEqual(msg["server_name"], "Bedroom TV")
        self.assertFalse(msg["option"] or msg["configuration_ack"] or msg["secret_ack"])

    def test_error_status_is_reported(self):
        msg = self.from_tv(messages.PM_PAIRING_SECRET_ACK, Writer(),
                           status=messages.STATUS_BAD_SECRET)
        self.assertEqual(msg["status"], messages.STATUS_BAD_SECRET)
        self.assertTrue(msg["secret_ack"])

    def test_outgoing_request_parses_back(self):
        (raw,) = DelimitedReader().feed(messages.pairing_request("svc", "PC"))
        msg = decode(raw)
        self.assertEqual(get_varint(msg, messages.PM_PROTOCOL_VERSION), 2)
        self.assertEqual(get_varint(msg, messages.PM_STATUS), messages.STATUS_OK)
        request = get_message(msg, messages.PM_PAIRING_REQUEST)
        self.assertEqual((get_string(request, 1), get_string(request, 2)), ("svc", "PC"))


if __name__ == "__main__":
    unittest.main()
