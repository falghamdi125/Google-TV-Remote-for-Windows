"""Write tests/cases.json: messages from the Python encoder, plus what the
real protobufjs library must decode them to.

    python tests/make_cases.py && node tests/verify_encoding.js
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from gtvremote import keycodes, messages  # noqa: E402

PAIRING = "pairing.PairingMessage"
REMOTE = "remote.RemoteMessage"
SECRET = bytes(range(32))

CASES = [
    ("pairing_request", PAIRING,
     messages.pairing_request("com.example/service", "MyPC"),
     {"protocolVersion": 2, "status": "STATUS_OK",
      "pairingRequest": {"serviceName": "com.example/service", "clientName": "MyPC"}}),
    ("pairing_option", PAIRING,
     messages.pairing_option(),
     {"protocolVersion": 2, "status": "STATUS_OK",
      "pairingOption": {
          "inputEncodings": [{"type": "ENCODING_TYPE_HEXADECIMAL", "symbolLength": 6}],
          "preferredRole": "ROLE_TYPE_INPUT"}}),
    ("pairing_configuration", PAIRING,
     messages.pairing_configuration(),
     {"protocolVersion": 2, "status": "STATUS_OK",
      "pairingConfiguration": {
          "encoding": {"type": "ENCODING_TYPE_HEXADECIMAL", "symbolLength": 6},
          "clientRole": "ROLE_TYPE_INPUT"}}),
    ("pairing_secret", PAIRING,
     messages.pairing_secret(SECRET),
     {"protocolVersion": 2, "status": "STATUS_OK",
      "pairingSecret": {"secret": SECRET.hex()}}),
    ("remote_configure", REMOTE,
     messages.remote_configure("Windows PC", "Contoso", "gtv-remote", "1.0.0"),
     {"remoteConfigure": {
         "code1": 622,
         "deviceInfo": {"model": "Windows PC", "vendor": "Contoso", "unknown1": 1,
                        "unknown2": "1", "packageName": "gtv-remote",
                        "appVersion": "1.0.0"}}}),
    ("remote_set_active", REMOTE,
     messages.remote_set_active(),
     {"remoteSetActive": {"active": 622}}),
    ("remote_ping_response", REMOTE,
     messages.remote_ping_response(12345),
     {"remotePingResponse": {"val1": 12345}}),
    ("key_dpad_up", REMOTE,
     messages.remote_key_inject(keycodes.KEYCODE_DPAD_UP, keycodes.SHORT),
     {"remoteKeyInject": {"keyCode": "KEYCODE_DPAD_UP", "direction": "SHORT"}}),
    ("key_power_long", REMOTE,
     messages.remote_key_inject(keycodes.KEYCODE_POWER, keycodes.START_LONG),
     {"remoteKeyInject": {"keyCode": "KEYCODE_POWER", "direction": "START_LONG"}}),
    ("app_link", REMOTE,
     messages.remote_app_link_launch("https://www.youtube.com"),
     {"remoteAppLinkLaunchRequest": {"appLink": "https://www.youtube.com"}}),
    ("ime_batch_edit", REMOTE,
     messages.remote_ime_batch_edit(3, 7, "hello world", 10, 10),
     {"remoteImeBatchEdit": {
         "imeCounter": 3, "fieldCounter": 7,
         "editInfo": [{"insert": 1,
                       "textFieldStatus": {"start": 10, "end": 10, "value": "hello world"}}]}}),
]


def main() -> int:
    cases = [{"name": name, "type": kind, "hex": raw.hex(), "expected": expected}
             for name, kind, raw, expected in CASES]
    target = Path(__file__).parent / "cases.json"
    target.write_text(json.dumps(cases, indent=1) + "\n", encoding="utf-8", newline="\n")
    print(f"wrote {len(cases)} cases to {target}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
