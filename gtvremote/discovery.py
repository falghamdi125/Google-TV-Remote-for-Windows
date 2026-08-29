"""Find Google TV / Android TV devices on the LAN via mDNS.

Devices that support the v2 remote protocol advertise the service type
``_androidtvremote2._tcp.local``. This is a small purpose-built mDNS client
so the app has no third-party discovery dependency.
"""

from __future__ import annotations

import select
import socket
import struct
import time
from dataclasses import dataclass, field

MDNS_ADDR = "224.0.0.251"
MDNS_PORT = 5353
SERVICE = "_androidtvremote2._tcp.local"
DEFAULT_TIMEOUT = 3.0

TYPE_A = 1
TYPE_PTR = 12
TYPE_TXT = 16
TYPE_SRV = 33
CLASS_IN = 1

HEADER = struct.Struct("!HHHHHH")
RR_FIXED = struct.Struct("!HHIH")          # type, class, ttl, rdlength


@dataclass
class Device:
    name: str
    host: str
    port: int = 6466
    properties: dict = field(default_factory=dict)

    @property
    def label(self) -> str:
        return f"{self.name}  -  {self.host}" if self.name else self.host


# -------------------------------------------------------------- packets ---

def _encode_name(name: str) -> bytes:
    out = bytearray()
    for label in name.rstrip(".").split("."):
        raw = label.encode("utf-8")
        out.append(len(raw))
        out += raw
    out.append(0)
    return bytes(out)


def _build_query(service: str = SERVICE) -> bytes:
    header = HEADER.pack(0, 0, 1, 0, 0, 0)
    return header + _encode_name(service) + struct.pack("!HH", TYPE_PTR, CLASS_IN)


def _read_name(data: bytes, pos: int) -> tuple[str, int]:
    """Read a (possibly compressed) DNS name. Returns (name, pos_after)."""
    labels: list[str] = []
    jumped = False
    after = pos
    hops = 0
    while True:
        if pos >= len(data):
            raise ValueError("truncated name")
        length = data[pos]
        if length & 0xC0 == 0xC0:                      # compression pointer
            if pos + 1 >= len(data):
                raise ValueError("truncated pointer")
            pointer = ((length & 0x3F) << 8) | data[pos + 1]
            if not jumped:
                after = pos + 2
                jumped = True
            pos = pointer
            hops += 1
            if hops > 32:
                raise ValueError("name pointer loop")
            continue
        pos += 1
        if length == 0:
            if not jumped:
                after = pos
            return ".".join(labels), after
        labels.append(data[pos:pos + length].decode("utf-8", "replace"))
        pos += length


def _parse_txt(blob: bytes) -> dict:
    out: dict = {}
    pos = 0
    while pos < len(blob):
        length = blob[pos]
        pos += 1
        item = blob[pos:pos + length]
        pos += length
        if b"=" in item:
            key, _, value = item.partition(b"=")
            out[key.decode("utf-8", "replace")] = value.decode("utf-8", "replace")
    return out


def _new_records() -> dict:
    return {"ptr": {}, "srv": {}, "a": {}, "txt": {}}


def _parse_response(data: bytes, records: dict) -> None:
    """Accumulate PTR/SRV/A/TXT records from one mDNS packet.

    Malformed packets are dropped; whatever was parsed before the damage is
    kept.
    """
    try:
        _parse_records(data, records)
    except (ValueError, IndexError, struct.error):
        pass


def _parse_records(data: bytes, records: dict) -> None:
    if len(data) < HEADER.size:
        return
    _, _, qdcount, ancount, nscount, arcount = HEADER.unpack_from(data)
    pos = HEADER.size
    for _ in range(qdcount):                            # skip questions
        _, pos = _read_name(data, pos)
        pos += 4
    for _ in range(ancount + nscount + arcount):
        if pos >= len(data):
            return
        name, pos = _read_name(data, pos)
        if pos + RR_FIXED.size > len(data):
            return
        rtype, _rclass, _ttl, rdlength = RR_FIXED.unpack_from(data, pos)
        pos += RR_FIXED.size
        rdata = data[pos:pos + rdlength]
        end = pos + rdlength
        try:
            if rtype == TYPE_PTR:
                target, _ = _read_name(data, pos)
                records["ptr"].setdefault(name, set()).add(target)
            elif rtype == TYPE_SRV and rdlength >= 6:
                _prio, _weight, port = struct.unpack("!HHH", rdata[:6])
                target, _ = _read_name(data, pos + 6)
                records["srv"][name] = (target, port)
            elif rtype == TYPE_A and rdlength == 4:
                records["a"][name] = socket.inet_ntoa(rdata)
            elif rtype == TYPE_TXT:
                records["txt"][name] = _parse_txt(rdata)
        except ValueError:
            pass
        pos = end


def _devices_from_records(records: dict, service: str = SERVICE) -> list[Device]:
    """Join PTR -> SRV -> A (+ TXT) records into Device objects."""
    devices: dict[str, Device] = {}
    instances = records["ptr"].get(service, set()) or set(records["srv"])
    for instance in instances:
        entry = records["srv"].get(instance)
        if not entry:
            continue
        target, port = entry
        host = records["a"].get(target)
        if not host:
            continue
        name = instance.split("." + service.split(".")[0])[0].rstrip(".")
        devices[host] = Device(name=name or target.rstrip("."), host=host, port=port,
                               properties=records["txt"].get(instance, {}))
    return sorted(devices.values(), key=lambda d: d.name.lower())


# -------------------------------------------------------------- network ---

def _local_addresses() -> list[str]:
    addresses = {"0.0.0.0"}
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            addresses.add(info[4][0])
    except OSError:
        pass
    return sorted(addresses)


def _open_query_sockets(query: bytes) -> list[socket.socket]:
    """Send the query from every local interface; multi-homed PCs otherwise
    tend to multicast on the wrong one."""
    sockets: list[socket.socket] = []
    for address in _local_addresses():
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 255)
            try:
                sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_IF,
                                socket.inet_aton(address))
            except OSError:
                pass
            sock.bind((address, 0))
            sock.setblocking(False)
            sock.sendto(query, (MDNS_ADDR, MDNS_PORT))
        except OSError:
            sock.close()
            continue
        sockets.append(sock)
    return sockets


def discover(timeout: float = DEFAULT_TIMEOUT, service: str = SERVICE) -> list[Device]:
    """Broadcast an mDNS query and collect the devices that answer."""
    records = _new_records()
    sockets = _open_query_sockets(_build_query(service))
    if not sockets:
        return []

    deadline = time.monotonic() + timeout
    try:
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            readable, _, _ = select.select(sockets, [], [], min(remaining, 0.5))
            for sock in readable:
                try:
                    data, _addr = sock.recvfrom(9000)
                except OSError:
                    continue
                _parse_response(data, records)
    finally:
        for sock in sockets:
            sock.close()

    return _devices_from_records(records, service)
