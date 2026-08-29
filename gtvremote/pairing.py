"""Pairing against a Google TV / Android TV device (TLS port 6467).

Flow: connect with our client certificate, exchange request/option/
configuration, then prove we can see the 6-digit code the TV displays by
hashing it together with both certificates' public keys.
"""

from __future__ import annotations

import hashlib
import ssl
from collections import deque
from pathlib import Path

from . import certs, messages
from .protobuf_lite import DelimitedReader

PAIRING_PORT = 6467
DEFAULT_CLIENT_NAME = "Windows Remote"
DEFAULT_SERVICE_NAME = "com.google.tv.remote/windows"


class PairingError(Exception):
    """Pairing failed; the message is safe to show to the user."""


class BadCodeError(PairingError):
    """The entered code did not match the one shown on the TV."""


class PairingSession:
    """One pairing attempt. Use as a context manager or call close()."""

    def __init__(self, host: str, client_name: str = DEFAULT_CLIENT_NAME,
                 service_name: str = DEFAULT_SERVICE_NAME,
                 port: int = PAIRING_PORT, cert_dir: Path | None = None,
                 timeout: float = certs.CONNECT_TIMEOUT) -> None:
        self.host = host
        self.port = port
        self.client_name = client_name
        self.service_name = service_name
        self.timeout = timeout
        self.server_name = ""
        self._cert_path, self._key_path = certs.ensure_client_cert(cert_dir)
        self._sock: ssl.SSLSocket | None = None
        self._reader = DelimitedReader()
        self._pending: deque[bytes] = deque()

    # -- plumbing ---------------------------------------------------------

    def _send(self, payload: bytes) -> None:
        if self._sock is None:
            raise PairingError("Not connected to the TV.")
        self._sock.sendall(payload)

    def _recv(self) -> dict:
        """Read the next PairingMessage, raising on a non-OK status."""
        while not self._pending:
            if self._sock is None:
                raise PairingError("Not connected to the TV.")
            chunk = self._sock.recv(4096)
            if not chunk:
                raise PairingError("The TV closed the connection.")
            self._pending.extend(self._reader.feed(chunk))

        msg = messages.parse_pairing(self._pending.popleft())
        status = msg["status"]
        if status == messages.STATUS_BAD_SECRET:
            raise BadCodeError("Wrong pairing code.")
        if status != messages.STATUS_OK:
            text = messages.STATUS_TEXT.get(status, f"status {status}")
            raise PairingError(f"Pairing failed: {text}.")
        return msg

    # -- steps ------------------------------------------------------------

    def begin(self) -> None:
        """Connect and negotiate until the TV shows its pairing code."""
        self._sock = certs.connect_tls(self.host, self.port,
                                       self._cert_path, self._key_path,
                                       timeout=self.timeout)
        self._send(messages.pairing_request(self.service_name, self.client_name))
        msg = self._recv()
        if not msg["request_ack"]:
            raise PairingError("The TV did not acknowledge the pairing request.")
        self.server_name = msg["server_name"]

        self._send(messages.pairing_option())
        if not self._recv()["option"]:
            raise PairingError("The TV did not return pairing options.")

        self._send(messages.pairing_configuration())
        if not self._recv()["configuration_ack"]:
            raise PairingError("The TV did not accept the pairing configuration.")
        # The code is now on screen; the caller collects it and calls send_code().

    def _secret(self, code: str) -> bytes:
        code = code.strip().replace(" ", "")
        if len(code) != messages.CODE_LENGTH:
            raise BadCodeError(f"The code must be {messages.CODE_LENGTH} characters.")
        try:
            code_bytes = bytes.fromhex(code)
        except ValueError:
            raise BadCodeError("The code must be hexadecimal (0-9, A-F).") from None

        client_n, client_e = certs.rsa_public_parts(certs.own_public_key(self._cert_path))
        server_n, server_e = certs.rsa_public_parts(certs.peer_public_key(self._sock))

        digest = hashlib.sha256()
        digest.update(client_n)
        digest.update(client_e)
        digest.update(server_n)
        digest.update(server_e)
        digest.update(code_bytes[1:])   # first byte is the checksum, not input
        secret = digest.digest()

        if secret[0] != code_bytes[0]:
            raise BadCodeError("Wrong pairing code.")
        return secret

    def send_code(self, code: str) -> str:
        """Submit the on-screen code. Returns the TV's name on success."""
        self._send(messages.pairing_secret(self._secret(code)))
        if not self._recv()["secret_ack"]:
            raise PairingError("The TV did not confirm pairing.")
        return self.server_name

    def close(self) -> None:
        sock, self._sock = self._sock, None
        if sock is not None:
            sock.close()

    def __enter__(self) -> PairingSession:
        return self

    def __exit__(self, *exc) -> None:
        self.close()
