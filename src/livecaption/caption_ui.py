#!/usr/bin/env python3
"""
Live Caption Generator — UI layer.

Owns the Tk window, text widgets, drag/scroll/right-click-menu handling,
and the persistent config file. Talks to the backend (live_caption.py) only
through:
  - submit_final(text) / submit_partial(text) / submit_translated(src, text):
    thread-safe, called from background threads to push new captions in.
  - clear_captions(), set_status(text), set_translation_enabled(flag): thread-safe.
  - on_settings_change(changes): called when the user changes a backend-owned
    setting (menu or Settings window). May raise to reject the change.
  - backend_values(): returns the backend's current settings (for the form).
  - normalize_patch(patch): lets the backend tidy what gets written to the
    config file (None means "remove this key").
  - on_pause_toggle(paused) / on_shutdown().

System tray (caption_tray.py, StatusNotifierItem over DBus): optional. Its menu
mirrors the right-click menu; clicks arrive on a queue drained by the Tk loop.

Settings: the constants below are the DEFAULTS. Anything changed through the
menu or the Settings window is saved to the JSON config file next to the
scripts and overrides them; "Reset to defaults" removes the override.
"""

import json
import queue
import sys
import threading
import tkinter as tk
from tkinter import font as tkfont

from .settings_ui import F, SettingsWindow


# ==================== DEFAULTS (also editable from the Settings window) ====================
# Base window size (pixels).
# WIDTH: scaled by WINDOW_SIZE_PERCENT below.
# HEIGHT: None = always auto-fit the window height to exactly what Tk says it
#   needs to show CAPTION_LINES lines (+ the partial row, if SHOW_PARTIAL is on)
#   without clipping — recommended. Set a number for extra breathing room above
#   that floor; it can never go below the floor, only above it.
BASE_WIDTH = 1000
BASE_HEIGHT = None

# Scale the window size (100 = base size, 150 = 1.5x, 50 = half size)
WINDOW_SIZE_PERCENT = 100

# Window opacity: 0.1 (very transparent) to 1.0 (fully opaque)
WINDOW_OPACITY = 0.77

# Colors
BG_COLOR = "#1a1a1a"
FG_COLOR = "#ffffff"

# Font
FONT_FAMILY = "Cantarell"  # falls back to Cantarell / DejaVu Sans if unavailable
FONT_SIZE = 12
FONT_WEIGHT = "bold"  # "normal" or "bold"

# Number of caption lines visible in the scrollback text widget.
CAPTION_LINES = 3

# If False, hides the bottom row showing the live/in-progress transcription
# (the text still being recognized, before it's finalized and translated).
SHOW_PARTIAL = False

# Scrollback buffer limit (lines). Older lines are trimmed automatically.
MAX_BUFFER_LINES = 500

# If True, hides the source-text pane. Only translated text appears.
SHOW_ONLY_TRANSLATION = True

# -------------------- LINE-FILL MERGING --------------------
# Short ASR fragments (1-3 words) would otherwise each claim their own
# line. Instead, a new finalized chunk is appended onto the CURRENT last
# visual row (joined by LINE_FILL_SEPARATOR) as long as that row hasn't yet
# filled this fraction of the widget's actual pixel width; only once a
# row is "full enough" does the next chunk start a fresh line.
#
# Only applied in translation-only (single-pane) mode. In dual-pane mode,
# source/translation entries are kept strictly 1-line-per-entry on both
# sides so the two panes stay row-aligned.
LINE_FILL_THRESHOLD = 1
LINE_FILL_SEPARATOR = ".."   # or e.g. "|"

_FALLBACK_FONTS = ["Cantarell", "DejaVu Sans"]

DEFAULTS = dict(
    font_family=FONT_FAMILY, font_size=int(FONT_SIZE), font_weight=FONT_WEIGHT,
    bg_color=BG_COLOR, fg_color=FG_COLOR, window_opacity=float(WINDOW_OPACITY),
    base_width=int(BASE_WIDTH), base_height=int(BASE_HEIGHT or 0),
    window_size_percent=int(WINDOW_SIZE_PERCENT), caption_lines=int(CAPTION_LINES),
    show_only_translation=bool(SHOW_ONLY_TRANSLATION), show_partial=bool(SHOW_PARTIAL),
    line_fill_threshold=float(LINE_FILL_THRESHOLD), line_fill_separator=str(LINE_FILL_SEPARATOR),
    max_buffer_lines=int(MAX_BUFFER_LINES), ui_scale=0.0,
)


def _coerce(key, value):
    """Config files get hand-edited; fall back to the default on a bad value."""
    default = DEFAULTS[key]
    try:
        if isinstance(default, bool):
            return bool(value)
        if isinstance(default, int):
            return int(value)
        if isinstance(default, float):
            return float(value)
        return str(value)
    except (TypeError, ValueError):
        return default


