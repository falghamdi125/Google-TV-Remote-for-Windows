"""Render the window in a "connected" state and save it as a PNG.

    python tools/screenshot_ui.py [output.png]

Needs Pillow (pip install pillow). Used to refresh docs/screenshot.png.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import tkinter as tk  # noqa: E402

from PIL import ImageGrab  # noqa: E402

from gtvremote import config  # noqa: E402
from gtvremote.ui import RemoteApp  # noqa: E402


def main(argv: list[str]) -> int:
    output = Path(argv[0]) if argv else Path("ui_preview.png")
    try:
        from ctypes import windll
        windll.shcore.SetProcessDpiAwareness(1)
    except (ImportError, AttributeError, OSError):
        pass

    # Keep the tool off the real TV and out of the user's saved settings.
    settings = {**config.DEFAULTS, "auto_connect": False, "window_geometry": ""}
    with mock.patch.object(config, "load", return_value=settings), \
            mock.patch.object(config, "save"):
        root = tk.Tk()
        app = RemoteApp(root)

    app.host_var.set("192.168.1.42")
    root.geometry("+60+30")
    root.update_idletasks()
    root.update()
    root.lift()
    root.attributes("-topmost", True)
    root.update()
    time.sleep(1.5)
    # Apply the display state last so a stray keypress during focus cannot alter it.
    for event in [("state", "connected", "Connected to 192.168.1.42"),
                  ("volume", 37, 100, False),
                  ("app", "com.google.android.youtube.tv")]:
        app._handle_event(event)
    root.update()

    x, y = root.winfo_rootx(), root.winfo_rooty()
    width, height = root.winfo_width(), root.winfo_height()
    image = ImageGrab.grab(bbox=(x, y, x + width, y + height), all_screens=True)
    image.save(output)
    print(f"saved {output} ({image.size[0]}x{image.size[1]})")
    root.destroy()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
