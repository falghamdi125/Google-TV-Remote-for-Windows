"""Message builders/parsers for the Android TV Remote v2 protocol.

Field numbers come from proto/pairingmessage.proto and
proto/remotemessage.proto, kept in the repository for reference.
"""

from __future__ import annotations

from .protobuf_lite import Writer, decode, get_message, get_string, get_varint, has

# ---------------------------------------------------------------- pairing ---

PROTOCOL_VERSION = 2

STATUS_UNKNOWN = 0
STATUS_OK = 200
STATUS_ERROR = 400
STATUS_BAD_CONFIGURATION = 401
STATUS_BAD_SECRET = 402

STATUS_TEXT = {
    STATUS_UNKNOWN: "unknown error",
    STATUS_ERROR: "the TV rejected the request",
    STATUS_BAD_CONFIGURATION: "the TV rejected the pairing configuration",
    STATUS_BAD_SECRET: "wrong pairing code",
}

ROLE_TYPE_INPUT = 1
ENCODING_TYPE_HEXADECIMAL = 3
CODE_LENGTH = 6

# PairingMessage field numbers
PM_PROTOCOL_VERSION = 1
PM_STATUS = 2
PM_PAIRING_REQUEST = 10
PM_PAIRING_REQUEST_ACK = 11
PM_PAIRING_OPTION = 20
PM_PAIRING_CONFIGURATION = 30
PM_PAIRING_CONFIGURATION_ACK = 31
PM_PAIRING_SECRET = 40
PM_PAIRING_SECRET_ACK = 41


def _pairing_envelope(field: int, body: Writer | bytes) -> bytes:
    return (
        Writer()
        .varint(PM_PROTOCOL_VERSION, PROTOCOL_VERSION)
        .varint(PM_STATUS, STATUS_OK)
        .message(field, body)
        .delimited()
    )


def pairing_request(service_name: str, client_name: str) -> bytes:
    body = Writer().string(1, service_name).string(2, client_name)
    return _pairing_envelope(PM_PAIRING_REQUEST, body)


def _encoding() -> Writer:
    return Writer().varint(1, ENCODING_TYPE_HEXADECIMAL).varint(2, CODE_LENGTH)


def pairing_option() -> bytes:
    # input_encodings = 1 (repeated), preferred_role = 3
    body = Writer().message(1, _encoding()).varint(3, ROLE_TYPE_INPUT)
    return _pairing_envelope(PM_PAIRING_OPTION, body)


def pairing_configuration() -> bytes:
    # encoding = 1, client_role = 2
    body = Writer().message(1, _encoding()).varint(2, ROLE_TYPE_INPUT)
    return _pairing_envelope(PM_PAIRING_CONFIGURATION, body)


def pairing_secret(secret: bytes) -> bytes:
    body = Writer().bytes(1, secret)
    return _pairing_envelope(PM_PAIRING_SECRET, body)


def parse_pairing(raw: bytes) -> dict:
    """Decode a PairingMessage body into a friendly dict."""
    msg = decode(raw)
    return {
        "status": get_varint(msg, PM_STATUS),
        "request_ack": has(msg, PM_PAIRING_REQUEST_ACK),
        "option": has(msg, PM_PAIRING_OPTION),
        "configuration_ack": has(msg, PM_PAIRING_CONFIGURATION_ACK),
        "secret_ack": has(msg, PM_PAIRING_SECRET_ACK),
        "server_name": get_string(get_message(msg, PM_PAIRING_REQUEST_ACK) or {}, 1),
    }


# ----------------------------------------------------------------- remote ---

# RemoteMessage field numbers
RM_CONFIGURE = 1
RM_SET_ACTIVE = 2
RM_ERROR = 3
RM_PING_REQUEST = 8
RM_PING_RESPONSE = 9
RM_KEY_INJECT = 10
RM_IME_KEY_INJECT = 20
RM_IME_BATCH_EDIT = 21
RM_IME_SHOW_REQUEST = 22
RM_VOICE_BEGIN = 30
RM_VOICE_PAYLOAD = 31
RM_VOICE_END = 32
RM_START = 40
RM_SET_VOLUME_LEVEL = 50
RM_ADJUST_VOLUME_LEVEL = 51
RM_SET_PREFERRED_AUDIO_DEVICE = 60
RM_RESET_PREFERRED_AUDIO_DEVICE = 61
RM_APP_LINK_LAUNCH_REQUEST = 90

