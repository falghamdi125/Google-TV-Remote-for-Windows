"""App icons for the shortcut buttons.

The bundled apps show their real logos: PNGs in ``gtvremote/assets/icons``,
rendered by ``tools/render_icons.py`` from the brand glyphs at every size
the interface can ask for, each in an enabled and a dimmed variant. Any
other app gets a neutral tile with the first letter of its name, drawn
straight onto the canvas.

Every drawing function has the signature ``(canvas, cx, cy, size, enabled)``
and tags what it draws with ``"icon"`` so the button can redraw it.
"""

from __future__ import annotations

import functools
import sys
import tkinter as tk
from pathlib import Path
from typing import Callable

from . import theme

TAG = "icon"

DIM_TILE = "#33373f"
DIM_GLYPH = theme.MUTED
GENERIC_TILE = theme.BTN_ACTIVE
#: Pixel sizes the PNGs exist in; must match tools/render_icons.py.
SIZES = tuple(range(20, 69, 4))

IconDrawer = Callable[[tk.Canvas, float, float, int, bool], None]


# --------------------------------------------------------------- bitmaps ---

def icon_dir() -> Path | None:
    """The rendered icons, whether running from source, pip-installed or frozen."""
    candidates = [Path(__file__).resolve().parent.parent / "assets" / "icons"]
    bundle = getattr(sys, "_MEIPASS", None)
    if bundle:
        candidates.append(Path(bundle) / "gtvremote" / "assets" / "icons")
    return next((path for path in candidates if path.is_dir()), None)


def icon_file(slug: str, size: float, enabled: bool) -> Path | None:
    """The PNG for ``slug`` nearest to ``size`` pixels, or None if missing."""
    directory = icon_dir()
    if directory is None:
        return None
    nearest = min(SIZES, key=lambda candidate: abs(candidate - size))
    path = directory / f"{slug}-{nearest}{'' if enabled else '-dim'}.png"
    return path if path.exists() else None


def bitmap(slug: str, name: str) -> IconDrawer:
    """A drawer that shows the rendered logo for ``slug``.

    Falls back to the lettered tile when the PNG is not there (a source
    checkout before the icons were rendered, or a stripped install), so a
    missing asset can never break the window.
    """
    def draw(canvas: tk.Canvas, cx: float, cy: float, size: int, enabled: bool) -> None:
        path = icon_file(slug, size, enabled)
        if path is not None:
            try:
                image = tk.PhotoImage(master=canvas, file=str(path))
            except tk.TclError:
                image = None
            if image is not None:
                canvas.create_image(cx, cy, image=image, tags=TAG)
                canvas._icon_image = image      # Tk holds no reference; keep one
                return
        lettered(name, canvas, cx, cy, size, enabled)

    draw.__name__ = slug
    return draw


youtube = bitmap("youtube", "YouTube")
netflix = bitmap("netflix", "Netflix")
prime_video = bitmap("primevideo", "Prime Video")
disney_plus = bitmap("disneyplus", "Disney+")
spotify = bitmap("spotify", "Spotify")


# -------------------------------------------------------------- fallback ---

def _tile(canvas, cx, cy, width, height, fill, radius_ratio=0.24) -> None:
    r = min(width, height) * radius_ratio
    x1, y1, x2, y2 = cx - width / 2, cy - height / 2, cx + width / 2, cy + height / 2
    points = [x1 + r, y1, x2 - r, y1, x2, y1, x2, y1 + r, x2, y2 - r, x2, y2,
              x2 - r, y2, x1 + r, y2, x1, y2, x1, y2 - r, x1, y1 + r, x1, y1]
    canvas.create_polygon(points, smooth=True, splinesteps=16, fill=fill,
                          outline="", tags=TAG)


def _letters(canvas, cx, cy, size, text, fill, ratio=0.5) -> None:
    font = (theme.UI_FONT, max(6, round(size * ratio)), "bold")
    canvas.create_text(cx, cy, text=text, fill=fill, font=font, tags=TAG)


def lettered(name: str, canvas, cx, cy, size, enabled) -> None:
    """Apps without a bundled logo: a tile with the initial."""
    initial = (name.strip()[:1] or "?").upper()
    _tile(canvas, cx, cy, size, size, GENERIC_TILE if enabled else DIM_TILE)
    _letters(canvas, cx, cy, size, initial, theme.TEXT if enabled else DIM_GLYPH, 0.52)


# Matched against the lower-cased app name, first hit wins.
KNOWN_ICONS: list[tuple[str, IconDrawer]] = [
    ("youtube", youtube),
    ("netflix", netflix),
    ("prime", prime_video),
    ("amazon", prime_video),
    ("disney", disney_plus),
    ("spotify", spotify),
]


def app_icon(name: str) -> IconDrawer:
    """The drawing function for an app button, by app name."""
    lowered = name.lower()
    for keyword, drawer in KNOWN_ICONS:
        if keyword in lowered:
            return drawer
    return functools.partial(lettered, name)
