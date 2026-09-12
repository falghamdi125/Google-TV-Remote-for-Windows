"""The main window: connection bar, remote buttons, and the event pump that
moves worker-thread callbacks onto the Tk thread."""

from __future__ import annotations

import logging
import queue
import sys
import threading
import tkinter as tk
from logging.handlers import RotatingFileHandler
from pathlib import Path
from tkinter import font as tkfont
from tkinter import messagebox, simpledialog

from .. import __version__, config, discovery, inputs, keycodes
from ..pairing import PairingError, PairingSession
from ..remote import RemoteClient
from . import theme
from .icons import app_icon
from .theme import (ACCENT, ACCENT_HOVER, BG, BTN, BTN_HOVER, MUTED,
                    PANEL, POWER_BG, POWER_HOVER, TEXT, px)
from .widgets import EvenRow, Placeholder, RoundButton, Tooltip, VolumeMeter

APP_TITLE = "Google TV Remote"
ICON_FILE = "app.ico"
EVENT_POLL_MS = 50
AUTO_CONNECT_DELAY_MS = 250
LOG_MAX_BYTES = 256_000                 # the connection log rotates once at this size
MAX_APP_LABEL = 44
MAX_APPS_PER_ROW = 6

#: Friendly names for the foreground-app readout. Unknown packages are shown
#: as-is (and truncated), so this only ever improves the common cases.
APP_NAMES = {
    "com.google.android.youtube.tv": "YouTube",
    "com.google.android.youtube.tvunplugged": "YouTube",
    "com.google.android.youtube.tvkids": "YouTube Kids",
    "com.netflix.ninja": "Netflix",
    "com.amazon.amazonvideo.livingroom": "Prime Video",
    "com.disney.disneyplus": "Disney+",
    "com.spotify.tv.android": "Spotify",
    "com.spotify.music": "Spotify",
    "com.google.android.tvlauncher": "Home",
    "com.google.android.apps.tv.launcherx": "Home",
    "com.google.android.tvrecommendations": "Home",
    "com.google.android.tv": "Live TV",
    "com.google.android.videos": "Google TV",
    "com.google.android.katniss": "Google Assistant",
    "com.android.tv.settings": "Settings",
    "com.plexapp.android": "Plex",
    "org.xbmc.kodi": "Kodi",
    "tv.twitch.android.app": "Twitch",
    "com.hbo.hbonow": "Max",
    "com.wbd.stream": "Max",
    "com.apple.atve.androidtv.appletv": "Apple TV",
}


def friendly_app_name(package: str) -> str:
    """A readable name for a foreground package, or the package unchanged."""
    return APP_NAMES.get(package, package)


#: Shown in the Shortcuts popup. The app is keyboard-first (see the README),
#: but nothing on screen said so until this.
SHORTCUTS = [
    ("↑ ↓ ← →", "Move around"),
    ("Enter", "OK / select"),
    ("Backspace  ·  Esc", "Back"),
    ("Home", "Home screen"),
    ("Space", "Play / Pause"),
    ("+   −   M", "Volume up, down, mute"),
    ("Just start typing", "Goes to the TV's text box; Enter submits"),
    ("Ctrl +   Ctrl −   Ctrl 0", "Resize the window, or reset"),
]


def icon_path() -> Path | None:
    """The bundled .ico, whether running from source, pip-installed or frozen."""
    candidates = [Path(__file__).resolve().parent.parent / "assets" / ICON_FILE]
    bundle = getattr(sys, "_MEIPASS", None)
    if bundle:
        candidates.append(Path(bundle) / "gtvremote" / "assets" / ICON_FILE)
    return next((path for path in candidates if path.exists()), None)


def fit_text(font: tkfont.Font, text: str, width: int) -> str:
    """Shorten ``text`` with an ellipsis until it fits ``width`` pixels."""
    if font.measure(text) <= width:
        return text
    while text and font.measure(text + "…") > width:
        text = text[:-1]
    return text + "…"


