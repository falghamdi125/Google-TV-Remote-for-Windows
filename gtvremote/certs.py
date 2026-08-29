"""Client certificate handling and TLS connections.

The TV identifies a paired remote by its TLS client certificate, so the
certificate is generated once and reused for every connection. Losing it
means pairing again.
"""

from __future__ import annotations

import datetime
import os
import socket
import ssl
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

from .config import config_dir

CERT_FILE = "client.crt"
KEY_FILE = "client.key"
CERT_LIFETIME = datetime.timedelta(days=3650)
CONNECT_TIMEOUT = 10.0


def _generate(cert_path: Path, key_path: Path) -> None:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([
        x509.NameAttribute(NameOID.COMMON_NAME, "atvremote"),
        x509.NameAttribute(NameOID.ORGANIZATION_NAME, "Google TV Remote for Windows"),
    ])
    now = datetime.datetime.now(datetime.timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(days=1))
        .not_valid_after(now + CERT_LIFETIME)
        .sign(key, hashes.SHA256())
    )
    key_path.write_bytes(key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.TraditionalOpenSSL,
        encryption_algorithm=serialization.NoEncryption(),
    ))
    cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    try:
        os.chmod(key_path, 0o600)
    except OSError:
        pass


def ensure_client_cert(directory: Path | None = None) -> tuple[Path, Path]:
    """Return (cert_path, key_path), creating the pair on first run."""
    directory = directory or config_dir()
    cert_path, key_path = directory / CERT_FILE, directory / KEY_FILE
    if not cert_path.exists() or not key_path.exists():
        _generate(cert_path, key_path)
    return cert_path, key_path


def reset_client_cert(directory: Path | None = None) -> None:
    """Forget this remote's identity, forcing a fresh pairing."""
    directory = directory or config_dir()
    for filename in (CERT_FILE, KEY_FILE):
        (directory / filename).unlink(missing_ok=True)


def rsa_public_parts(public_key) -> tuple[bytes, bytes]:
    """Return (modulus, exponent) as big-endian bytes with no leading zeros.

    This is how the reference implementations feed the keys into the pairing
    hash, so the digest agrees with what the TV computes.
    """
    numbers = public_key.public_numbers()

    def minimal_bytes(value: int) -> bytes:
        return value.to_bytes((value.bit_length() + 7) // 8, "big")

    return minimal_bytes(numbers.n), minimal_bytes(numbers.e)


def build_ssl_context(cert_path: Path, key_path: Path) -> ssl.SSLContext:
    """A permissive client context: the TV always presents a self-signed cert."""
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE
    context.load_cert_chain(certfile=str(cert_path), keyfile=str(key_path))
    try:
        # Some TVs still negotiate older suites than Python's default policy allows.
        context.set_ciphers("DEFAULT@SECLEVEL=1")
    except ssl.SSLError:
        pass
    return context


def connect_tls(host: str, port: int, cert_path: Path, key_path: Path,
                timeout: float = CONNECT_TIMEOUT) -> ssl.SSLSocket:
    """Open a TLS connection presenting our client certificate."""
    context = build_ssl_context(cert_path, key_path)
    raw = socket.create_connection((host, port), timeout=timeout)
    try:
        return context.wrap_socket(raw, server_hostname=None)
    except Exception:
        raw.close()
        raise


def peer_public_key(sock: ssl.SSLSocket):
    der = sock.getpeercert(binary_form=True)
    if not der:
        raise ConnectionError("The TV did not present a certificate.")
    return x509.load_der_x509_certificate(der).public_key()


def own_public_key(cert_path: Path):
    return x509.load_pem_x509_certificate(cert_path.read_bytes()).public_key()
