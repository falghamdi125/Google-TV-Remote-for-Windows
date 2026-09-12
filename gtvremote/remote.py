"""Persistent remote-control connection (TLS port 6466).

Runs its own reader thread, answers the TV's 5-second pings, tracks volume /
power / foreground app, and reconnects on its own when the link drops.
"""

from __future__ import annotations

import logging
import socket
import ssl
import threading
import time
from pathlib import Path
from typing import Callable

from . import __version__, certs, keycodes, messages
from .protobuf_lite import DelimitedReader

log = logging.getLogger(__name__)

REMOTE_PORT = 6466

# The TV pings every 5s; give it some slack before declaring the link dead.
SOCKET_TIMEOUT = 15.0
# A session that was up and then dropped is retried almost at once: TVs and
# Wi-Fi hops reset idle sessions now and then and accept a new one straight
# away. Only failures to *get* connected back off.
QUICK_RETRY = 0.5
INITIAL_BACKOFF = 1.0
MAX_BACKOFF = 15.0
# A TV that has accepted this remote before is not declared unpaired on a
# single refusal - its remote service restarting looks exactly the same -
# only after this many in a row.
UNPAIRED_AFTER_REFUSALS = 3
# The TV echoes every text edit it accepts within a few ms; silence means
# it was dropped (no field focused, or stale counters).
EDIT_ECHO_TIMEOUT = 1.5

# How this remote introduces itself to the TV.
DEVICE_MODEL = "Windows PC"
DEVICE_VENDOR = "Microsoft"
PACKAGE_NAME = "gtv-remote-windows"

#: Values passed to ``on_state``.
STATES = ("connecting", "connected", "reconnecting", "error", "unpaired", "disconnected")

UNPAIRED_MESSAGE = ("The TV rejected the connection - it probably does not "
                    "recognise this remote yet. Pair with it first.")


class NotPairedError(Exception):
    """The TV dropped us before saying anything - it rejected our certificate."""


