"""End-to-end tests against a mock Google TV.

A real TLS server speaks the pairing and remote protocols the way a TV does,
and the tests drive the actual client code against it: certificate exchange,
the pairing hash agreement, the remote handshake, ping/pong, state callbacks,
key injection and reconnection.

The TV-side messages are bytes encoded by protobufjs from the real .proto
files (tests/fixtures.json, produced by make_fixtures.js), so the decoder is
checked against a genuine protobuf implementation rather than against itself.
"""

from __future__ import annotations

import datetime
import hashlib
import json
import socket
import ssl
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

from gtvremote import certs, keycodes, messages
from gtvremote.pairing import BadCodeError, PairingSession
from gtvremote.protobuf_lite import (DelimitedReader, Writer, decode, get_bytes,
                                     get_message, get_string, get_varint)
from gtvremote.remote import RemoteClient

FIXTURES = json.loads((Path(__file__).parent / "fixtures.json").read_text())
LOCALHOST = "127.0.0.1"


def wait_for(condition, timeout: float = 5.0, interval: float = 0.02) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if condition():
            return True
        time.sleep(interval)
    return bool(condition())


# --------------------------------------------------------------- mock TV ---

def make_server_cert(directory: Path) -> tuple[Path, Path]:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Mock TV")])
    now = datetime.datetime.now(datetime.timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name).issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(days=1))
        .not_valid_after(now + datetime.timedelta(days=365))
        .sign(key, hashes.SHA256())
    )
    cert_path, key_path = directory / "server.crt", directory / "server.key"
    key_path.write_bytes(key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.TraditionalOpenSSL,
        serialization.NoEncryption()))
    cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    return cert_path, key_path


def server_context(server_cert: Path, server_key: Path, trusted_cert: Path) -> ssl.SSLContext:
    """A TV requires a client certificate and only accepts ones it has paired with."""
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(str(server_cert), str(server_key))
    context.load_verify_locations(cafile=str(trusted_cert))
    context.verify_mode = ssl.CERT_REQUIRED
    try:
        context.set_ciphers("DEFAULT@SECLEVEL=1")
    except ssl.SSLError:
        pass
    return context


def pairing_envelope(field: int, body: Writer | bytes,
                     status: int = messages.STATUS_OK) -> bytes:
    return (Writer().varint(1, messages.PROTOCOL_VERSION).varint(2, status)
            .message(field, body).delimited())


def recv_one(sock, reader: DelimitedReader) -> dict:
    while True:
        chunk = sock.recv(4096)
        if not chunk:
            raise ConnectionError("client closed")
        for raw in reader.feed(chunk):
            return decode(raw)


class MockServer(threading.Thread):
    """Listens on a random localhost port and serves ``connections`` clients."""

    def __init__(self, context: ssl.SSLContext, connections: int = 1) -> None:
        super().__init__(daemon=True)
        self.context = context
        self.connections = connections
        self.served = 0
        self.error: Exception | None = None
        self.listener = socket.socket()
        self.listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.listener.bind((LOCALHOST, 0))
        self.listener.listen(1)
        self.port = self.listener.getsockname()[1]

    def run(self) -> None:
        try:
            for _ in range(self.connections):
                raw, _addr = self.listener.accept()
                conn = None
                try:
                    conn = self.context.wrap_socket(raw, server_side=True)
                    self.served += 1
                    self.serve(conn)
                except Exception as exc:              # noqa: BLE001 - kept for the test
                    self.error = exc
                finally:
                    for sock in (conn, raw):
                        if sock is not None:
                            try:
                                sock.close()
                            except OSError:
                                pass
        finally:
            self.listener.close()

    def serve(self, conn: ssl.SSLSocket) -> None:
        raise NotImplementedError