# The reference implementations all send 622 here; the TV echoes it back.
CONFIGURE_CODE = 622

# Messages that carry no payload we use; parse_remote reports just the kind.
_SIMPLE_KINDS = {
    RM_CONFIGURE: "configure",
    RM_SET_ACTIVE: "set_active",
    RM_VOICE_BEGIN: "voice_begin",
    RM_VOICE_END: "voice_end",
}


def remote_configure(model: str, vendor: str, package_name: str, app_version: str) -> bytes:
    device_info = (
        Writer()
        .string(1, model)
        .string(2, vendor)
        .varint(3, 1)
        .string(4, "1")
        .string(5, package_name)
        .string(6, app_version)
    )
    body = Writer().varint(1, CONFIGURE_CODE).message(2, device_info)
    return Writer().message(RM_CONFIGURE, body).delimited()


def remote_set_active(active: int = CONFIGURE_CODE) -> bytes:
    body = Writer().varint(1, active)
    return Writer().message(RM_SET_ACTIVE, body).delimited()


def remote_ping_response(val1: int) -> bytes:
    body = Writer().varint(1, val1)
    return Writer().message(RM_PING_RESPONSE, body).delimited()


def remote_key_inject(key_code: int, direction: int) -> bytes:
    body = Writer().varint(1, key_code).varint(2, direction)
    return Writer().message(RM_KEY_INJECT, body).delimited()


def remote_app_link_launch(app_link: str) -> bytes:
    body = Writer().string(1, app_link)
    return Writer().message(RM_APP_LINK_LAUNCH_REQUEST, body).delimited()


def remote_ime_batch_edit(ime_counter: int, field_counter: int, text: str) -> bytes:
    """Replace the focused text field's contents with ``text``.

    Mirrors the official client: the counters are the ones the TV last sent
    in its own RemoteImeBatchEdit, and the caret goes to the end of the text.
    """
    caret = max(len(text) - 1, 0)
    ime_object = Writer().varint(1, caret).varint(2, caret).string(3, text)
    edit = Writer().varint(1, 1).message(2, ime_object)           # insert = 1
    body = Writer().varint(1, ime_counter).varint(2, field_counter).message(3, edit)
    return Writer().message(RM_IME_BATCH_EDIT, body).delimited()


def parse_remote(raw: bytes) -> dict:
    """Decode a RemoteMessage body into a dict with a "kind" key.

    Unrecognised messages come back as ``{"kind": None}``.
    """
    msg = decode(raw)

    if has(msg, RM_PING_REQUEST):
        ping = get_message(msg, RM_PING_REQUEST) or {}
        return {"kind": "ping", "val1": get_varint(ping, 1)}
    if has(msg, RM_START):
        start = get_message(msg, RM_START) or {}
        return {"kind": "start", "started": bool(get_varint(start, 1))}
    if has(msg, RM_SET_VOLUME_LEVEL):
        vol = get_message(msg, RM_SET_VOLUME_LEVEL) or {}
        return {
            "kind": "volume",
            "player_model": get_string(vol, 3),
            "volume_max": get_varint(vol, 6),
            "volume_level": get_varint(vol, 7),
            "volume_muted": bool(get_varint(vol, 8)),
        }
    if has(msg, RM_IME_KEY_INJECT):
        ime = get_message(msg, RM_IME_KEY_INJECT) or {}
        app_info = get_message(ime, 1) or {}
        return {"kind": "current_app", "app_package": get_string(app_info, 12)}
    if has(msg, RM_IME_BATCH_EDIT):
        edit = get_message(msg, RM_IME_BATCH_EDIT) or {}
        return {"kind": "ime_batch_edit",
                "ime_counter": get_varint(edit, 1),
                "field_counter": get_varint(edit, 2)}
    if has(msg, RM_IME_SHOW_REQUEST):
        show = get_message(msg, RM_IME_SHOW_REQUEST) or {}
        field = get_message(show, 2) or {}
        return {"kind": "ime_show_request",
                "counter_field": get_varint(field, 1),
                "value": get_string(field, 2),
                "label": get_string(field, 6)}
    if has(msg, RM_ERROR):
        err = get_message(msg, RM_ERROR) or {}
        return {"kind": "error", "value": bool(get_varint(err, 1))}
    for field, kind in _SIMPLE_KINDS.items():
        if has(msg, field):
            return {"kind": kind}
    return {"kind": None}