def _ui_tabs():
    """Settings-window tabs for the options this module owns."""
    tabs = [
        {"title": "Appearance", "fields": [
            F("font_family", "Font", "font"),
            F("font_size", "Font size", "int", min=6, max=72),
            F("font_weight", "Font weight", "choice", choices=["normal", "bold"]),
            F("bg_color", "Background", "color"),
            F("fg_color", "Text colour", "color"),
            F("window_opacity", "Opacity", "scale", min=0.2, max=1.0, step=0.01),
            F("base_width", "Width (px)", "int", min=300, max=7680),
            F("base_height", "Height (px)", "int", min=0, max=4000,
              help="0 = fit automatically to the number of lines"),
            F("window_size_percent", "Scale (%)", "int", min=50, max=300),
            F("caption_lines", "Visible lines", "int", min=1, max=20),
            F("ui_scale", "Settings window scale", "float", min=0, max=4, step=0.05,
              help="0 = automatic (follows the display scale). Ctrl and + / - / 0 "
                   "or Ctrl+wheel zoom this window."),
        ]},
        {"title": "Display", "fields": [
            F("show_only_translation", "Translation only", "bool",
              help="Off = show the recognised text beside the translation."),
            F("show_partial", "Live transcription row", "bool",
              help="The in-progress text shown under the captions."),
            F("line_fill_threshold", "Merge short captions", "scale", min=0, max=1, step=0.05,
              help="Short captions are joined onto the same line until it is this full. "
                   "0 = every caption starts a new line."),
            F("line_fill_separator", "Separator", "text",
              help="Placed between captions merged onto one line."),
            F("max_buffer_lines", "Scrollback (lines)", "int", min=20, max=5000),
        ]},
    ]
    for tab in tabs:
        for field in tab["fields"]:
            field.setdefault("default", DEFAULTS[field["key"]])
    return tabs