class MockPairingServer(MockServer):
    """The TV half of the pairing handshake."""

    def __init__(self, context: ssl.SSLContext, server_cert: Path) -> None:
        super().__init__(context)
        self.server_cert = server_cert
        self.code_ready = threading.Event()
        self.code = ""
        self.expected_secret = b""
        self.secret_ok: bool | None = None
        self.client_name = ""

    def serve(self, conn: ssl.SSLSocket) -> None:
        reader = DelimitedReader()
        # 1. PairingRequest -> PairingRequestAck
        request = get_message(recv_one(conn, reader), messages.PM_PAIRING_REQUEST) or {}
        self.client_name = get_string(request, 2)
        conn.sendall(pairing_envelope(messages.PM_PAIRING_REQUEST_ACK,
                                      Writer().string(1, "Mock TV")))
        # 2. PairingOption -> PairingOption
        assert messages.PM_PAIRING_OPTION in recv_one(conn, reader), "expected PairingOption"
        conn.sendall(pairing_envelope(
            messages.PM_PAIRING_OPTION,
            Writer().message(1, Writer().varint(1, 3).varint(2, 6)).varint(3, 1)))
        # 3. PairingConfiguration -> PairingConfigurationAck, then show a code
        assert messages.PM_PAIRING_CONFIGURATION in recv_one(conn, reader), \
            "expected PairingConfiguration"
        conn.sendall(pairing_envelope(messages.PM_PAIRING_CONFIGURATION_ACK, Writer()))

        client_n, client_e = certs.rsa_public_parts(certs.peer_public_key(conn))
        server_n, server_e = certs.rsa_public_parts(certs.own_public_key(self.server_cert))
        nonce = b"\x9c\x4e"                             # deterministic for the test
        digest = hashlib.sha256(client_n + client_e + server_n + server_e + nonce).digest()
        self.expected_secret = digest
        self.code = bytes([digest[0]]).hex() + nonce.hex()
        self.code_ready.set()

        # 4. PairingSecret -> PairingSecretAck
        secret = get_message(recv_one(conn, reader), messages.PM_PAIRING_SECRET) or {}
        got = get_bytes(secret, 1)
        self.secret_ok = got == self.expected_secret
        if self.secret_ok:
            conn.sendall(pairing_envelope(messages.PM_PAIRING_SECRET_ACK, Writer().bytes(1, got)))
        else:
            conn.sendall(pairing_envelope(messages.PM_PAIRING_SECRET_ACK, Writer(),
                                          status=messages.STATUS_BAD_SECRET))


class MockRemoteServer(MockServer):
    """The TV half of the remote-control session."""

    def __init__(self, context: ssl.SSLContext, connections: int = 1,
                 drop_first: bool = False) -> None:
        super().__init__(context, connections)
        self.drop_first = drop_first
        self.configure_code = None
        self.set_active = None
        self.ping_reply = None
        self.keys: list[tuple[int, int]] = []
        self.app_links: list[str] = []
        self.texts: list[dict] = []
        self.handshake_done = threading.Event()

    def serve(self, conn: ssl.SSLSocket) -> None:
        reader = DelimitedReader()
        conn.sendall(bytes.fromhex(FIXTURES["configure"]))
        configure = get_message(recv_one(conn, reader), messages.RM_CONFIGURE)
        if configure is not None:
            self.configure_code = get_varint(configure, 1)

        conn.sendall(bytes.fromhex(FIXTURES["set_active"]))
        active = get_message(recv_one(conn, reader), messages.RM_SET_ACTIVE)
        if active is not None:
            self.set_active = get_varint(active, 1)

        conn.sendall(bytes.fromhex(FIXTURES["ping"]))
        pong = get_message(recv_one(conn, reader), messages.RM_PING_RESPONSE)
        if pong is not None:
            self.ping_reply = get_varint(pong, 1)

        for name in ("start_on", "volume", "current_app", "ime_batch_edit"):
            conn.sendall(bytes.fromhex(FIXTURES[name]))
        self.handshake_done.set()

        if self.drop_first and self.served == 1:
            return                                      # the TV goes away

        conn.settimeout(10.0)
        while True:
            msg = recv_one(conn, reader)
            key = get_message(msg, messages.RM_KEY_INJECT)
            if key is not None:
                self.keys.append((get_varint(key, 1), get_varint(key, 2)))
            link = get_message(msg, messages.RM_APP_LINK_LAUNCH_REQUEST)
            if link is not None:
                self.app_links.append(get_string(link, 1))
            batch = get_message(msg, messages.RM_IME_BATCH_EDIT)
            if batch is not None:
                edit = get_message(batch, 3) or {}
                field = get_message(edit, 2) or {}
                self.texts.append({
                    "ime_counter": get_varint(batch, 1),
                    "field_counter": get_varint(batch, 2),
                    "insert": get_varint(edit, 1),
                    "start": get_varint(field, 1),
                    "end": get_varint(field, 2),
                    "value": get_string(field, 3),
                })


