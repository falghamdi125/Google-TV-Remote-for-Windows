"""Minimal protobuf3 encoder/decoder.

Only the subset needed by the Android TV Remote v2 protocol is implemented:
varints, length-delimited fields (string/bytes/embedded messages) and the
varint-delimited stream framing the protocol uses on both ports.

Proto3 default-value semantics are honoured: scalar fields equal to their
default are not written, which is what protobuf.js does on the other side.
Embedded messages are always written when present, even when empty, because
the wrapper messages rely on field presence to tell requests apart.
"""

from __future__ import annotations

WIRE_VARINT = 0
WIRE_64BIT = 1
WIRE_LEN = 2
WIRE_32BIT = 5


def encode_varint(value: int) -> bytes:
    if value < 0:
        # proto3 encodes negative int32/enum as a 10-byte two's complement varint
        value += 1 << 64
    out = bytearray()
    while True:
        byte = value & 0x7F
        value >>= 7
        if value:
            out.append(byte | 0x80)
        else:
            out.append(byte)
            return bytes(out)


def decode_varint(buf: bytes | bytearray, pos: int) -> tuple[int, int]:
    """Return (value, new_pos). Raises IndexError if the buffer is truncated."""
    result = 0
    shift = 0
    while True:
        if pos >= len(buf):
            raise IndexError("truncated varint")
        byte = buf[pos]
        pos += 1
        result |= (byte & 0x7F) << shift
        if not byte & 0x80:
            return result, pos
        shift += 7
        if shift > 63:
            raise ValueError("varint too long")


class Writer:
    """Builds a protobuf message body."""

    def __init__(self) -> None:
        self._buf = bytearray()

    def _tag(self, field: int, wire: int) -> None:
        self._buf += encode_varint((field << 3) | wire)

    def varint(self, field: int, value: int | None) -> Writer:
        if value:  # proto3: skip zero/None
            self._tag(field, WIRE_VARINT)
            self._buf += encode_varint(value)
        return self

    def bool(self, field: int, value: bool | None) -> Writer:
        return self.varint(field, 1 if value else 0)

    def bytes(self, field: int, value: bytes | None) -> Writer:
        if value:
            self._tag(field, WIRE_LEN)
            self._buf += encode_varint(len(value))
            self._buf += value
        return self

    def string(self, field: int, value: str | None) -> Writer:
        if value:
            return self.bytes(field, value.encode("utf-8"))
        return self

    def message(self, field: int, sub: Writer | bytes | None) -> Writer:
        """Write an embedded message. Empty messages are still written."""
        if sub is None:
            return self
        raw = sub.to_bytes() if isinstance(sub, Writer) else sub
        self._tag(field, WIRE_LEN)
        self._buf += encode_varint(len(raw))
        self._buf += raw
        return self

    def to_bytes(self) -> bytes:
        return bytes(self._buf)

    def delimited(self) -> bytes:
        """Varint length prefix + body, the framing used on the wire."""
        raw = self.to_bytes()
        return encode_varint(len(raw)) + raw


def decode(buf: bytes) -> dict[int, list]:
    """Decode a message body into {field_number: [values]}.

    Varint fields yield ints, length-delimited fields yield bytes, and
    fixed-width fields yield their raw bytes. Unknown fields are kept.
    """
    out: dict[int, list] = {}
    pos = 0
    end = len(buf)
    while pos < end:
        key, pos = decode_varint(buf, pos)
        field, wire = key >> 3, key & 0x07
        if wire == WIRE_VARINT:
            value, pos = decode_varint(buf, pos)
        elif wire == WIRE_LEN:
            length, pos = decode_varint(buf, pos)
            if pos + length > end:
                raise IndexError("truncated length-delimited field")
            value = buf[pos:pos + length]
            pos += length
        elif wire == WIRE_64BIT:
            value = buf[pos:pos + 8]
            pos += 8
        elif wire == WIRE_32BIT:
            value = buf[pos:pos + 4]
            pos += 4
        else:
            raise ValueError(f"unsupported wire type {wire}")
        out.setdefault(field, []).append(value)
    return out


def get_varint(msg: dict[int, list], field: int, default: int = 0) -> int:
    values = msg.get(field)
    return values[0] if values else default


def get_bytes(msg: dict[int, list], field: int, default: bytes = b"") -> bytes:
    values = msg.get(field)
    return values[0] if values else default


def get_string(msg: dict[int, list], field: int, default: str = "") -> str:
    values = msg.get(field)
    if not values:
        return default
    return values[0].decode("utf-8", "replace")


def get_message(msg: dict[int, list], field: int) -> dict[int, list] | None:
    values = msg.get(field)
    if not values:
        return None
    return decode(values[0])


def has(msg: dict[int, list], field: int) -> bool:
    return field in msg


class DelimitedReader:
    """Reassembles varint-delimited messages from a TCP byte stream."""

    def __init__(self) -> None:
        self._buf = bytearray()

    def feed(self, data: bytes) -> list[bytes]:
        """Add received bytes, return every complete message body available."""
        self._buf += data
        messages: list[bytes] = []
        while True:
            try:
                length, header_len = decode_varint(self._buf, 0)
            except (IndexError, ValueError):
                break  # incomplete length prefix
            if len(self._buf) < header_len + length:
                break  # incomplete body
            messages.append(bytes(self._buf[header_len:header_len + length]))
            del self._buf[:header_len + length]
        return messages