class RemoteClient:
    """Thread-safe client for sending key events to a Google TV.

    Callbacks are invoked on the client's own thread; a GUI must hand them
    over to its main thread.
    """

    def __init__(self, host: str, port: int = REMOTE_PORT,
                 model: str = DEVICE_MODEL, vendor: str = DEVICE_VENDOR,
                 package_name: str = PACKAGE_NAME, app_version: str = __version__,
                 cert_dir: Path | None = None,
                 on_state: Callable[[str, str], None] | None = None,
                 on_volume: Callable[[int, int, bool], None] | None = None,
                 on_power: Callable[[bool], None] | None = None,
                 on_app: Callable[[str], None] | None = None,
                 on_field: Callable[[str | None], None] | None = None) -> None:
        self.host = host
        self.port = port
        self.model = model
        self.vendor = vendor
        self.package_name = package_name
        self.app_version = app_version
        self._cert_path, self._key_path = certs.ensure_client_cert(cert_dir)

        self.on_state = on_state or (lambda status, detail: None)
        self.on_volume = on_volume or (lambda level, maximum, muted: None)
        self.on_power = on_power or (lambda on: None)
        self.on_app = on_app or (lambda package: None)
        # Called with the label of the text field the TV has focused, or
        # None when it goes away.
        self.on_field = on_field or (lambda label: None)

        self._sock: ssl.SSLSocket | None = None
        self._send_lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._connected = threading.Event()
        self._connected_at = 0.0
        self._ever_connected = False            # this TV has accepted our certificate
        self._refusals = 0                      # consecutive drops before the TV said anything

        self.tv_model = ""                      # from RemoteConfigure, e.g. "Smart TV Pro"
        self.tv_vendor = ""                     # e.g. "TCL"
        self.volume_level = 0
        self.volume_max = 0
        self.volume_muted = False
        self.powered: bool | None = None
        self.current_app = ""
        # The focused text field, as reported by the TV (see send_text): its
        # label, current contents, and the counters every edit must echo.
        self.text_field: str | None = None
        self.text_value: str = ""
        self._ime_counter = 0
        self._field_counter = 0
        self._field_echo = threading.Event()

    # -- lifecycle --------------------------------------------------------

    @property
    def is_connected(self) -> bool:
        return self._connected.is_set()

    def start(self) -> None:
        """Begin connecting in the background (idempotent)."""
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="gtv-remote", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._close_socket()
        thread = self._thread
        if thread and thread.is_alive() and thread is not threading.current_thread():
            thread.join(timeout=2.0)
        self._thread = None

    def wait_until_connected(self, timeout: float = 12.0) -> bool:
        return self._connected.wait(timeout)

    def _close_socket(self) -> None:
        with self._send_lock:
            sock, self._sock = self._sock, None
        self._connected.clear()
        if sock is not None:
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            try:
                sock.close()
            except OSError:
                pass

    # -- background loop --------------------------------------------------

    def _run(self) -> None:
        backoff = INITIAL_BACKOFF
        while not self._stop.is_set():
            was_up = False
            try:
                self.on_state("connecting", f"Connecting to {self.host}...")
                log.info("Connecting to %s", self.host)
                self._connect_once()
                break                                   # only stop() ends a session cleanly
            except NotPairedError as exc:
                if self._stop.is_set():
                    break
                self._refusals += 1
                if not self._ever_connected or self._refusals >= UNPAIRED_AFTER_REFUSALS:
                    # Retrying cannot help until the user pairs, so stop here.
                    log.warning("%s refused the connection %d time(s): treating as unpaired",
                                self.host, self._refusals)
                    self.on_state("unpaired", str(exc))
                    return
                reason = "The TV refused the connection."
                log.warning("%s refused the connection (%d of %d before giving up)",
                            self.host, self._refusals, UNPAIRED_AFTER_REFUSALS)
                self.on_state("error", reason)
            except Exception as exc:                    # noqa: BLE001 - surfaced to UI
                if self._stop.is_set():
                    break
                was_up = self._connected.is_set()
                reason = self._describe(exc, was_up)
                if was_up:
                    log.warning("Lost the connection to %s after %.0fs: %r",
                                self.host, time.monotonic() - self._connected_at, exc)
                else:
                    log.info("Could not connect to %s: %r", self.host, exc)
                self.on_state("error", reason)
            finally:
                self._close_socket()

            if self._stop.is_set():
                break
            if was_up:
                delay, backoff = QUICK_RETRY, INITIAL_BACKOFF
            else:
                delay, backoff = backoff, min(backoff * 2, MAX_BACKOFF)
            self.on_state("reconnecting", f"{reason} Reconnecting in {delay:g}s...")
            if self._stop.wait(delay):
                break

        log.info("Disconnected from %s", self.host)
        self.on_state("disconnected", "Disconnected")

    def _describe(self, exc: Exception, was_up: bool) -> str:
        if isinstance(exc, ssl.SSLError):
            return "TLS error: " + (getattr(exc, "reason", None) or str(exc))
        if not was_up:
            if isinstance(exc, OSError):
                return f"Cannot reach {self.host}. Is the TV on and on this network?"
            return str(exc) or type(exc).__name__
        if isinstance(exc, TimeoutError):
            return "The TV stopped responding."
        if isinstance(exc, ConnectionResetError):
            return "The TV reset the connection."
        if isinstance(exc, ConnectionAbortedError):
            return "The network dropped the connection."
        if isinstance(exc, ConnectionError):
            return str(exc) or "The connection was lost."
        if isinstance(exc, OSError):
            return f"Lost the connection to {self.host}."
        return str(exc) or type(exc).__name__

    def _connect_once(self) -> None:
        self._reset_session()
        # A TV that does not know our certificate refuses the TLS handshake.
        # With TLS 1.3 the refusal only surfaces on the first read, and it
        # may arrive as a TLS alert or as a bare reset/EOF, so any drop before
        # the TV has sent its first message counts as "not paired".
        try:
            sock = certs.connect_tls(self.host, self.port, self._cert_path, self._key_path)
        except ssl.SSLError as exc:
            raise NotPairedError(UNPAIRED_MESSAGE) from exc
        sock.settimeout(SOCKET_TIMEOUT)
        with self._send_lock:
            self._sock = sock
        reader = DelimitedReader()
        heard_from_tv = False

        while not self._stop.is_set():
            try:
                chunk = sock.recv(8192)
            except TimeoutError:
                raise TimeoutError("No data from the TV.") from None
            except OSError as exc:
                if not heard_from_tv and not self._stop.is_set():
                    raise NotPairedError(UNPAIRED_MESSAGE) from exc
                raise
            if not chunk:
                if not heard_from_tv and not self._stop.is_set():
                    raise NotPairedError(UNPAIRED_MESSAGE)
                raise ConnectionError("The TV closed the connection.")
            heard_from_tv = True
            for raw in reader.feed(chunk):
                try:
                    msg = messages.parse_remote(raw)
                except (ValueError, IndexError) as exc:
                    # One message we cannot read is no reason to drop the link.
                    log.warning("Ignoring an undecodable message from the TV (%s): %s",
                                exc, raw.hex())
                    continue
                self._handle(msg)

    def _reset_session(self) -> None:
        """Forget what the previous session learned: the TV re-sends its
        state on connect, and the text-field counters are per session."""
        self.current_app = ""
        had_field = self.text_field is not None
        self.text_field = None
        self.text_value = ""
        self._ime_counter = 0
        self._field_counter = 0
        if had_field:
            self.on_field(None)

    def _mark_connected(self) -> None:
        if not self._connected.is_set():
            self._connected_at = time.monotonic()
            self._ever_connected = True
            self._refusals = 0
            self._connected.set()
            log.info("Connected to %s (%s %s)", self.host, self.tv_vendor, self.tv_model)
            self.on_state("connected", f"Connected to {self.host}")

    def _handle(self, msg: dict) -> None:
        kind = msg.get("kind")
        if kind == "configure":
            self.tv_model = msg.get("model", "")
            self.tv_vendor = msg.get("vendor", "")
            self._send_raw(messages.remote_configure(
                self.model, self.vendor, self.package_name, self.app_version))
        elif kind == "set_active":
            self._send_raw(messages.remote_set_active())
            self._mark_connected()
        elif kind == "ping":
            self._send_raw(messages.remote_ping_response(msg.get("val1", 0)))
            self._mark_connected()
        elif kind == "volume":
            self.volume_level = msg.get("volume_level", 0)
            self.volume_max = msg.get("volume_max", 0)
            self.volume_muted = msg.get("volume_muted", False)
            self.on_volume(self.volume_level, self.volume_max, self.volume_muted)
        elif kind == "start":
            self.powered = msg.get("started", False)
            self.on_power(self.powered)
        elif kind == "current_app":
            package = msg.get("app_package", "")
            if package and package != self.current_app:
                self.current_app = package
                self.on_app(package)
                if "field_counter" not in msg and self.text_field is not None:
                    self.text_field = None          # a different app: the field is gone
                    self.text_value = ""
                    self.on_field(None)
            if "field_counter" in msg:
                self._note_field(msg.get("field_label", ""), msg.get("field_value", ""),
                                 msg["field_counter"])
        elif kind == "ime_show_request":
            self._note_field(msg.get("label", ""), msg.get("value", ""),
                             msg.get("counter_field", 0))
        elif kind == "ime_batch_edit":
            # Zero shows up both when a field loses focus and, sometimes,
            # right after an accepted edit, so it proves nothing; only ever
            # move to a real counter.
            if msg.get("ime_counter"):
                self._ime_counter = msg["ime_counter"]
                self._note_field_counter(msg.get("field_counter", 0))

    def _note_field(self, label: str, value: str, counter: int) -> None:
        appeared = self.text_field is None
        self.text_field = label
        self.text_value = value
        self._note_field_counter(counter)
        self._field_echo.set()
        if appeared:
            self.on_field(label)

    def _note_field_counter(self, counter: int) -> None:
        # The TV reports 0 in its RemoteImeBatchEdit while the field status
        # carries the real, ever-increasing counter; keep the latest real one.
        if counter:
            self._field_counter = counter

    # -- sending ----------------------------------------------------------

    def _send_raw(self, payload: bytes) -> None:
        with self._send_lock:
            sock = self._sock
            if sock is None:
                raise ConnectionError("Not connected.")
            sock.sendall(payload)

    def send_key(self, key_code: int, direction: int = keycodes.SHORT) -> None:
        """Inject a key press. Raises ConnectionError when not connected."""
        self._send_raw(messages.remote_key_inject(key_code, direction))

    def long_press(self, key_code: int, hold: float = 0.8) -> None:
        """Hold a key for ``hold`` seconds (e.g. a long press on Home)."""
        self._send_raw(messages.remote_key_inject(key_code, keycodes.START_LONG))
        time.sleep(hold)
        self._send_raw(messages.remote_key_inject(key_code, keycodes.END_LONG))

    def launch_app(self, app_link: str) -> None:
        """Open a deep link, e.g. https://www.youtube.com or a market:// URI."""
        self._send_raw(messages.remote_app_link_launch(app_link))

    # -- text field -------------------------------------------------------
    # All three go through the TV's input method, as the official app does,
    # rather than through key presses (which the on-screen keyboard
    # swallows), so any Unicode text works. They raise ValueError when the
    # TV has never reported a focused text field (e.g. a search box) or
    # does not confirm the edit.

    def send_text(self, text: str) -> None:
        """Append ``text`` to the text field focused on the TV."""
        if not text:
            raise ValueError("text is empty")
        length = self._focused_field_length()
        self._edit_field(length, length, text)

    def delete_text(self) -> None:
        """Delete the last character of the focused text field."""
        length = self._focused_field_length()
        if length == 0:
            raise ValueError("The text field on the TV is already empty.")
        self._edit_field(length - 1, length, "")

    def clear_text(self) -> None:
        """Empty the focused text field."""
        length = self._focused_field_length()
        if length == 0:
            raise ValueError("The text field on the TV is already empty.")
        self._edit_field(0, length, "")

    def _focused_field_length(self) -> int:
        if self.text_field is None:
            raise ValueError("No text field is focused on the TV.")
        return len(self.text_value)

    def _edit_field(self, start: int, end: int, text: str) -> None:
        self._field_echo.clear()
        self._send_raw(messages.remote_ime_batch_edit(
            self._ime_counter, self._field_counter, text, start, end))
        # The echo carries the field's new contents and counter (see _note_field).
        if not self._field_echo.wait(EDIT_ECHO_TIMEOUT):
            raise ValueError("The TV did not accept the edit. Put the cursor in the text box "
                             "on the TV (close its on-screen keyboard) and try again.")
