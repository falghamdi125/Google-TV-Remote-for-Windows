"""Input-source switching (HDMI, AV, tuner).

Android TV has no universal "switch to HDMI n" command. The TV_INPUT key
codes are honoured by some makers and silently ignored by others (TCL among
them). What does work is the TV-input framework itself: viewing a
``content://android.media.tv/passthrough/<input id>`` URI starts the TV app
on that input, and the remote protocol's app-link launch delivers exactly
such an ACTION_VIEW. The input ids are vendor specific, so they are listed
per vendor here and can be overridden in settings.json under "inputs".
"""

from __future__ import annotations

import urllib.parse

from . import keycodes

PASSTHROUGH_PREFIX = "content://android.media.tv/passthrough/"

#: An input entry: (label, ("key", key_code)) or (label, ("link", uri)).
Source = tuple[str, tuple[str, object]]


def passthrough_link(input_id: str) -> str:
    """The app link that tunes the TV app to ``input_id``."""
    return PASSTHROUGH_PREFIX + urllib.parse.quote(input_id, safe="")


def _tcl(hw_id: str) -> tuple[str, str]:
    return ("link", passthrough_link(
        f"com.tcl.tvinput/.passthroughinput.TvPassThroughService/{hw_id}"))


# Verified on a TCL Google TV ("Smart TV Pro"); the ids are shared by the
# C635 / C825 generations. Sets with fewer ports just ignore the extras.
TCL_SOURCES: list[Source] = [
    ("HDMI 1", _tcl("HW1413744128")),
    ("HDMI 2", _tcl("HW1413744384")),
    ("HDMI 3", _tcl("HW1413744640")),
    ("HDMI 4", _tcl("HW1413745664")),
    ("AV", _tcl("HW1413743104")),
    ("TV tuner", _tcl("HW1413742848")),
]

# For everyone else: the key codes, which work on e.g. Sony sets.
KEY_SOURCES: list[Source] = [
    ("Input picker", ("key", keycodes.KEYCODE_TV_INPUT)),
    ("HDMI 1", ("key", keycodes.KEYCODE_TV_INPUT_HDMI_1)),
    ("HDMI 2", ("key", keycodes.KEYCODE_TV_INPUT_HDMI_2)),
    ("HDMI 3", ("key", keycodes.KEYCODE_TV_INPUT_HDMI_3)),
    ("HDMI 4", ("key", keycodes.KEYCODE_TV_INPUT_HDMI_4)),
    ("Live TV", ("key", keycodes.KEYCODE_TV)),
]

BY_VENDOR: dict[str, list[Source]] = {
    "tcl": TCL_SOURCES,
}


def input_sources(vendor: str | None, settings: dict | None = None) -> list[Source]:
    """The Input menu for a TV: custom entries from settings, else the
    vendor's known passthrough links, else the generic key codes."""
    custom = _from_settings(settings or {})
    if custom:
        return custom
    known = BY_VENDOR.get((vendor or "").strip().lower())
    if known:
        return known + [("Input picker", ("key", keycodes.KEYCODE_TV_INPUT))]
    return KEY_SOURCES


def _from_settings(settings: dict) -> list[Source]:
    """Entries look like {"name": ..., "link": <uri or bare input id>} or
    {"name": ..., "key": "KEYCODE_TV_INPUT_HDMI_1"}."""
    out: list[Source] = []
    for entry in settings.get("inputs") or []:
        if not isinstance(entry, dict) or not entry.get("name"):
            continue
        name = str(entry["name"])
        if entry.get("link"):
            link = str(entry["link"])
            if "://" not in link:                       # bare input id
                link = passthrough_link(link)
            out.append((name, ("link", link)))
        elif entry.get("key"):
            key = entry["key"]
            code = key if isinstance(key, int) else keycodes.BY_NAME.get(str(key).upper())
            if code is not None:
                out.append((name, ("key", code)))
    return out