class CaptionWindow:
    def __init__(self, preset_names=None, initial_preset=None,
                 language_pair_names=None, initial_language_pair=None,
                 config_path=None, on_shutdown=None, on_pause_toggle=None,
                 settings_tabs=None, backend_values=None,
                 on_settings_change=None, normalize_patch=None, backend_choices=None):
        self.preset_names = list(preset_names or [])
        self.language_pair_names = list(language_pair_names or [])
        self.config_path = config_path
        self.on_shutdown = on_shutdown
        self.on_pause_toggle = on_pause_toggle
        self.settings_tabs = list(settings_tabs or [])
        self.backend_values = backend_values
        self.on_settings_change = on_settings_change
        self.normalize_patch = normalize_patch
        self.backend_choices = backend_choices   # -> {"language_pair": [names]} (can change at runtime)

        self.audio_paused = False
        # Plain-attribute mirror of menu state for the tray's DBus thread, which
        # must never touch Tk (see _tray_menu_spec).
        self.current_preset = initial_preset
        self.current_language_pair = initial_language_pair
        self.tray_queue = queue.Queue()
        self._tray = None
        self._tray_hidden = False
        self._tray_restore_geom = None
        self.text_queue = queue.Queue()
        self.caption_entries = []   # {"source": str, "translated": str|None}
        self.current_partial = ""
        self.translation_enabled = True
        self.running = True
        self._pos = None            # window x, y once placed (kept across re-layouts)
        self._shown = False
        self._settings_win = None
        self._ui_tabs = _ui_tabs()

        # Live settings = defaults overlaid with whatever the config file holds.
        config = self._load_config()
        self.settings = dict(DEFAULTS)
        for key in DEFAULTS:
            if key in config:
                self.settings[key] = _coerce(key, config[key])
        self._last_geom_w = self.settings["base_width"]

        self._build_ui(initial_preset, initial_language_pair)
        self._apply_pane_layout()
        self.root.after(100, self._update_caption)

    @property
    def show_only_translation(self):
        return self.settings["show_only_translation"]

    # -------------------- Thread-safe inbound API --------------------
    def submit_final(self, text):
        self.text_queue.put(("final", text))

    def submit_partial(self, text):
        self.text_queue.put(("partial", text))

    def submit_translated(self, source_text, translated_text):
        self.text_queue.put(("translated", (source_text, translated_text)))

    def clear_captions(self):
        self.text_queue.put(("clear", None))

    def set_translation_enabled(self, enabled):
        """False = no translator is active, so translation-only mode shows the
        recognised text instead of an empty window."""
        self.text_queue.put(("translation_mode", bool(enabled)))

    def set_status(self, text):
        """Show a status message in the text widgets."""
        if threading.current_thread() is threading.main_thread():
            self._set_status_now(text)
        else:
            self.text_queue.put(("status", text))

    # -------------------- Config persistence --------------------
    def _load_config(self):
        if not self.config_path:
            return {}
        try:
            with open(self.config_path) as f:
                return json.load(f)
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            return {}

    def _save_config_patch(self, patch):
        """Merge `patch` into the config file (a None value removes that key)."""
        if not self.config_path:
            return
        if self.normalize_patch:
            patch = self.normalize_patch(dict(patch))
        data = self._load_config()
        for key, value in patch.items():
            if value is None:
                data.pop(key, None)
            else:
                data[key] = value
        try:
            with open(self.config_path, "w") as f:
                json.dump(data, f, indent=2)
        except Exception as e:
            print(f"[Config] Failed to save {self.config_path}: {e}")

    # -------------------- UI construction --------------------
    def _build_ui(self, initial_preset, initial_language_pair):
        s = self.settings
        self.root = tk.Tk()
        self.root.title("Live Captions")
        self.root.withdraw()

        # Frameless, always on top
        self.root.overrideredirect(True)
        self.root.attributes("-topmost", True)

        self.caption_font = self._make_font(weight=s["font_weight"])
        self.partial_font = self._make_font(weight="normal", slant="italic")

        # Main container
        main_frame = tk.Frame(self.root, bg=s["bg_color"])
        main_frame.pack(expand=True, fill="both")
        self.main_frame = main_frame

        # ---- Scrollable text area ----
        text_frame = tk.Frame(main_frame, bg=s["bg_color"])
        text_frame.pack(expand=True, fill="both", padx=10, pady=(10, 0))
        self.text_frame = text_frame

        text_kwargs = dict(
            font=self.caption_font, bg=s["bg_color"], fg=s["fg_color"], wrap=tk.WORD,
            state=tk.DISABLED, padx=5, pady=5, height=s["caption_lines"],
            spacing1=2, spacing3=2, highlightthickness=0, borderwidth=0,
        )
        self.left_text = tk.Text(text_frame, **text_kwargs)
        self.right_text = tk.Text(text_frame, **text_kwargs)
        self.scrollbar = tk.Scrollbar(text_frame, command=self._on_scrollbar)

        # ---- Live partial indicator (bottom). The widgets always exist; whether
        # the row is shown is decided in _apply_pane_layout(), so it can be
        # toggled at runtime.
        self.partial_frame = tk.Frame(main_frame, bg=s["bg_color"])
        self.partial_left = tk.Label(
            self.partial_frame, text="", font=self.partial_font,
            bg=s["bg_color"], fg="#888888", anchor="w", justify="left",
        )
        self.partial_right = tk.Label(
            self.partial_frame, text="", font=self.partial_font,
            bg=s["bg_color"], fg="#999999", anchor="w", justify="left",
        )

        # Mousewheel scroll — bound unconditionally; harmless on hidden widgets.
        for w in (self.left_text, self.right_text, text_frame):
            w.bind("<MouseWheel>", self._on_mousewheel)
            w.bind("<Button-4>", self._on_mousewheel)
            w.bind("<Button-5>", self._on_mousewheel)

        # Drag to move
        for w in (self.root, main_frame, self.partial_frame):
            w.bind("<Button-1>", self._start_drag)
            w.bind("<B1-Motion>", self._on_drag)

        # Right-click menu
        for w in (self.root, main_frame, text_frame, self.left_text, self.right_text,
                  self.partial_frame, self.partial_left, self.partial_right):
            w.bind("<Button-3>", self._show_menu)

        self._build_menu(initial_preset, initial_language_pair)
        self._setup_tray()

        # Keyboard shortcut
        self.root.bind("<Escape>", lambda e: self.shutdown())

    def _resolve_family(self, name):
        families = set(tkfont.families(self.root))
        for family in [name] + _FALLBACK_FONTS:
            if family in families:
                return family
        return tkfont.nametofont("TkDefaultFont").actual("family")

    def _make_font(self, **kwargs):
        s = self.settings
        return tkfont.Font(family=self._resolve_family(s["font_family"]),
                           size=s["font_size"], **kwargs)

    def _build_menu(self, initial_preset, initial_language_pair):
        self.menu = tk.Menu(
            self.root, tearoff=0, bg="#2d2d2d", fg="#ffffff",
            activebackground="#444444", activeforeground="#ffffff",
        )
        # ---- Pause / Resume audio feed ----
        # "Deaf mode": capture keeps running and the model stays loaded, but
        # incoming audio is discarded so unrelated playback makes no captions.
        self.menu.add_command(label="Pause Audio", command=self._toggle_pause)
        self._pause_menu_index = self.menu.index("end")

        self.menu.add_separator()
        self.menu.add_command(label="Increase Font", command=self._increase_font)
        self.menu.add_command(label="Decrease Font", command=self._decrease_font)

        # ---- Display Mode ----
        self.menu.add_separator()
        self._display_mode_var = tk.StringVar(
            value="translation_only" if self.show_only_translation else "with_transcription"
        )
        display_menu = self._submenu()
        display_menu.add_radiobutton(
            label="Translation Only", variable=self._display_mode_var,
            value="translation_only", command=self._on_menu_display_mode_selected,
        )
        display_menu.add_radiobutton(
            label="Source + Translation", variable=self._display_mode_var,
            value="with_transcription", command=self._on_menu_display_mode_selected,
        )
        self.menu.add_cascade(label="Display Mode", menu=display_menu)
        submenus = [display_menu]

        # ---- Language Pair ----
        if self.language_pair_names:
            self._language_pair_var = tk.StringVar(value=initial_language_pair or "")
            lang_menu = self._lang_menu = self._submenu()
            self._fill_pair_menu()
            self.menu.add_cascade(label="Language Pair", menu=lang_menu)
            submenus.append(lang_menu)

        # ---- Endpoint Preset ----
        if self.preset_names:
            self._preset_var = tk.StringVar(value=initial_preset or "")
            preset_menu = self._submenu()
            for name in self.preset_names:
                preset_menu.add_radiobutton(
                    label=name, variable=self._preset_var, value=name,
                    command=lambda: self._backend_choice("endpoint_preset", self._preset_var),
                )
            self.menu.add_cascade(label="Endpoint Preset", menu=preset_menu)
            submenus.append(preset_menu)

        self.menu.add_separator()
        self.menu.add_command(label="Settings…", command=self._open_settings)
        self.menu.add_command(label="Exit", command=self.shutdown)

        # Auto-close when it loses focus (covers clicks outside the app).
        for m in [self.menu] + submenus:
            m.bind("<FocusOut>", lambda e: self._close_menu())

    def _fill_pair_menu(self):
        self._lang_menu.delete(0, "end")
        for name in self.language_pair_names:
            self._lang_menu.add_radiobutton(
                label=name, variable=self._language_pair_var, value=name,
                command=lambda: self._backend_choice("language_pair", self._language_pair_var),
            )

    def _submenu(self):
        return tk.Menu(
            self.menu, tearoff=0, bg="#2d2d2d", fg="#ffffff",
            activebackground="#444444", activeforeground="#ffffff",
        )

    # -------------------- Settings: apply / persist --------------------
    def _all_values(self):
        values = dict(self.settings)
        if self.backend_values:
            values.update(self.backend_values())
        return values

    def _set_ui(self, changes):
        """Persist + apply settings owned by this module."""
        self._save_config_patch(
            {k: (None if v == DEFAULTS[k] else v) for k, v in changes.items()})
        self._apply_ui_settings(changes)

    def _on_settings_apply(self, changes):
        """Entry point for the Settings window and the menu. Raises (message
        shown to the user) if the backend rejects a change; nothing is saved then."""
        ui = {k: v for k, v in changes.items() if k in DEFAULTS}
        backend = {k: v for k, v in changes.items() if k not in DEFAULTS}
        if backend and self.on_settings_change:
            self.on_settings_change(backend)      # may raise before anything is saved
            self._save_config_patch(backend)
        if ui:
            self._set_ui(ui)
        self._sync_menu_vars()

    def _backend_choice(self, key, var):
        try:
            self._on_settings_apply({key: var.get()})
        except Exception as e:
            self.set_status(str(e))
            self._sync_menu_vars()                # put the radio button back

    def _sync_menu_vars(self):
        values = self.backend_values() if self.backend_values else {}
        if hasattr(self, "_language_pair_var"):
            self._language_pair_var.set(values.get("language_pair") or "")
        if hasattr(self, "_preset_var"):
            self._preset_var.set(values.get("endpoint_preset") or "")
        if self.backend_choices and hasattr(self, "_lang_menu"):
            names = list(self.backend_choices().get("language_pair") or [])
            if names and names != self.language_pair_names:
                self.language_pair_names = names          # the tray menu reads this too
                self._fill_pair_menu()
            if names and self._settings_win is not None and self._settings_win.alive():
                self._settings_win.set_choices("language_pair", names)
        self.current_language_pair = values.get("language_pair") or self.current_language_pair
        self.current_preset = values.get("endpoint_preset") or self.current_preset
        if self._settings_win is not None and self._settings_win.alive():
            self._settings_win.set_values(self._all_values())
        self._tray_refresh_menu()

    def _open_settings(self):
        if self._tray_hidden:
            self._tray_show_window()          # the form belongs next to a visible window
        if self._settings_win is not None and self._settings_win.alive():
            self._settings_win.lift()
            return
        self._settings_win = SettingsWindow(
            self.root, self._ui_tabs + self.settings_tabs, self._all_values(),
            on_apply=self._on_settings_apply, scale=self.settings["ui_scale"])

    def _on_menu_display_mode_selected(self):
        self._set_ui({"show_only_translation": self._display_mode_var.get() == "translation_only"})
        self._sync_menu_vars()

    def _apply_ui_settings(self, changes):
        s = self.settings
        s.update(changes)
        if set(changes) <= {"ui_scale"}:
            return                                  # only the Settings window itself changes
        if {"font_family", "font_size", "font_weight"} & changes.keys():
            family = self._resolve_family(s["font_family"])
            self.caption_font.configure(family=family, size=s["font_size"], weight=s["font_weight"])
            self.partial_font.configure(family=family, size=s["font_size"])
        if "caption_lines" in changes:
            for text in (self.left_text, self.right_text):
                text.config(height=s["caption_lines"])
        if "show_only_translation" in changes:
            self._display_mode_var.set(
                "translation_only" if s["show_only_translation"] else "with_transcription")
        self._apply_colors()
        self._apply_pane_layout()

    def _hex(self, color):
        """Any Tk colour name or #rgb -> #rrggbb (keeps Tk's palette handling out of it)."""
        try:
            r, g, b = self.root.winfo_rgb(color)
            return f"#{r >> 8:02x}{g >> 8:02x}{b >> 8:02x}"
        except tk.TclError:
            return color

    def _apply_colors(self):
        bg, fg = self._hex(self.settings["bg_color"]), self._hex(self.settings["fg_color"])
        for w in (self.root, self.main_frame, self.text_frame, self.partial_frame):
            w.configure(bg=bg)
        for w in (self.left_text, self.right_text):
            w.configure(bg=bg, fg=fg)
        for w in (self.partial_left, self.partial_right):
            w.configure(bg=bg)

    def _set_status_now(self, text):
        self.right_text.config(state=tk.NORMAL)
        self.right_text.delete("1.0", tk.END)
        self.right_text.insert("1.0", text)
        self.right_text.config(state=tk.DISABLED)
        if not self.show_only_translation:
            self.left_text.config(state=tk.NORMAL)
            self.left_text.delete("1.0", tk.END)
            self.left_text.insert("1.0", text)
            self.left_text.config(state=tk.DISABLED)

    # -------------------- Pane layout (display-mode dependent) --------------------
    def _apply_pane_layout(self):
        """(Re)apply the grid/pack layout for the current settings.
        Called at build time and again whenever a setting changes."""
        self.left_text.grid_remove()
        self.right_text.grid_remove()
        self.scrollbar.grid_remove()

        if self.show_only_translation:
            self.right_text.grid(row=0, column=0, sticky="nsew")
            self.scrollbar.grid(row=0, column=1, sticky="ns")
            self.right_text.config(yscrollcommand=self.scrollbar.set)
            # Reset BOTH weight and uniform on every column, every time: dual-pane
            # mode sets uniform="col" on columns 0/1, and if that lingers the
            # scrollbar column gets forced to the text column's width.
            self.text_frame.grid_columnconfigure(0, weight=1, uniform="")
            self.text_frame.grid_columnconfigure(1, weight=0, uniform="")
            self.text_frame.grid_columnconfigure(2, weight=0, uniform="")
        else:
            self.left_text.grid(row=0, column=0, sticky="nsew")
            self.right_text.grid(row=0, column=1, sticky="nsew", padx=(5, 0))
            self.scrollbar.grid(row=0, column=2, sticky="ns")
            self.left_text.config(yscrollcommand=self._on_text_scroll)
            self.right_text.config(yscrollcommand=self._on_text_scroll)
            self.text_frame.grid_columnconfigure(0, weight=1, uniform="col")
            self.text_frame.grid_columnconfigure(1, weight=1, uniform="col")
            self.text_frame.grid_columnconfigure(2, weight=0, uniform="")

        if self.settings["show_partial"]:
            self.partial_frame.pack(fill="x", padx=10, pady=(5, 10))
            self.partial_left.pack(side=tk.LEFT, expand=True, fill="x")
            if self.show_only_translation:
                self.partial_right.pack_forget()
            else:
                self.partial_right.pack(side=tk.RIGHT, expand=True, fill="x", padx=(5, 0))
        else:
            self.partial_frame.pack_forget()

        self._rebuild_text_widgets()
        self._apply_appearance()

    def _apply_appearance(self):
        s = self.settings
        scale = s["window_size_percent"] / 100.0
        w = int(s["base_width"] * scale)
        self._last_geom_w = w

        # Set the width first so geometry is deterministic, then ask Tk itself how
        # tall it needs to be to show everything without clipping (line spacing,
        # padding, the partial row, ...) instead of hand-replicating its layout.
        self.root.geometry(f"{w}x1")
        self.root.update_idletasks()
        natural_h = self.root.winfo_reqheight()
        h = max(int(s["base_height"] * scale) if s["base_height"] else 0, natural_h + 4)

        sw = self.root.winfo_screenwidth()
        sh = self.root.winfo_screenheight()
        if self._pos is None:
            x, y = (sw - w) // 2, sh - h - 60       # first time: bottom centre
        else:
            x, y = self._pos                          # afterwards: stay where the user put it
        x = max(0, min(x, sw - w))
        y = max(0, min(y, sh - h))
        self._pos = (x, y)

        self.root.geometry(f"{w}x{h}+{x}+{y}")
        self.root.configure(bg=self._hex(s["bg_color"]))

        if not self._shown:
            # Opacity fix for Cinnamon / X11
            self.root.deiconify()
            self.root.wait_visibility()
            self._shown = True
        self.root.attributes("-alpha", s["window_opacity"])
        self.root.update_idletasks()

        col_width = max(200, w - 80) if self.show_only_translation else max(200, (w - 100) // 2)
        self.partial_left.config(wraplength=col_width)
        self.partial_right.config(wraplength=col_width)

    def _start_drag(self, event):
        self._close_menu()
        self._drag_x = event.x
        self._drag_y = event.y

    def _on_drag(self, event):
        x = self.root.winfo_x() + event.x - self._drag_x
        y = self.root.winfo_y() + event.y - self._drag_y
        self._pos = (x, y)
        self.root.geometry(f"+{x}+{y}")

    def _show_menu(self, event):
        self.menu.post(event.x_root, event.y_root)
        # Taking focus is what makes <FocusOut> fire reliably when the user clicks
        # elsewhere; an override-redirect, always-on-top window otherwise can
        # leave the popup in a state where outside clicks don't dismiss it.
        self.menu.focus_set()

    def _close_menu(self):
        try:
            self.menu.unpost()
        except tk.TclError:
            pass

    def _increase_font(self):
        self._set_ui({"font_size": min(self.settings["font_size"] + 2, 72)})
        self._sync_menu_vars()

    def _decrease_font(self):
        self._set_ui({"font_size": max(self.settings["font_size"] - 2, 8)})
        self._sync_menu_vars()

    # -------------------- Scroll Sync --------------------
    def _on_scrollbar(self, *args):
        self.right_text.yview(*args)
        if not self.show_only_translation:
            self.left_text.yview(*args)

    def _on_text_scroll(self, first, last):
        self.scrollbar.set(first, last)
        self.right_text.yview_moveto(first)
        if not self.show_only_translation:
            self.left_text.yview_moveto(first)

    def _on_mousewheel(self, event):
        delta = 0
        if event.num == 4 or getattr(event, "delta", 0) > 0:
            delta = -3
        elif event.num == 5 or getattr(event, "delta", 0) < 0:
            delta = 3
        self.right_text.yview_scroll(delta, "units")
        if not self.show_only_translation:
            self.left_text.yview_scroll(delta, "units")
        return "break"

    # -------------------- Display --------------------
    def _update_caption(self):
        # Apply tray events (window toggle, pause, menu picks) on the Tk thread.
        self._drain_tray_queue()

        changed = False
        try:
            while True:
                kind, payload = self.text_queue.get_nowait()
                if kind == "final":
                    self.current_partial = ""
                    self._append_entry(payload, translated=None)
                    changed = True
                elif kind == "partial":
                    self.current_partial = payload
                    changed = True
                elif kind == "translated":
                    self.current_partial = ""
                    source, translated = payload
                    self._update_translation(source, translated)
                    changed = True
                elif kind == "clear":
                    self.caption_entries = []
                    self.current_partial = ""
                    self._rebuild_text_widgets()
                    changed = True
                elif kind == "status":
                    self._set_status_now(payload)
                elif kind == "translation_mode":
                    self.translation_enabled = payload
                    self._rebuild_text_widgets()
        except queue.Empty:
            pass

        if changed:
            self._update_partial_display()

        if self.running:
            self.root.after(100, self._update_caption)

    def _insert_chunk(self, text_widget, chunk_text, font):
        """
        Insert chunk_text into text_widget. If the widget isn't empty and the
        last VISUAL row hasn't yet filled line_fill_threshold of the widget's
        pixel width, append onto that row (joined by the separator) instead of
        starting a new line — this stops short 1-3 word fragments from each
        grabbing their own line.
        """
        chunk_text = (chunk_text or "").strip()
        if not chunk_text:
            return

        is_empty = text_widget.get("1.0", "end-1c") == ""
        if not is_empty:
            # "display linestart" (not plain "linestart"): plain linestart walks back
            # to the last real \n — the whole logical paragraph, even if it already
            # wrapped onto several visual rows — so a wrapped entry always measured
            # as "full". "display linestart" finds the start of the last VISUAL row.
            last_line = text_widget.get("end-1c display linestart", "end-1c")
            avail_px = text_widget.winfo_width() - 2 * 5  # minus padx
            if avail_px <= 1:
                avail_px = max(200, self._last_geom_w - 40)
            last_px = font.measure(last_line) if last_line.strip() else 0
            if last_line.strip() and last_px < avail_px * self.settings["line_fill_threshold"]:
                text_widget.insert(tk.END, f" {self.settings['line_fill_separator']} {chunk_text}")
                return
            text_widget.insert(tk.END, f"\n{chunk_text}")
        else:
            text_widget.insert(tk.END, chunk_text)

    def _append_entry(self, source_text, translated=None):
        entry = {"source": source_text, "translated": translated}
        self.caption_entries.append(entry)
        self._trim_buffer()

        if self.show_only_translation:
            if translated or not self.translation_enabled:
                self._rebuild_text_widgets()
            return

        # Dual-pane mode: strict 1 entry = 1 line on both sides, so source
        # and translation rows stay aligned.
        self.left_text.config(state=tk.NORMAL)
        self.right_text.config(state=tk.NORMAL)

        if len(self.caption_entries) > 1:
            self.left_text.insert(tk.END, "\n")
            self.right_text.insert(tk.END, "\n")

        self.left_text.insert(tk.END, source_text)
        if translated:
            self.right_text.insert(tk.END, translated)

        self.left_text.config(state=tk.DISABLED)
        self.right_text.config(state=tk.DISABLED)
        self._scroll_to_end()

    def _update_translation(self, source_text, translated_text):
        for i in range(len(self.caption_entries) - 1, -1, -1):
            if self.caption_entries[i]["source"] == source_text and self.caption_entries[i]["translated"] is None:
                self.caption_entries[i]["translated"] = translated_text
                self._rebuild_text_widgets()
                break

    def _rebuild_text_widgets(self):
        if not self.show_only_translation:
            self.left_text.config(state=tk.NORMAL)
            self.left_text.delete("1.0", tk.END)

        self.right_text.config(state=tk.NORMAL)
        self.right_text.delete("1.0", tk.END)

        if self.show_only_translation:
            key = "translated" if self.translation_enabled else "source"
            for entry in self.caption_entries:
                if entry[key]:
                    self._insert_chunk(self.right_text, entry[key], self.caption_font)
        else:
            for i, entry in enumerate(self.caption_entries):
                if i > 0:
                    self.left_text.insert(tk.END, "\n")
                    self.right_text.insert(tk.END, "\n")
                self.left_text.insert(tk.END, entry["source"])
                if entry["translated"]:
                    self.right_text.insert(tk.END, entry["translated"])

        if not self.show_only_translation:
            self.left_text.config(state=tk.DISABLED)
        self.right_text.config(state=tk.DISABLED)
        self._scroll_to_end()

    def _trim_buffer(self):
        limit = self.settings["max_buffer_lines"]
        if len(self.caption_entries) > limit:
            self.caption_entries = self.caption_entries[len(self.caption_entries) - limit:]
            self._rebuild_text_widgets()

    def _scroll_to_end(self):
        self.right_text.see(tk.END)
        if not self.show_only_translation:
            self.left_text.see(tk.END)

    def _update_partial_display(self):
        if not self.settings["show_partial"]:
            return
        if self.audio_paused:
            self.partial_left.config(text="⏸ Paused — audio feed muted")
            self.partial_right.config(text="")
            return
        if self.current_partial:
            self.partial_left.config(text=f"{self.current_partial} …")
        else:
            self.partial_left.config(text="")

    # -------------------- Pause / Resume --------------------
    def _toggle_pause(self):
        self.audio_paused = not self.audio_paused
        self.menu.entryconfig(
            self._pause_menu_index,
            label="Resume Audio" if self.audio_paused else "Pause Audio",
        )
        if self.on_pause_toggle:
            try:
                self.on_pause_toggle(self.audio_paused)
            except Exception as e:
                print(f"[Pause] backend toggle error: {e}")
        # Reflect the state in the partial row so it's visible at a glance
        # even without opening the menu.
        self._update_partial_display()
        # Keep the tray in sync: menu label + paused icon variant.
        if self._tray:
            self._tray.set_paused(self.audio_paused)
        self._tray_refresh_menu()

    # -------------------- System Tray --------------------
    def _setup_tray(self):
        """Register a StatusNotifierItem (Plasma 6 / Wayland's tray protocol).
        The DBus side lives on a background thread; clicks and menu picks come
        back through tray_queue and are applied on the Tk thread."""
        try:
            from .caption_tray import TrayController
            self._tray = TrayController(
                menu_source=self._tray_menu_spec,
                on_event=self._tray_event,
            )
            self._tray.start()
            self.root.after(8000, self._check_tray)
        except Exception as e:
            print(f"[Tray] System tray disabled: {e}")
            self._tray = None

    def _check_tray(self):
        """Say so in the terminal if the tray never registered (no tray host / no DBus)."""
        if self._tray is not None and not getattr(self._tray, "_registered", True):
            print("[Tray] Still not registered with a StatusNotifier host after 8 s. "
                  "Is Plasma's system tray running, and is the DBus session bus reachable "
                  "from this terminal? (If an icon exists, check the tray's hidden-icons arrow.)")

    def _tray_menu_spec(self):
        """Plain-data snapshot of the tray menu — mirrors the right-click menu.
        Called from the tray's DBus thread: reads plain attributes only, no Tk."""
        spec = [
            {"label": "Resume Audio" if self.audio_paused else "Pause Audio",
             "action": ("toggle_pause", None)},
            {"separator": True},
            {"label": "Increase Font", "action": ("increase_font", None)},
            {"label": "Decrease Font", "action": ("decrease_font", None)},
            {"separator": True},
            {"label": "Display Mode", "submenu": [
                {"label": "Translation Only", "check_type": "radio",
                 "checked": self.show_only_translation,
                 "action": ("display_mode", "translation_only")},
                {"label": "Source + Translation", "check_type": "radio",
                 "checked": not self.show_only_translation,
                 "action": ("display_mode", "with_transcription")},
            ]},
        ]
        if self.language_pair_names:
            spec.append({"label": "Language Pair", "submenu": [
                {"label": name, "check_type": "radio",
                 "checked": self.current_language_pair == name,
                 "action": ("language_pair", name)}
                for name in self.language_pair_names
            ]})
        if self.preset_names:
            spec.append({"label": "Endpoint Preset", "submenu": [
                {"label": name, "check_type": "radio",
                 "checked": self.current_preset == name,
                 "action": ("preset", name)}
                for name in self.preset_names
            ]})
        spec.append({"separator": True})
        spec.append({"label": "Settings…", "action": ("settings", None)})
        spec.append({"label": "Exit", "action": ("exit", None)})
        return spec

    def _tray_event(self, kind, payload):
        """Tray DBus thread -> Tk thread (queue.Queue is thread-safe)."""
        self.tray_queue.put((kind, payload))

    def _drain_tray_queue(self):
        while True:
            try:
                kind, payload = self.tray_queue.get_nowait()
            except queue.Empty:
                return
            try:
                if kind == "toggle_visible":
                    self._tray_toggle_visible()
                elif kind == "context_menu":
                    # Fallback for hosts that don't render DBusMenu menus.
                    self.menu.post(payload[0], payload[1])
                    self.menu.focus_set()
                elif kind == "toggle_pause":
                    self._toggle_pause()
                elif kind == "increase_font":
                    self._increase_font()
                elif kind == "decrease_font":
                    self._decrease_font()
                elif kind == "display_mode":
                    self._display_mode_var.set(payload)
                    self._on_menu_display_mode_selected()
                elif kind == "language_pair":
                    self._language_pair_var.set(payload)
                    self._backend_choice("language_pair", self._language_pair_var)
                elif kind == "preset":
                    self._preset_var.set(payload)
                    self._backend_choice("endpoint_preset", self._preset_var)
                elif kind == "settings":
                    self._open_settings()
                elif kind == "exit":
                    self.shutdown()
            except tk.TclError:
                pass
            except Exception as e:
                print(f"[Tray] action '{kind}' failed: {e}")

    def _tray_refresh_menu(self):
        if self._tray:
            try:
                self._tray.refresh()
            except Exception as e:
                print(f"[Tray] refresh failed: {e}")

    def _tray_toggle_visible(self):
        """Left/middle tray click: hide the window AND pause the audio; the
        next click brings the window back and resumes."""
        if self._tray_hidden:
            self._tray_show_window()
        else:
            self._tray_hide_window()

    def _tray_hide_window(self):
        self._tray_hidden = True
        try:
            self._tray_restore_geom = self.root.geometry()
            self._close_menu()
            self.root.withdraw()
        except tk.TclError:
            pass
        if not self.audio_paused:
            self._toggle_pause()

    def _tray_show_window(self):
        self._tray_hidden = False
        try:
            if self._tray_restore_geom:
                self.root.geometry(self._tray_restore_geom)
            self.root.deiconify()
            self.root.attributes("-topmost", True)
            self.root.lift()
        except tk.TclError:
            pass
        if self.audio_paused:
            self._toggle_pause()

    # -------------------- Lifecycle --------------------
    def fatal_error(self, msg):
        self.right_text.config(state=tk.NORMAL)
        self.right_text.delete("1.0", tk.END)
        self.right_text.insert("1.0", msg)
        self.right_text.config(fg="#ff6666")
        self.right_text.config(state=tk.DISABLED)
        if not self.show_only_translation:
            self.left_text.config(state=tk.NORMAL)
            self.left_text.delete("1.0", tk.END)
            self.left_text.insert("1.0", msg)
            self.left_text.config(fg="#ff6666")
            self.left_text.config(state=tk.DISABLED)
        self.root.after(8000, self.shutdown)

    def shutdown(self):
        self.running = False
        if self._tray:
            try:
                self._tray.stop()
            except Exception as e:
                print(f"[Tray] stop error: {e}")
            self._tray = None
        if self.on_shutdown:
            try:
                self.on_shutdown()
            except Exception as e:
                print(f"[Shutdown] backend cleanup error: {e}")
        self.root.destroy()
        sys.exit(0)

    def run(self):
        self.root.mainloop()
