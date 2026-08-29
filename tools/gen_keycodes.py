"""Regenerate gtvremote/keycodes.py from proto/remotemessage.proto.

    python tools/gen_keycodes.py          # rewrite the module
    python tools/gen_keycodes.py --check  # exit 1 if it is out of date (CI)
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PROTO = ROOT / "proto" / "remotemessage.proto"
TARGET = ROOT / "gtvremote" / "keycodes.py"

ENUM_RE = re.compile(r"enum\s+(\w+)\s*\{(.*?)\}", re.DOTALL)
MEMBER_RE = re.compile(r"^\s*(\w+)\s*=\s*(-?\d+)\s*;", re.MULTILINE)

HEADER = '''\
"""Android key codes and press directions.

Generated from proto/remotemessage.proto by tools/gen_keycodes.py - do not
edit by hand. The values match Android's own KeyEvent constants.
"""
'''

FOOTER = '''\
#: Every KEYCODE_* constant by name, e.g. ``BY_NAME["KEYCODE_HOME"]``.
BY_NAME: dict[str, int] = {
    name: value for name, value in dict(globals()).items() if name.startswith("KEYCODE_")
}
'''


def enum_members(source: str, name: str) -> list[tuple[str, int]]:
    for match in ENUM_RE.finditer(source):
        if match.group(1) == name:
            body = match.group(2)
            return [(m.group(1), int(m.group(2))) for m in MEMBER_RE.finditer(body)]
    raise SystemExit(f"enum {name} not found in {PROTO}")


def render() -> str:
    source = PROTO.read_text(encoding="utf-8")
    parts = [HEADER]
    for enum in ("RemoteDirection", "RemoteKeyCode"):
        lines = [f"# --- {enum} ---"]
        lines += [f"{name} = {value}" for name, value in enum_members(source, enum)]
        parts.append("\n".join(lines) + "\n")
    parts.append(FOOTER)
    return "\n".join(parts)


def main(argv: list[str]) -> int:
    text = render()
    if "--check" in argv:
        current = TARGET.read_text(encoding="utf-8") if TARGET.exists() else ""
        if current == text:
            print(f"{TARGET.name} is up to date")
            return 0
        print(f"{TARGET.name} is out of date - run python tools/gen_keycodes.py")
        return 1
    TARGET.write_text(text, encoding="utf-8", newline="\n")
    print(f"wrote {TARGET}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
