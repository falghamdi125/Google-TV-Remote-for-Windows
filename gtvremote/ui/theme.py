"""Palette, fonts and the single scale factor the whole interface derives from.

Fonts and pixel sizes are recomputed by :func:`apply_scale`. Widgets must
read ``theme.FONT`` etc. at call time rather than importing the names, so a
rescale is picked up when the window is rebuilt.
"""

from __future__ import annotations

# --- palette ---------------------------------------------------------------
BG = "#15171c"
PANEL = "#1e2128"
BTN = "#2a2e37"
BTN_HOVER = "#3a3f4b"
BTN_ACTIVE = "#4a5060"
ACCENT = "#4c8dff"
ACCENT_HOVER = "#639dff"
DANGER = "#e05260"
WARNING = "#fbbc04"
OK_GREEN = "#34a853"
TEXT = "#e8eaed"
MUTED = "#9aa0a6"
POWER_BG = "#3a2126"
POWER_HOVER = "#54282f"
TOOLTIP_BG = "#000000"

#: Status-dot colour for each RemoteClient state.
STATUS_COLORS = {
    "connected": OK_GREEN,
    "connecting": WARNING,
    "reconnecting": WARNING,
    "error": DANGER,
    "unpaired": DANGER,
    "disconnected": MUTED,
}

# --- sizing ----------------------------------------------------------------
# Everything (fonts and button geometry) is derived from one scale factor so
# the whole window grows together. Adjustable at runtime with Ctrl +/- and
# remembered in settings.json as "ui_scale".
DEFAULT_SCALE = 1.4       # fits a 1080p screen with room for the taskbar
MIN_SCALE, MAX_SCALE = 0.8, 2.2
SCALE_STEP = 0.15

# --- layout (unscaled pixels; pass through px()) -----------------------------
MIN_WINDOW_WIDTH = 340
WINDOW_PADX = 12
WINDOW_PADY = 10
ROW_GAP = 8               # vertical space between rows of buttons
COL_GAP = 6               # horizontal space between buttons in a row
BUTTON_HEIGHT = 42        # every row: keys, app icons, entries and their buttons
SMALL_BUTTON_HEIGHT = 34  # dialog buttons
PANEL_PAD = 6             # inner padding of the connection bar and d-pad panel

UI_FONT = "Segoe UI"
ICON_FONT = "Segoe UI Symbol"

SCALE = DEFAULT_SCALE
FONT: tuple[str, int] = (UI_FONT, 15)
FONT_SMALL: tuple[str, int] = (UI_FONT, 13)
FONT_ICON: tuple[str, int] = (ICON_FONT, 20)
FONT_ICON_BIG: tuple[str, int] = (ICON_FONT, 24)


def clamp_scale(scale: float) -> float:
    return max(MIN_SCALE, min(MAX_SCALE, float(scale)))


def apply_scale(scale: float) -> None:
    """Recompute the font set for a new scale factor."""
    global SCALE, FONT, FONT_SMALL, FONT_ICON, FONT_ICON_BIG
    SCALE = clamp_scale(scale)
    FONT = (UI_FONT, max(8, round(10 * SCALE)))
    FONT_SMALL = (UI_FONT, max(7, round(8.5 * SCALE)))
    FONT_ICON = (ICON_FONT, max(9, round(13 * SCALE)))
    FONT_ICON_BIG = (ICON_FONT, max(11, round(16 * SCALE)))


def px(value: float) -> int:
    """Scale a pixel dimension."""
    return int(round(value * SCALE))


apply_scale(DEFAULT_SCALE)
