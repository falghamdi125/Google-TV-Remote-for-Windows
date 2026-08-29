"""Custom Tk widgets: a flat rounded button and a hover tooltip."""

from __future__ import annotations

import tkinter as tk
from typing import Callable

from . import theme
from .theme import ACCENT, BTN, BTN_ACTIVE, BTN_HOVER, MUTED, TEXT, TOOLTIP_BG, px

REPEAT_DELAY_MS = 500
REPEAT_INTERVAL_MS = 120
FLASH_MS = 110
LABEL_MARGIN = 22          # unscaled: horizontal room around a text label
ICON_TAG = "icon"


class RoundButton(tk.Canvas):
    """A flat rounded button; tkinter has no native one.

    ``width`` and ``height`` are unscaled *minimums*. When the geometry
    manager grants more room (``sticky="ew"`` in a weighted column) the shape
    is redrawn to fill it, so rows of buttons line up edge to edge.

    The face shows ``text`` or, if given, an ``icon`` - a callable
    ``(canvas, cx, cy, size, enabled)`` that draws onto the button with the
    ``"icon"`` tag (see :mod:`gtvremote.ui.icons`). With ``repeat=True`` the
    command keeps firing while the mouse button is held (arrow and volume
    keys).
    """

    def __init__(self, parent, text="", command=None, width=58, height=44,
                 radius=12, fill=BTN, hover=BTN_HOVER, fg=TEXT, font=None,
                 repeat=False, tooltip="",
                 icon: Callable[[tk.Canvas, float, float, int, bool], None] | None = None,
                 icon_size=26):
        width, height, radius = px(width), px(height), px(radius)
        super().__init__(parent, width=width, height=height, bg=parent["bg"],
                         highlightthickness=0, bd=0, cursor="hand2")
        self.command = command
        self._fill = fill
        self._hover = hover
        self._fg = fg
        self._repeat = repeat
        self._repeat_job = None
        self._enabled = True
        self._radius = radius
        self._width, self._height = width, height
        self._icon = icon
        self._icon_size = px(icon_size)
        self._shape = self.create_polygon(self._outline(width, height), smooth=True,
                                          splinesteps=24, fill=fill, outline="")
        self._label = self.create_text(width / 2, height / 2, text=text, fill=fg,
                                       font=font or theme.FONT)
        self._grow_to_fit()
        self._draw_icon()

        self.bind("<Configure>", self._on_configure)
        self.bind("<Enter>", self._on_enter)
        self.bind("<Leave>", self._on_leave)
        self.bind("<ButtonPress-1>", self._on_press)
        self.bind("<ButtonRelease-1>", self._on_release)

        if tooltip:
            Tooltip(self, tooltip)

    # -- geometry ---------------------------------------------------------

    def _outline(self, width: int, height: int) -> list[float]:
        x1, y1, x2, y2, r = 1, 1, width - 1, height - 1, self._radius
        return [x1 + r, y1, x2 - r, y1, x2, y1, x2, y1 + r, x2, y2 - r, x2, y2,
                x2 - r, y2, x1 + r, y2, x1, y2, x1, y2 - r, x1, y1 + r, x1, y1]

    def _on_configure(self, event) -> None:
        """Redraw to fill whatever size the geometry manager granted."""
        if (event.width, event.height) == (self._width, self._height):
            return
        self._width, self._height = event.width, event.height
        try:
            self.coords(self._shape, *self._outline(event.width, event.height))
            self.coords(self._label, event.width / 2, event.height / 2)
            self._draw_icon()
        except tk.TclError:
            pass                                        # <Configure> during teardown

    def _grow_to_fit(self) -> None:
        """Raise the minimum width if the label would be clipped.

        Only ever grows, so toggling a label (Connect/Disconnect) does not
        make the layout jump around.
        """
        bounds = self.bbox(self._label)
        if not bounds:
            return
        needed = (bounds[2] - bounds[0]) + px(LABEL_MARGIN)
        if needed > self._width:
            self.configure(width=needed)        # <Configure> redraws the shape

    def _draw_icon(self) -> None:
        self.delete(ICON_TAG)
        if self._icon is not None:
            self._icon(self, self._width / 2, self._height / 2, self._icon_size, self._enabled)

    # -- public -----------------------------------------------------------

    def set_enabled(self, enabled: bool) -> None:
        self._enabled = enabled
        self.itemconfig(self._label, fill=self._fg if enabled else MUTED)
        self.configure(cursor="hand2" if enabled else "arrow")
        self._draw_icon()

    def set_text(self, text: str) -> None:
        self.itemconfig(self._label, text=text)
        self._grow_to_fit()

    def set_fill(self, fill: str, hover: str | None = None) -> None:
        self._fill = fill
        self._hover = hover or fill
        self.itemconfig(self._shape, fill=fill)

    def flash(self) -> None:
        """Brief visual confirmation that a key was sent."""
        self.itemconfig(self._shape, fill=ACCENT)
        self.after(FLASH_MS, lambda: self.itemconfig(self._shape, fill=self._fill))

    # -- mouse ------------------------------------------------------------

    def _on_enter(self, _event=None) -> None:
        if self._enabled:
            self.itemconfig(self._shape, fill=self._hover)

    def _on_leave(self, _event=None) -> None:
        self.itemconfig(self._shape, fill=self._fill)
        self._cancel_repeat()

    def _on_press(self, _event=None) -> None:
        if not self._enabled:
            return
        self.itemconfig(self._shape, fill=BTN_ACTIVE)
        self._fire()
        if self._repeat:
            self._repeat_job = self.after(REPEAT_DELAY_MS, self._auto_repeat)

    def _on_release(self, _event=None) -> None:
        self._cancel_repeat()
        self.itemconfig(self._shape, fill=self._hover if self._enabled else self._fill)

    def _auto_repeat(self) -> None:
        self._fire()
        self._repeat_job = self.after(REPEAT_INTERVAL_MS, self._auto_repeat)

    def _cancel_repeat(self) -> None:
        if self._repeat_job is not None:
            self.after_cancel(self._repeat_job)
            self._repeat_job = None

    def _fire(self) -> None:
        if self._enabled and self.command:
            self.command()


