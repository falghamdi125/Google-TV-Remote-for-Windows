"""Parses a hand-built mDNS response (with name compression) into a Device."""

from __future__ import annotations

import struct
import unittest

from gtvremote import discovery
from gtvremote.discovery import CLASS_IN, TYPE_A, TYPE_PTR, TYPE_SRV, TYPE_TXT, _encode_name


def pointer(offset: int) -> bytes:
    return struct.pack("!H", 0xC000 | offset)


def fixed(rtype: int, rdlength: int) -> bytes:
    return struct.pack("!HHIH", rtype, CLASS_IN, 120, rdlength)


def build_response() -> bytes:
    """One PTR answer plus SRV, TXT and A additionals, the way a TV replies."""
    packet = bytearray(struct.pack("!HHHHHH", 0, 0x8400, 0, 1, 0, 3))

    service_at = len(packet)
    ptr_rdata = bytes([14]) + b"Living Room TV" + pointer(service_at)
    packet += _encode_name(discovery.SERVICE) + fixed(TYPE_PTR, len(ptr_rdata))
    instance_at = len(packet)
    packet += ptr_rdata

    srv_rdata = struct.pack("!HHH", 0, 0, 6466) + _encode_name("living-room.local")
    packet += pointer(instance_at) + fixed(TYPE_SRV, len(srv_rdata))
    target_at = len(packet) + 6
    packet += srv_rdata

    txt_rdata = b"\x08bt=AA:BB" + b"\x07model=X"
    packet += pointer(instance_at) + fixed(TYPE_TXT, len(txt_rdata)) + txt_rdata

    packet += pointer(target_at) + fixed(TYPE_A, 4) + bytes([192, 168, 1, 42])
    return bytes(packet)


class ParseResponseTest(unittest.TestCase):
    def test_compressed_names_join_into_a_device(self):
        records = discovery._new_records()
        discovery._parse_response(build_response(), records)
        devices = discovery._devices_from_records(records)

        self.assertEqual(len(devices), 1)
        device = devices[0]
        self.assertEqual((device.name, device.host, device.port),
                         ("Living Room TV", "192.168.1.42", 6466))
        self.assertEqual(device.properties, {"bt": "AA:BB", "model": "X"})
        self.assertEqual(device.label, "Living Room TV  -  192.168.1.42")

    def test_malformed_packets_are_ignored(self):
        records = discovery._new_records()
        for junk in (b"", b"\x00" * 5,
                     struct.pack("!HHHHHH", 0, 0, 0, 1, 0, 0) + b"\xc0\xff",   # bad pointer
                     struct.pack("!HHHHHH", 0, 0, 0, 1, 0, 0) + b"\x05ab"):     # truncated
            with self.subTest(packet=junk):
                discovery._parse_response(junk, records)
        self.assertEqual(records, discovery._new_records())
        self.assertEqual(discovery._devices_from_records(records), [])

    def test_query_is_well_formed(self):
        query = discovery._build_query()
        self.assertEqual(query[:12], struct.pack("!HHHHHH", 0, 0, 1, 0, 0, 0))
        name, pos = discovery._read_name(query, 12)
        self.assertEqual(name, discovery.SERVICE)
        self.assertEqual(query[pos:], struct.pack("!HH", TYPE_PTR, CLASS_IN))


if __name__ == "__main__":
    unittest.main()
