"""Small vector app icons drawn straight onto a button's canvas.

No bitmap assets: each icon is a brand-coloured tile with a simple glyph,
so it scales with the UI and needs no image files (and bundles no
trademarked artwork). Apps without a known icon get a neutral tile showing
the first letter of their name.

Every drawing function has the signature ``(canvas, cx, cy, size, enabled)``
and tags what it draws with ``"icon"`` so the button can redraw it.
"""

from __future__ import annotations

import functools
import tkinter as tk
from typing import Callable

from . import theme

TAG = "icon"

DIM_TILE = "#33373f"
DIM_GLYPH = theme.MUTED
GENERIC_TILE = theme.BTN_ACTIVE

IconDrawer = Callable[[tk.Canvas, float, float, int, bool], None]


# ------------------------------------------------------------ primitives ---

def _tile(canvas, cx, cy, width, height, fill, radius_ratio=0.24) -> None:
    r = min(width, height) * radius_ratio
    x1, y1, x2, y2 = cx - width / 2, cy - height / 2, cx + width / 2, cy + height / 2
    points = [x1 + r, y1, x2 - r, y1, x2, y1, x2, y1 + r, x2, y2 - r, x2, y2,
              x2 - r, y2, x1 + r, y2, x1, y2, x1, y2 - r, x1, y1 + r, x1, y1]
    canvas.create_polygon(points, smooth=True, splinesteps=16, fill=fill,
                          outline="", tags=TAG)


def _play(canvas, cx, cy, height, fill) -> None:
    """A play triangle, nudged right so it looks centred."""
    width = height * 0.9
    cx += width * 0.08
    canvas.create_polygon(cx - width / 2, cy - height / 2, cx - width / 2, cy + height / 2,
                          cx + width / 2, cy, fill=fill, outline="", tags=TAG)


def _letters(canvas, cx, cy, size, text, fill, ratio=0.5) -> None:
    font = (theme.UI_FONT, max(6, round(size * ratio)), "bold")
    canvas.create_text(cx, cy, text=text, fill=fill, font=font, tags=TAG)


def _stroke(size) -> int:
    return max(1, round(size * 0.075))


# ----------------------------------------------------------------- icons ---

def youtube(canvas, cx, cy, size, enabled) -> None:
    _tile(canvas, cx, cy, size, size * 0.72, "#ff0000" if enabled else DIM_TILE, 0.3)
    _play(canvas, cx, cy, size * 0.36, "white" if enabled else DIM_GLYPH)


def netflix(canvas, cx, cy, size, enabled) -> None:
    _tile(canvas, cx, cy, size, size, "#141414" if enabled else DIM_TILE)
    _letters(canvas, cx, cy, size, "N", "#e50914" if enabled else DIM_GLYPH, 0.62)


def prime_video(canvas, cx, cy, size, enabled) -> None:
    glyph = "white" if enabled else DIM_GLYPH
    _tile(canvas, cx, cy, size, size, "#00a8e1" if enabled else DIM_TILE)
    _play(canvas, cx, cy - size * 0.1, size * 0.34, glyph)
    # The "smile" swoosh under the play button.
    canvas.create_arc(cx - size * 0.3, cy - size * 0.08, cx + size * 0.3, cy + size * 0.4,
                      start=200, extent=140, style="arc", outline=glyph,
                      width=_stroke(size), tags=TAG)


def disney_plus(canvas, cx, cy, size, enabled) -> None:
    _tile(canvas, cx, cy, size, size, "#1a3fb8" if enabled else DIM_TILE)
    _letters(canvas, cx, cy, size, "D+", "white" if enabled else DIM_GLYPH, 0.42)


def spotify(canvas, cx, cy, size, enabled) -> None:
    r = size / 2
    canvas.create_oval(cx - r, cy - r, cx + r, cy + r,
                       fill="#1db954" if enabled else DIM_TILE, outline="", tags=TAG)
    glyph = "#191414" if enabled else DIM_GLYPH
    for index in range(3):                              # three sound waves
        half_width = size * (0.33 - 0.06 * index)
        top = cy - size * 0.32 + index * size * 0.18
        canvas.create_arc(cx - half_width, top, cx + half_width, top + size * 0.6,
                          start=35, extent=110, style="arc", outline=glyph,
                          width=_stroke(size), tags=TAG)


def lettered(name: str, canvas, cx, cy, size, enabled) -> None:
    """Fallback for apps without a known icon: a tile with the initial."""
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