class EvenRow(tk.Frame):
    """Lays its children out side by side with equal widths and a fixed gap.

    Children are positioned by hand on every resize, so a row of 3, 4 or 5
    buttons always spans exactly the same width and lines up with the rows
    around it. (Tk's grid cannot promise equal cells *and* fixed gaps.)
    """

    def __init__(self, parent, gap: int, height: int, bg: str) -> None:
        super().__init__(parent, bg=bg, height=height)
        self._gap = gap
        self._children: list[tk.Widget] = []
        self.bind("<Configure>", self._layout)

    def add(self, widget: tk.Widget) -> tk.Widget:
        """Append a child created with this row as its parent."""
        self._children.append(widget)
        count = len(self._children)
        widest = max(child.winfo_reqwidth() for child in self._children)
        self.configure(width=widest * count + (count - 1) * self._gap)
        return widget

    def _layout(self, event=None) -> None:
        count = len(self._children)
        if not count:
            return
        try:
            total, height = self.winfo_width(), self.winfo_height()
            cell = (total - (count - 1) * self._gap) / count
            for index, child in enumerate(self._children):
                x = round(index * (cell + self._gap))
                width = round((index + 1) * (cell + self._gap) - self._gap) - x
                child.place(x=x, y=0, width=width, height=height)
        except tk.TclError:
            pass                                        # <Configure> during teardown


class Tooltip:
    """Small black label shown under a widget while the mouse hovers it."""

    def __init__(self, widget, text: str) -> None:
        self.widget = widget
        self.text = text
        self.window: tk.Toplevel | None = None
        widget.bind("<Enter>", self._show, add="+")
        widget.bind("<Leave>", self._hide, add="+")
        widget.bind("<ButtonPress>", self._hide, add="+")

    def _show(self, _event=None) -> None:
        if self.window or not self.text:
            return
        x = self.widget.winfo_rootx() + self.widget.winfo_width() // 2
        y = self.widget.winfo_rooty() + self.widget.winfo_height() + 4
        self.window = tk.Toplevel(self.widget)
        self.window.wm_overrideredirect(True)
        label = tk.Label(self.window, text=self.text, bg=TOOLTIP_BG, fg=TEXT,
                         font=theme.FONT_SMALL, padx=6, pady=3, bd=0)
        label.pack()
        self.window.update_idletasks()
        self.window.wm_geometry(f"+{x - self.window.winfo_width() // 2}+{y}")

    def _hide(self, _event=None) -> None:
        if self.window:
            self.window.destroy()
            self.window = None