def balanced_rows(items: list, max_per_row: int) -> list[list]:
    """Split ``items`` into rows of at most ``max_per_row``, as evenly as
    possible: 5 -> [5], 7 -> [4, 3], 9 -> [5, 4]."""
    if not items:
        return []
    rows = -(-len(items) // max_per_row)
    per_row = -(-len(items) // rows)
    return [items[start:start + per_row] for start in range(0, len(items), per_row)]


class RemoteApp:
    """The remote window. Owns the RemoteClient and all Tk state."""

    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.settings = config.load()
        self.client: RemoteClient | None = None
        self.events: queue.Queue[tuple] = queue.Queue()
        self._buttons: list[RoundButton] = []
        self._last_status = "disconnected"
        self._app_text = ""                  # full text behind the fitted app label
        self._volume = (0, 0, False)         # last (level, maximum, muted) from the TV
        self._volume_known = False           # has the TV reported volume this session
        self._help_win: tk.Toplevel | None = None
        self._pump_job: str | None = None
        theme.apply_scale(self.settings.get("ui_scale", theme.DEFAULT_SCALE))

        root.title(APP_TITLE)
        root.configure(bg=BG)
        self._set_icon()
        root.protocol("WM_DELETE_WINDOW", self.on_close)

        self._build()
        self._fit_window()
        self._bind_keys()
        self._drain_events()

        last = self.settings.get("last_host")
        if last:
            self.host_var.set(last)
            if self.settings.get("auto_connect", True):
                # Let the window paint first, then connect on its own.
                self._set_status("connecting", f"Connecting to {last}...")
                root.after(AUTO_CONNECT_DELAY_MS, self._auto_connect)

    # ------------------------------------------------------------ window --

    def _set_icon(self) -> None:
        icon = icon_path()
        if icon is None:
            return
        try:
            self.root.iconbitmap(default=str(icon))
        except tk.TclError:
            pass

    def _fit_window(self, force: bool = False) -> None:
        """Size the window to its content so it survives any display scaling."""
        self.root.update_idletasks()
        width = max(self.root.winfo_reqwidth(), px(theme.MIN_WINDOW_WIDTH))
        height = self.root.winfo_reqheight()
        # Never open taller than the screen the window lives on.
        height = min(height, self.root.winfo_screenheight() - 80)
        self.root.minsize(width, min(height, px(460)))

        saved = self.settings.get("window_geometry")
        if saved and not force:
            try:
                self.root.geometry(saved)
                return
            except tk.TclError:
                pass
        self.root.geometry(f"{width}x{height}")

    def set_scale(self, scale: float) -> None:
        """Rebuild the interface at a new size and remember the choice."""
        scale = theme.clamp_scale(scale)
        if abs(scale - theme.SCALE) < 0.01:
            return
        host = self.host_var.get()
        status_key, status_text = self._last_status, self.status_var.get()
        volume, app_text = self._volume, self._app_text

        theme.apply_scale(scale)
        self.settings["ui_scale"] = theme.SCALE
        self.settings["window_geometry"] = ""      # let it re-fit at the new size
        config.save(self.settings)

        self._outer.destroy()
        self._buttons.clear()
        self._build()

        self.host_var.set(host)
        if self._volume_known:
            self._update_volume(*volume)
        else:
            self._clear_volume()
        self._set_app_text(app_text)
        self._set_status(status_key, status_text)
        self._show_connected(self._is_connected())
        self._fit_window(force=True)

    # ------------------------------------------------------------- build --

    def _build(self) -> None:
        outer = tk.Frame(self.root, bg=BG)
        outer.pack(fill="both", expand=True, padx=px(theme.WINDOW_PADX),
                   pady=px(theme.WINDOW_PADY))
        self._outer = outer

        self._build_connection_bar(outer)
        self._build_status(outer)
        self._build_top_row(outer)
        self._build_dpad(outer)
        self._build_nav_row(outer)
        self._build_volume_row(outer)
        self._build_media_row(outer)
        self._build_text_row(outer)
        self._build_apps(outer)
        self._build_footer(outer)
        self._set_controls_enabled(False)

    def _section(self, parent, gap: float = theme.ROW_GAP) -> tk.Frame:
        frame = tk.Frame(parent, bg=BG)
        frame.pack(fill="x", pady=(px(gap), 0))
        return frame

    def _row(self, parent, gap: float = theme.ROW_GAP,
             height: float = theme.BUTTON_HEIGHT) -> EvenRow:
        """A full-width row of equally sized buttons."""
        row = EvenRow(parent, gap=px(theme.COL_GAP), height=px(height), bg=BG)
        row.pack(fill="x", pady=(px(gap), 0))
        return row

    def _entry(self, parent, variable: tk.StringVar) -> tuple[tk.Entry, int]:
        """A text box plus the ``ipady`` that makes it as tall as the buttons."""
        # The small font: an address or a search term, not a heading. The
        # box still stretches to button height through ipady below.
        entry = tk.Entry(parent, textvariable=variable, bg=BTN, fg=TEXT,
                         insertbackground=TEXT, relief="flat", font=theme.FONT_SMALL,
                         highlightthickness=1, highlightbackground=BTN,
                         highlightcolor=ACCENT)
        ipady = max(0, (px(theme.BUTTON_HEIGHT) - entry.winfo_reqheight()) // 2)
        return entry, ipady

    def _key_button(self, parent, text, key_code, **kwargs) -> RoundButton:
        """A remote button that injects ``key_code`` and flashes on send."""
        kwargs.setdefault("height", theme.BUTTON_HEIGHT)
        button = RoundButton(parent, text=text, **kwargs)
        button.command = lambda: self.send_key(key_code, button)
        self._buttons.append(button)
        return button

    def _build_connection_bar(self, parent) -> None:
        bar = tk.Frame(parent, bg=PANEL)
        bar.pack(fill="x")
        inner = tk.Frame(bar, bg=PANEL)
        inner.pack(fill="x", padx=px(theme.PANEL_PAD), pady=px(theme.PANEL_PAD))

        self.host_var = tk.StringVar()
        self.host_entry, ipady = self._entry(inner, self.host_var)
        self.host_entry.configure(width=12)
        self.host_entry.pack(side="left", fill="x", expand=True, ipady=ipady,
                             padx=(0, px(theme.COL_GAP)))
        self.host_entry.bind("<Return>", lambda _e: self.toggle_connection())
        Placeholder(self.host_entry, self.host_var, "TV IP address")

        # fill="y" keeps the buttons exactly as tall as the box beside them.
        self.scan_btn = RoundButton(inner, text="Scan", command=self.scan_devices,
                                    width=54, height=theme.BUTTON_HEIGHT, radius=8,
                                    font=theme.FONT_SMALL,
                                    tooltip="Find Google TV devices on your network")
        self.scan_btn.pack(side="left", fill="y", padx=(0, px(theme.COL_GAP)))

        self.connect_btn = RoundButton(inner, text="Connect", command=self.toggle_connection,
                                       width=76, height=theme.BUTTON_HEIGHT, radius=8,
                                       font=theme.FONT_SMALL, fill=ACCENT, hover=ACCENT_HOVER)
        self.connect_btn.pack(side="left", fill="y")

    def _build_status(self, parent) -> None:
        row = self._section(parent)
        dot = px(11)
        self.status_dot = tk.Canvas(row, width=dot, height=dot, bg=BG, highlightthickness=0)
        self._dot = self.status_dot.create_oval(1, 1, dot - 1, dot - 1, fill=MUTED, outline="")
        self.status_dot.pack(side="left", padx=(px(2), px(6)))
        self.status_var = tk.StringVar(value="Not connected")
        self._status_label = tk.Label(row, textvariable=self.status_var, bg=BG, fg=MUTED,
                                      font=theme.FONT_SMALL, anchor="w")
        self._status_label.pack(side="left", fill="x", expand=True)

    def _build_top_row(self, parent) -> None:
        row = self._row(parent)
        self.power_btn = RoundButton(row, text="⏻", height=theme.BUTTON_HEIGHT,
                                     font=theme.FONT_ICON_BIG, fill=POWER_BG,
                                     hover=POWER_HOVER, command=self.toggle_power,
                                     tooltip="Power")
        self.input_btn = RoundButton(row, text="Input", height=theme.BUTTON_HEIGHT,
                                     font=theme.FONT_SMALL, command=self._show_input_menu,
                                     tooltip="Switch input source")
        self._buttons += [self.power_btn, self.input_btn]
        # Text beats glyphs here: the input and menu icons look almost identical,
        # and the microphone glyph is missing from Segoe UI Symbol.
        for button in (self.power_btn, self.input_btn,
                       self._key_button(row, "Search", keycodes.KEYCODE_SEARCH,
                                        font=theme.FONT_SMALL, tooltip="Search / Assistant")):
            row.add(button)

    def _build_dpad(self, parent) -> None:
        panel = tk.Frame(self._section(parent), bg=PANEL)
        panel.pack(fill="x")
        pad = tk.Frame(panel, bg=PANEL)
        pad.pack(pady=px(theme.PANEL_PAD))                 # centred in the panel

        def key(text, code, row, column, **kwargs):
            button = self._key_button(pad, text, code, width=64, **kwargs)
            button.grid(row=row, column=column, padx=px(3), pady=px(3))

        key("▲", keycodes.KEYCODE_DPAD_UP, 0, 1, font=theme.FONT_ICON, repeat=True)
        key("◀", keycodes.KEYCODE_DPAD_LEFT, 1, 0, font=theme.FONT_ICON, repeat=True)
        key("OK", keycodes.KEYCODE_DPAD_CENTER, 1, 1, fill=ACCENT, hover=ACCENT_HOVER)
        key("▶", keycodes.KEYCODE_DPAD_RIGHT, 1, 2, font=theme.FONT_ICON, repeat=True)
        key("▼", keycodes.KEYCODE_DPAD_DOWN, 2, 1, font=theme.FONT_ICON, repeat=True)

    def _build_nav_row(self, parent) -> None:
        row = self._row(parent)
        row.add(self._key_button(row, "↩", keycodes.KEYCODE_BACK, font=theme.FONT_ICON,
                                 tooltip="Back  (Backspace)"))
        row.add(self._key_button(row, "⌂", keycodes.KEYCODE_HOME, font=theme.FONT_ICON_BIG,
                                 tooltip="Home"))
        row.add(self._key_button(row, "≡", keycodes.KEYCODE_MENU, font=theme.FONT_ICON,
                                 tooltip="Menu"))

    def _build_volume_row(self, parent) -> None:
        row = self._row(parent)
        row.add(self._key_button(row, "\U0001f509", keycodes.KEYCODE_VOLUME_DOWN,
                                 font=theme.FONT_ICON, repeat=True, tooltip="Volume down  (-)"))
        self.mute_btn = self._key_button(row, "\U0001f507", keycodes.KEYCODE_VOLUME_MUTE,
                                         font=theme.FONT_ICON, tooltip="Mute  (M)")
        row.add(self.mute_btn)
        row.add(self._key_button(row, "\U0001f50a", keycodes.KEYCODE_VOLUME_UP,
                                 font=theme.FONT_ICON, repeat=True, tooltip="Volume up  (+)"))

    def _build_media_row(self, parent) -> None:
        row = self._row(parent)
        media = [
            ("⏮", keycodes.KEYCODE_MEDIA_PREVIOUS, "Previous"),
            ("⏪", keycodes.KEYCODE_MEDIA_REWIND, "Rewind"),
            ("⏯", keycodes.KEYCODE_MEDIA_PLAY_PAUSE, "Play / Pause  (Space)"),
            ("⏩", keycodes.KEYCODE_MEDIA_FAST_FORWARD, "Fast forward"),
        ]
        for glyph, code, tip in media:
            row.add(self._key_button(row, glyph, code, width=44, font=theme.FONT_ICON,
                                     tooltip=tip))

    def _build_text_row(self, parent) -> None:
        row = self._section(parent)
        self.text_var = tk.StringVar()
        self.text_entry, ipady = self._entry(row, self.text_var)
        self.text_entry.configure(width=10)              # it stretches; keep the minimum small
        self.text_entry.pack(side="left", fill="x", expand=True, ipady=ipady,
                             padx=(0, px(theme.COL_GAP)))
        self.text_entry.bind("<Return>", lambda _e: self.send_text())
        Placeholder(self.text_entry, self.text_var, "Text for the TV")
        tools = [
            ("⌫", self.backspace, "Delete the last character on the TV"),
            ("✕", self.clear_text, "Clear the text field on the TV"),
            ("➤", self.send_text, "Type this text on the TV  (Enter)"),
        ]
        for index, (glyph, command, tip) in enumerate(tools):
            last = index == len(tools) - 1
            button = RoundButton(row, text=glyph, command=command, width=40,
                                 height=theme.BUTTON_HEIGHT, radius=8,
                                 font=theme.FONT_ICON, tooltip=tip)
            button.pack(side="left", fill="y", padx=(0, 0 if last else px(theme.COL_GAP)))
            self._buttons.append(button)

    def _build_apps(self, parent) -> None:
        section = self._section(parent, gap=theme.ROW_GAP + 4)
        tk.Label(section, text="APPS", bg=BG, fg=MUTED, font=theme.FONT_SMALL,
                 anchor="w").pack(fill="x", pady=(0, px(4)))
        apps = config.app_shortcuts(self.settings)
        # Icon-only, dock style; the name is in the tooltip.
        for index, chunk in enumerate(balanced_rows(apps, MAX_APPS_PER_ROW)):
            row = self._row(section, gap=theme.ROW_GAP if index else 0)
            for name, link in chunk:
                button = RoundButton(row, icon=app_icon(name), width=44, icon_size=30,
                                     height=theme.BUTTON_HEIGHT, tooltip=name,
                                     command=lambda l=link, n=name: self.launch_app(l, n))
                self._buttons.append(row.add(button))

    def _build_footer(self, parent) -> None:
        row = tk.Frame(parent, bg=BG)
        row.pack(fill="x", side="bottom", pady=(px(4), 0))
        self._footer_link(row, "Re-pair", self.repair,
                          "Pair again if the TV has forgotten this remote")
        self._footer_link(row, "Shortcuts", self._show_shortcuts,
                          "Keyboard shortcuts", pad=(0, px(12)))

        self.auto_var = tk.BooleanVar(value=bool(self.settings.get("auto_connect", True)))
        auto = tk.Checkbutton(
            row, text="Auto-connect", variable=self.auto_var,
            command=self._toggle_auto_connect, bg=BG, fg=MUTED, font=theme.FONT_SMALL,
            selectcolor=BTN, activebackground=BG, activeforeground=TEXT,
            highlightthickness=0, bd=0, cursor="hand2")
        auto.pack(side="left")
        Tooltip(auto, "Connect to the last TV automatically at startup")

        # What the TV reports: foreground app (or power state) and volume.
        info = tk.Frame(parent, bg=BG)
        info.pack(fill="x", side="bottom", pady=(px(theme.ROW_GAP), 0))
        # The volume group sits on the right: a speaker glyph, a level bar and
        # the number. Pack it first so the expanding app label cannot cover it.
        self._volbox = tk.Frame(info, bg=BG)
        self._volbox.pack(side="right", padx=(px(8), 0))
        self.volume_var = tk.StringVar(value="")
        self._vol_speaker = tk.Label(self._volbox, text="", bg=BG, fg=MUTED,
                                     font=theme.FONT_ICON_SMALL)
        self._vol_speaker.pack(side="left")
        self._vol_meter = VolumeMeter(self._volbox)
        self._vol_meter.pack(side="left", padx=(px(5), px(5)))
        self._volume_label = tk.Label(self._volbox, textvariable=self.volume_var, bg=BG,
                                      fg=MUTED, font=theme.FONT_SMALL, anchor="e")
        self._volume_label.pack(side="left")
        self.app_var = tk.StringVar(value="")
        tk.Label(info, textvariable=self.app_var, bg=BG, fg=MUTED,
                 font=theme.FONT_SMALL, anchor="w").pack(side="left", fill="x", expand=True)
        self._info = info
        self._small_font = tkfont.Font(font=theme.FONT_SMALL)
        info.bind("<Configure>", lambda _e: self._refresh_app_label())
        self._vol_meter.clear()

    def _footer_link(self, parent, text, command, tip: str, pad=(0, 0)) -> tk.Label:
        link = tk.Label(parent, text=text, bg=BG, fg=MUTED, font=theme.FONT_SMALL,
                        cursor="hand2")
        link.pack(side="right", padx=pad)
        link.bind("<Button-1>", lambda _e: command())
        link.bind("<Enter>", lambda _e: link.configure(fg=TEXT))
        link.bind("<Leave>", lambda _e: link.configure(fg=MUTED))
        if tip:
            Tooltip(link, tip)
        return link

    def _set_app_text(self, text: str) -> None:
        self._app_text = text
        self._refresh_app_label()

    def _update_volume(self, level: int, maximum: int, muted: bool) -> None:
        """Reflect the TV's volume in the number, the bar and the mute button."""
        self._volume = (level, maximum, muted)
        self._volume_known = True
        if muted:
            self.volume_var.set("muted")
        elif maximum:
            self.volume_var.set(f"vol {level}/{maximum}")
        else:
            self.volume_var.set(f"vol {level}")
        self._volume_label.configure(fg=MUTED if muted else TEXT)
        self._vol_speaker.configure(text=self._speaker_glyph(level, maximum, muted),
                                    fg=MUTED if muted else TEXT)
        self._vol_meter.set(level, maximum, muted)
        self.mute_btn.set_active(muted, theme.TOGGLE, theme.TOGGLE_HOVER)
        self._refresh_app_label()                   # the readout changed width

    def _clear_volume(self) -> None:
        self._volume = (0, 0, False)
        self._volume_known = False
        self.volume_var.set("")
        self._vol_speaker.configure(text="")
        self._vol_meter.clear()
        self.mute_btn.set_active(False)
        self._refresh_app_label()

    @staticmethod
    def _speaker_glyph(level: int, maximum: int, muted: bool) -> str:
        if muted:
            return "\U0001f507"                     # muted speaker
        ratio = (level / maximum) if maximum else 0.0
        if ratio <= 0:
            return "\U0001f508"                     # speaker, no waves
        if ratio < 0.5:
            return "\U0001f509"                     # one wave
        return "\U0001f50a"                         # full

    def _refresh_app_label(self) -> None:
        """Show as much of the app text as fits beside the volume readout."""
        text = self._app_text
        try:
            available = (self._info.winfo_width() - self._volbox.winfo_reqwidth()
                         - px(8))
            if available > px(40):                      # laid out: measure for real
                text = fit_text(self._small_font, text, available)
            elif len(text) > MAX_APP_LABEL:             # not mapped yet: rough cut
                text = text[:MAX_APP_LABEL - 1] + "…"
            self.app_var.set(text)
        except tk.TclError:
            pass                                        # <Configure> during teardown

    # ---------------------------------------------------------- keyboard --

    def _bind_keys(self) -> None:
        mapping = {
            "<Up>": keycodes.KEYCODE_DPAD_UP,
            "<Down>": keycodes.KEYCODE_DPAD_DOWN,
            "<Left>": keycodes.KEYCODE_DPAD_LEFT,
            "<Right>": keycodes.KEYCODE_DPAD_RIGHT,
            "<Return>": keycodes.KEYCODE_DPAD_CENTER,
            "<BackSpace>": keycodes.KEYCODE_BACK,
            "<Escape>": keycodes.KEYCODE_BACK,
            "<space>": keycodes.KEYCODE_MEDIA_PLAY_PAUSE,
            "<Home>": keycodes.KEYCODE_HOME,
            "<plus>": keycodes.KEYCODE_VOLUME_UP,
            "<equal>": keycodes.KEYCODE_VOLUME_UP,
            "<minus>": keycodes.KEYCODE_VOLUME_DOWN,
            "<m>": keycodes.KEYCODE_VOLUME_MUTE,
        }
        for sequence, code in mapping.items():
            self.root.bind(sequence, lambda event, c=code: self._hotkey(c, event))
        # Everything else: live typing into a text box focused on the TV.
        self.root.bind("<Key>", self._on_key)

        # Ctrl +/- resizes the whole interface; Ctrl+0 restores the default.
        for sequence in ("<Control-plus>", "<Control-equal>", "<Control-KP_Add>"):
            self.root.bind(sequence, lambda _e: self.set_scale(theme.SCALE + theme.SCALE_STEP))
        for sequence in ("<Control-minus>", "<Control-KP_Subtract>"):
            self.root.bind(sequence, lambda _e: self.set_scale(theme.SCALE - theme.SCALE_STEP))
        self.root.bind("<Control-Key-0>", lambda _e: self.set_scale(theme.DEFAULT_SCALE))

    def _typing_in_app(self) -> bool:
        """True while one of the window's own text boxes has keyboard focus."""
        return self.root.focus_get() in (self.text_entry, self.host_entry)

    def _tv_typing(self) -> bool:
        """True while the TV reports a focused text box: keystrokes go to it."""
        return self._is_connected() and self.client.text_field is not None

    def _hotkey(self, key_code: int, event):
        if self._typing_in_app():
            return None
        if self._tv_typing():
            if event.keysym == "BackSpace":
                self._edit_tv_text(self.client.delete_text, "Deleted a character on the TV")
                return "break"
            if event.keysym == "Return":
                self.send_key(keycodes.KEYCODE_ENTER)           # submit the search
                return "break"
            if len(event.char) == 1 and event.char.isprintable():
                self._type_char(event.char)                     # space, +, -, m ...
                return "break"
        self.send_key(key_code)
        return "break"

    def _on_key(self, event):
        """Live typing: while the TV has a text box focused, printable
        keystrokes go straight to it."""
        if self._typing_in_app() or not self._tv_typing():
            return None
        if len(event.char) != 1 or not event.char.isprintable():
            return None
        self._type_char(event.char)
        return "break"

    def _type_char(self, char: str) -> None:
        self._edit_tv_text(lambda: self.client.send_text(char), f"Typed {char!r} on the TV")

    # ----------------------------------------------------------- actions --

    def _is_connected(self) -> bool:
        return bool(self.client and self.client.is_connected)

    def _set_controls_enabled(self, enabled: bool) -> None:
        for button in self._buttons:
            button.set_enabled(enabled)

    def _show_connected(self, connected: bool) -> None:
        """Enable the remote buttons and flip the Connect/Disconnect button."""
        self._set_controls_enabled(connected)
        if connected:
            self.connect_btn.set_text("Disconnect")
            self.connect_btn.set_fill(BTN, BTN_HOVER)
        else:
            self.connect_btn.set_text("Connect")
            self.connect_btn.set_fill(ACCENT, ACCENT_HOVER)

    def _toggle_auto_connect(self) -> None:
        self.settings["auto_connect"] = bool(self.auto_var.get())
        config.save(self.settings)

    def _auto_connect(self) -> None:
        """Connect to the last used TV at startup."""
        if self.client is None and self.host_var.get().strip():
            self.connect()

    def toggle_connection(self) -> None:
        if self._is_connected():
            self.disconnect()
        else:
            self.connect()

    def connect(self) -> None:
        host = self.host_var.get().strip()
        if not host:
            messagebox.showinfo(APP_TITLE, "Enter your TV's IP address, or press Scan.")
            return
        self.disconnect(update_ui=False)
        self._set_status("connecting", f"Connecting to {host}...")
        self.client = RemoteClient(
            host,
            on_state=lambda status, detail: self._post(("state", status, detail)),
            on_volume=lambda level, maximum, muted: self._post(("volume", level, maximum, muted)),
            on_power=lambda on: self._post(("power", on)),
            on_app=lambda package: self._post(("app", package)),
            on_field=lambda label: self._post(("field", label)),
        )
        self.client.start()
        self.settings["last_host"] = host
        config.save(self.settings)
        self.root.focus_set()           # take focus off the IP box so hotkeys work

    def disconnect(self, update_ui: bool = True) -> None:
        if self.client:
            self.client.stop()
            self.client = None
        if update_ui:
            self._set_status("disconnected", "Disconnected")
            self._show_connected(False)
            self._clear_volume()
            self._set_app_text("")

    def send_key(self, key_code: int, button: RoundButton | None = None) -> None:
        if not self._is_connected():
            self._set_status("error", "Not connected.")
            return
        try:
            self.client.send_key(key_code)
        except OSError as exc:
            self._set_status("error", f"Send failed: {exc}")
            return
        if button:
            button.flash()

    def toggle_power(self) -> None:
        self.send_key(keycodes.KEYCODE_POWER, self.power_btn)

    def _show_input_menu(self) -> None:
        """Offer the inputs this TV can switch to (see gtvremote.inputs)."""
        if not self._is_connected():
            self._set_status("error", "Not connected.")
            return
        menu = tk.Menu(self.root, tearoff=0, bg=PANEL, fg=TEXT, activebackground=ACCENT,
                       activeforeground=TEXT, bd=0, relief="flat", font=theme.FONT_SMALL)
        for label, (kind, target) in inputs.input_sources(self.client.tv_vendor, self.settings):
            if kind == "key":
                command = lambda c=target: self.send_key(c, self.input_btn)
            else:
                command = lambda l=target, n=label: self.switch_input(l, n)
            menu.add_command(label=label, command=command)
        button = self.input_btn
        try:
            menu.tk_popup(button.winfo_rootx(), button.winfo_rooty() + button.winfo_height())
        finally:
            menu.grab_release()

    def switch_input(self, link: str, name: str) -> None:
        if not self._is_connected():
            self._set_status("error", "Not connected.")
            return
        try:
            self.client.launch_app(link)
        except OSError as exc:
            self._set_status("error", f"Could not switch to {name}: {exc}")
            return
        self.input_btn.flash()
        self._set_status("connected", f"Switching to {name}...")

    def launch_app(self, link: str, name: str) -> None:
        if not self._is_connected():
            self._set_status("error", "Not connected.")
            return
        try:
            self.client.launch_app(link)
        except OSError as exc:
            self._set_status("error", f"Could not open {name}: {exc}")
            return
        self._set_status("connected", f"Opening {name}...")

    def send_text(self) -> None:
        text = self.text_var.get()
        if not text or not self._is_connected():
            return
        try:
            self.client.send_text(text)
        except ValueError as exc:                       # no field focused on the TV
            self._set_status("error", str(exc))
            return
        except OSError as exc:
            self._set_status("error", f"Typing failed: {exc}")
            return
        self._set_status("connected", "Text sent to the TV")
        self.text_var.set("")

    def backspace(self) -> None:
        if self._is_connected():
            self._edit_tv_text(self.client.delete_text, "Deleted the last character")
        else:
            self._set_status("error", "Not connected.")

    def clear_text(self) -> None:
        if self._is_connected():
            self._edit_tv_text(self.client.clear_text, "Cleared the text field")
        else:
            self._set_status("error", "Not connected.")

    def _edit_tv_text(self, action, done: str) -> None:
        try:
            action()
        except ValueError as exc:                       # no field focused / not accepted
            self._set_status("error", str(exc))
            return
        except OSError as exc:
            self._set_status("error", f"Edit failed: {exc}")
            return
        self._set_status("connected", done)

    def scan_devices(self) -> None:
        self._set_status("connecting", "Scanning the network...")
        self.scan_btn.set_enabled(False)

        def worker():
            try:
                devices = discovery.discover()
            except OSError:
                devices = []
            self._post(("devices", devices))

        threading.Thread(target=worker, daemon=True).start()

    def _show_devices(self, devices: list[discovery.Device]) -> None:
        self.scan_btn.set_enabled(True)
        if not devices:
            self._set_status("error", "No devices found - enter the IP manually.")
            messagebox.showinfo(
                APP_TITLE,
                "No Google TV devices answered.\n\n"
                "Check that the TV is on and on the same network, then try again.\n"
                "You can also type its IP address directly "
                "(Settings > Network > see the TV's IP).")
            return
        if len(devices) == 1:
            self.host_var.set(devices[0].host)
            self._set_status("disconnected", "Found " + devices[0].label)
            self.connect()
            return
        DevicePicker(self.root, devices, self._on_device_chosen)
        self._set_status("disconnected", f"Found {len(devices)} devices")

    def _on_device_chosen(self, device: discovery.Device) -> None:
        self.host_var.set(device.host)
        self.connect()

    # ----------------------------------------------------------- pairing --

    def repair(self, confirm: bool = True) -> None:
        """Start pairing with the TV in the host box."""
        host = self.host_var.get().strip()
        if not host:
            messagebox.showinfo(APP_TITLE, "Enter the TV's IP address first.")
            return
        if confirm and not messagebox.askyesno(
                APP_TITLE, f"Pair with {host}?\n\nA 6-digit code will appear on the TV screen."):
            return
        self.disconnect()
        threading.Thread(target=self._pair_worker, args=(host,), daemon=True).start()

    def _pair_worker(self, host: str) -> None:
        self._post(("status", "connecting", f"Starting pairing with {host}..."))
        session = PairingSession(host, client_name=self.settings["client_name"])
        try:
            session.begin()
        except PairingError as exc:
            session.close()
            self._post(("pair_failed", str(exc)))
            return
        except OSError as exc:
            session.close()
            self._post(("pair_failed", f"Could not reach {host} for pairing: {exc}"))
            return
        self._post(("pair_code", session, host))

    def _prompt_code(self, session: PairingSession, host: str) -> None:
        code = simpledialog.askstring(APP_TITLE, "Enter the 6-digit code shown on the TV:",
                                      parent=self.root)
        if not code:
            session.close()
            self._set_status("disconnected", "Pairing cancelled")
            return

        def worker():
            try:
                name = session.send_code(code)
            except (PairingError, OSError) as exc:
                self._post(("pair_failed", str(exc)))
            else:
                self._post(("pair_ok", host, name))
            finally:
                session.close()

        self._set_status("connecting", "Verifying code...")
        threading.Thread(target=worker, daemon=True).start()

    def _offer_pairing(self) -> None:
        host = self.host_var.get().strip()
        if host and messagebox.askyesno(
                APP_TITLE, "This TV does not recognise this remote yet.\n\nPair with it now?"):
            self.repair(confirm=False)

    def _show_shortcuts(self) -> None:
        """A small, themed cheat-sheet of the keyboard controls."""
        existing = self._help_win
        if existing is not None and existing.winfo_exists():
            existing.lift()
            existing.focus_set()
            return

        win = tk.Toplevel(self.root, bg=BG)
        self._help_win = win
        win.title("Keyboard shortcuts")
        win.transient(self.root)
        win.resizable(False, False)
        win.bind("<Escape>", lambda _e: win.destroy())

        body = tk.Frame(win, bg=BG)
        body.pack(fill="both", expand=True, padx=px(18), pady=px(16))
        tk.Label(body, text="Keyboard control", bg=BG, fg=TEXT,
                 font=(theme.UI_FONT, theme.FONT[1], "bold")).grid(
                     row=0, column=0, columnspan=2, sticky="w")
        tk.Label(body, text="Keep this window focused - the mouse is optional.",
                 bg=BG, fg=MUTED, font=theme.FONT_SMALL).grid(
                     row=1, column=0, columnspan=2, sticky="w", pady=(0, px(10)))
        for index, (keys, desc) in enumerate(SHORTCUTS):
            tk.Label(body, text=keys, bg=BTN, fg=TEXT, font=theme.FONT_SMALL,
                     padx=px(9), pady=px(4)).grid(
                         row=index + 2, column=0, sticky="w", padx=(0, px(12)), pady=px(3))
            tk.Label(body, text=desc, bg=BG, fg=MUTED, font=theme.FONT_SMALL,
                     anchor="w").grid(row=index + 2, column=1, sticky="w", pady=px(3))

        win.update_idletasks()
        root_x, root_y = self.root.winfo_rootx(), self.root.winfo_rooty()
        offset_x = (self.root.winfo_width() - win.winfo_width()) // 2
        win.geometry(f"+{root_x + max(0, offset_x)}+{root_y + px(40)}")
        win.focus_set()

    # -------------------------------------------------------- event pump --

    def _post(self, event: tuple) -> None:
        """Called from worker threads; hands the event to the UI thread."""
        self.events.put(event)

    def _drain_events(self) -> None:
        try:
            while True:
                self._handle_event(self.events.get_nowait())
        except queue.Empty:
            pass
        self._pump_job = self.root.after(EVENT_POLL_MS, self._drain_events)

    def _handle_event(self, event: tuple) -> None:
        kind = event[0]
        if kind in ("state", "status"):
            _, status, detail = event
            self._set_status(status, detail)
            if status == "connected":
                self._show_connected(True)
            elif status in ("error", "disconnected", "reconnecting", "unpaired"):
                self._show_connected(False)
            if status == "unpaired":
                self._offer_pairing()
        elif kind == "volume":
            _, level, maximum, muted = event
            self._update_volume(level, maximum, muted)
        elif kind == "power":
            self._set_app_text("TV is on" if event[1] else "TV is in standby")
        elif kind == "app":
            self._set_app_text(friendly_app_name(event[1]))
        elif kind == "field":
            if event[1] is not None:
                self._set_status("connected", "TV text box active - just type; Enter submits")
            elif self._last_status == "connected" and self.client:
                self._set_status("connected", f"Connected to {self.client.host}")
        elif kind == "devices":
            self._show_devices(event[1])
        elif kind == "pair_code":
            self._prompt_code(event[1], event[2])
        elif kind == "pair_ok":
            _, host, name = event
            config.remember_device(self.settings, host, name)
            config.save(self.settings)
            messagebox.showinfo(APP_TITLE, f"Paired with {name or host}.")
            self.host_var.set(host)
            self.connect()
        elif kind == "pair_failed":
            self._set_status("error", event[1])
            messagebox.showerror(APP_TITLE, event[1])

    def _set_status(self, status: str, detail: str) -> None:
        self._last_status = status
        self.status_dot.itemconfig(self._dot, fill=theme.STATUS_COLORS.get(status, MUTED))
        self._status_label.configure(fg=theme.STATUS_TEXT_COLORS.get(status, MUTED))
        self.status_var.set(detail)

    def on_close(self) -> None:
        """Remember the window position, stop the client and close the window."""
        try:
            self.settings["window_geometry"] = self.root.geometry()
            config.save(self.settings)
        finally:
            if self._pump_job is not None:
                self.root.after_cancel(self._pump_job)
                self._pump_job = None
            self.disconnect(update_ui=False)
            self.root.destroy()


class DevicePicker(tk.Toplevel):
    """Modal list shown when discovery finds more than one TV."""

    def __init__(self, parent, devices: list[discovery.Device], on_choose):
        super().__init__(parent)
        self.title("Choose a device")
        self.configure(bg=BG)
        self.resizable(False, False)
        self.transient(parent)
        self.grab_set()
        self._on_choose = on_choose
        self._devices = devices

        tk.Label(self, text="Google TV devices found", bg=BG, fg=TEXT,
                 font=theme.FONT).pack(padx=16, pady=(14, 8), anchor="w")
        listbox = tk.Listbox(self, bg=PANEL, fg=TEXT, relief="flat", font=theme.FONT,
                             selectbackground=ACCENT, highlightthickness=0,
                             activestyle="none", height=min(len(devices), 8), width=38)
        for device in devices:
            listbox.insert("end", "  " + device.label)
        listbox.selection_set(0)
        listbox.pack(padx=16, fill="x")
        listbox.bind("<Double-Button-1>", lambda _e: self._choose())
        self._listbox = listbox

        row = tk.Frame(self, bg=BG)
        row.pack(padx=16, pady=14, fill="x")
        RoundButton(row, text="Connect", command=self._choose, width=90,
                    height=theme.SMALL_BUTTON_HEIGHT, radius=8, font=theme.FONT_SMALL,
                    fill=ACCENT, hover=ACCENT_HOVER).pack(side="right")
        RoundButton(row, text="Cancel", command=self.destroy, width=76,
                    height=theme.SMALL_BUTTON_HEIGHT, radius=8,
                    font=theme.FONT_SMALL).pack(side="right", padx=(0, 8))

    def _choose(self):
        selection = self._listbox.curselection()
        if not selection:
            return
        device = self._devices[selection[0]]
        self.destroy()
        self._on_choose(device)


def log_to_file() -> None:
    """Keep a small connection log next to the settings, so a dropped link
    can be explained after the fact (see README, Troubleshooting)."""
    try:
        handler = RotatingFileHandler(config.log_path(), maxBytes=LOG_MAX_BYTES,
                                      backupCount=1, encoding="utf-8")
    except OSError:
        return
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    logger = logging.getLogger("gtvremote")
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    logger.info("%s %s starting", APP_TITLE, __version__)


def main() -> None:
    """Open the remote window and run until it is closed."""
    log_to_file()
    if sys.platform == "win32":
        # Must run before the first Tk window exists. Set afterwards, Windows
        # has already sized the window and then scales it, shrinking the UI.
        try:
            from ctypes import windll
            windll.shcore.SetProcessDpiAwareness(1)          # crisp text on HiDPI
        except (ImportError, AttributeError, OSError):
            pass
    root = tk.Tk()
    app = RemoteApp(root)
    app.root.mainloop()
