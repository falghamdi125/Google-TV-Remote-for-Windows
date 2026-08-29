"""Per-user settings, stored as JSON in the app's data directory.

On Windows that is the ``GoogleTVRemote`` folder under ``%APPDATA%``;
elsewhere ``~/.config/GoogleTVRemote``. The same directory holds the client
certificate (see :mod:`gtvremote.certs`).
"""

from __future__ import annotations

import copy
import json
import os
from pathlib import Path

APP_DIR_NAME = "GoogleTVRemote"
SETTINGS_FILE = "settings.json"
MAX_REMEMBERED_DEVICES = 10

DEFAULTS: dict = {
    "last_host": "",
    "client_name": "Windows Remote",
    "devices": [],           # [{"name": ..., "host": ...}], most recent first
    "window_geometry": "",
    "ui_scale": 1.5,
    "auto_connect": True,
    "apps": [],              # [{"name": ..., "link": ...}]; empty = DEFAULT_APPS
}

# Plain https:// links are handled by more than one app, so Android shows an
# "Open with" chooser instead of launching. A private scheme (vnd.youtube://)
# or market://launch?id=<package> resolves to exactly one app, so it opens
# straight away. Override these in settings.json under "apps".
DEFAULT_APPS: list[tuple[str, str]] = [
    ("YouTube", "vnd.youtube://"),
    ("Netflix", "netflix://"),
    ("Prime Video", "market://launch?id=com.amazon.amazonvideo.livingroom"),
    ("Disney+", "market://launch?id=com.disney.disneyplus"),
    ("Spotify", "spotify://"),
]


def config_dir() -> Path:
    """The per-user data directory, created on first use."""
    appdata = os.environ.get("APPDATA")
    base = Path(appdata) if appdata else Path.home() / ".config"
    path = base / APP_DIR_NAME
    path.mkdir(parents=True, exist_ok=True)
    return path


def settings_path() -> Path:
    return config_dir() / SETTINGS_FILE


def load() -> dict:
    """Read settings, falling back to DEFAULTS for anything missing or broken."""
    data = copy.deepcopy(DEFAULTS)
    try:
        stored = json.loads(settings_path().read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return data
    if isinstance(stored, dict):
        data.update({key: value for key, value in stored.items() if key in DEFAULTS})
    return data


def save(data: dict) -> None:
    """Write settings. Failures are ignored: settings are a convenience only."""
    payload = {key: value for key, value in data.items() if key in DEFAULTS}
    try:
        settings_path().write_text(json.dumps(payload, indent=2), encoding="utf-8")
    except OSError:
        pass


def remember_device(data: dict, host: str, name: str = "") -> None:
    """Record a successfully paired device, most recent first."""
    devices = [d for d in data.get("devices", [])
               if isinstance(d, dict) and d.get("host") != host]
    devices.insert(0, {"name": name or host, "host": host})
    data["devices"] = devices[:MAX_REMEMBERED_DEVICES]
    data["last_host"] = host


def app_shortcuts(data: dict) -> list[tuple[str, str]]:
    """App buttons from the "apps" setting, falling back to DEFAULT_APPS.

    Accepts ``{"name": ..., "link": ...}`` entries or ``[name, link]`` pairs.
    A link without a scheme is taken as a package name and turned into a
    ``market://`` launch link.
    """
    apps: list[tuple[str, str]] = []
    for entry in data.get("apps") or []:
        if isinstance(entry, dict):
            name, link = entry.get("name"), entry.get("link")
        elif isinstance(entry, (list, tuple)) and len(entry) == 2:
            name, link = entry
        else:
            continue
        if not name or not link:
            continue
        link = str(link)
        if "://" not in link:
            link = "market://launch?id=" + link
        apps.append((str(name), link))
    return apps or list(DEFAULT_APPS)