class MockRejectingServer(MockServer):
    """A TV that has never seen our certificate: the TLS handshake is refused."""

    def serve(self, conn: ssl.SSLSocket) -> None:
        conn.close()


# ------------------------------------------------------------------ tests ---

class MockTVTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._tmp = tempfile.TemporaryDirectory(prefix="gtvremote-test-",
                                               ignore_cleanup_errors=True)
        cls.workdir = Path(cls._tmp.name)
        cls.client_cert, cls.client_key = certs.ensure_client_cert(cls.workdir)
        cls.server_cert, cls.server_key = make_server_cert(cls.workdir)

    @classmethod
    def tearDownClass(cls) -> None:
        cls._tmp.cleanup()

    def tv_context(self, trusted_cert: Path | None = None) -> ssl.SSLContext:
        return server_context(self.server_cert, self.server_key,
                              trusted_cert or self.client_cert)

    def pairing_server(self) -> MockPairingServer:
        server = MockPairingServer(self.tv_context(), self.server_cert)
        server.start()
        return server

    def pairing_session(self, server: MockServer, **kwargs) -> PairingSession:
        session = PairingSession(LOCALHOST, port=server.port, cert_dir=self.workdir, **kwargs)
        self.addCleanup(session.close)
        return session

    def remote_client(self, server: MockServer, **callbacks) -> RemoteClient:
        client = RemoteClient(LOCALHOST, port=server.port, cert_dir=self.workdir, **callbacks)
        self.addCleanup(client.stop)
        return client

    # -- pairing ----------------------------------------------------------

    def test_pairing_agrees_on_the_secret(self):
        server = self.pairing_server()
        session = self.pairing_session(server, client_name="Test PC")
        session.begin()
        self.assertTrue(server.code_ready.wait(5), "TV produced a pairing code")
        self.assertEqual(server.client_name, "Test PC")
        self.assertEqual(session.server_name, "Mock TV")

        spaced = " ".join(server.code.upper()[i:i + 2] for i in range(0, 6, 2))
        name = session.send_code(spaced)                # "AB CD EF" is accepted
        server.join(5)

        self.assertIs(server.secret_ok, True, "secret hash agreed with the TV")
        self.assertEqual(name, "Mock TV")
        self.assertIsNone(server.error)

    def test_wrong_code_is_rejected_before_it_is_sent(self):
        server = self.pairing_server()
        session = self.pairing_session(server)
        session.begin()
        self.assertTrue(server.code_ready.wait(5))
        first = "1" if server.code[0] != "1" else "2"
        with self.assertRaises(BadCodeError):
            session.send_code(first + server.code[1:])
        self.assertIsNone(server.secret_ok, "nothing reached the TV")

    def test_malformed_codes_are_rejected(self):
        server = self.pairing_server()
        session = self.pairing_session(server)
        session.begin()
        self.assertTrue(server.code_ready.wait(5))
        for bad in ("12345", "1234567", "ghijkl", ""):
            with self.subTest(code=bad), self.assertRaises(BadCodeError):
                session.send_code(bad)

    def test_bad_secret_status_from_the_tv_is_reported(self):
        server = self.pairing_server()
        session = self.pairing_session(server)
        session.begin()
        self.assertTrue(server.code_ready.wait(5))
        # Skip the local check so the TV gets to reject the secret itself.
        with mock.patch.object(session, "_secret", return_value=bytes(32)):
            with self.assertRaises(BadCodeError):
                session.send_code(server.code)
        server.join(5)
        self.assertIs(server.secret_ok, False)

    # -- remote -----------------------------------------------------------

    def test_remote_session(self):
        server = MockRemoteServer(self.tv_context())
        server.start()
        states: list[str] = []
        volumes: list[tuple] = []
        powers: list[bool] = []
        apps: list[str] = []
        client = self.remote_client(
            server,
            on_state=lambda status, _detail: states.append(status),
            on_volume=lambda level, maximum, muted: volumes.append((level, maximum, muted)),
            on_power=powers.append,
            on_app=apps.append)

        client.start()
        self.assertTrue(client.wait_until_connected(10), states)
        self.assertTrue(server.handshake_done.wait(5))
        self.assertEqual(server.configure_code, messages.CONFIGURE_CODE)
        self.assertEqual(server.set_active, messages.CONFIGURE_CODE)
        self.assertEqual(server.ping_reply, 42)

        self.assertTrue(wait_for(lambda: volumes and powers and apps))
        self.assertEqual(volumes[-1], (37, 100, False))
        self.assertIs(powers[-1], True)
        self.assertEqual(apps[-1], "com.google.android.youtube.tv")
        self.assertEqual((client.volume_level, client.volume_max, client.volume_muted),
                         (37, 100, False))
        self.assertIs(client.powered, True)
        self.assertEqual(client.current_app, "com.google.android.youtube.tv")

        # The TV announced a focused text field; typing must echo its counters.
        self.assertTrue(wait_for(lambda: (client._ime_counter, client._field_counter) == (3, 7)))

        client.send_key(keycodes.KEYCODE_DPAD_UP)
        client.send_key(keycodes.KEYCODE_DPAD_CENTER)
        client.launch_app("https://www.youtube.com")
        client.send_text("Hi 5!")
        with self.assertRaises(ValueError):
            client.send_text("")

        expected = [(keycodes.KEYCODE_DPAD_UP, keycodes.SHORT),
                    (keycodes.KEYCODE_DPAD_CENTER, keycodes.SHORT)]
        self.assertTrue(wait_for(lambda: server.texts))
        self.assertEqual(server.keys, expected)
        self.assertEqual(server.app_links, ["https://www.youtube.com"])
        self.assertEqual(server.texts, [{"ime_counter": 3, "field_counter": 7, "insert": 1,
                                         "start": 4, "end": 4, "value": "Hi 5!"}])

        client.stop()
        self.assertFalse(client.is_connected)
        self.assertEqual(states, ["connecting", "connected", "disconnected"])

    def test_reconnects_when_the_tv_drops_the_link(self):
        server = MockRemoteServer(self.tv_context(), connections=2, drop_first=True)
        server.start()
        states: list[str] = []
        client = self.remote_client(server, on_state=lambda status, _d: states.append(status))

        client.start()
        self.assertTrue(wait_for(lambda: states.count("connected") == 2, timeout=15), states)
        self.assertEqual(server.served, 2)
        self.assertIn("reconnecting", states)
        self.assertTrue(client.is_connected)

    def test_unknown_certificate_reports_unpaired_and_stops(self):
        # This TV trusts only its own certificate, i.e. it has never paired with us.
        server = MockRejectingServer(self.tv_context(trusted_cert=self.server_cert))
        server.start()
        states: list[str] = []
        unpaired = threading.Event()

        def on_state(status, _detail):
            states.append(status)
            if status == "unpaired":
                unpaired.set()

        client = self.remote_client(server, on_state=on_state)
        client.start()
        self.assertTrue(unpaired.wait(10), states)
        self.assertEqual(states, ["connecting", "unpaired"])
        self.assertFalse(client.is_connected)
        time.sleep(0.3)
        self.assertNotIn("reconnecting", states, "must not retry until paired")

    def test_sending_while_disconnected_raises(self):
        client = RemoteClient(LOCALHOST, port=1, cert_dir=self.workdir)
        with self.assertRaises(ConnectionError):
            client.send_key(keycodes.KEYCODE_HOME)


if __name__ == "__main__":
    unittest.main()
