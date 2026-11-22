#!/usr/bin/env python3
"""
Settings window for Live Caption: one scrolling, resizable page built from a
schema, drawn with a Breeze-like ttk theme (see breeze_theme.py).

Nothing in here knows about captions or speech. The caller passes a list of
sections (each a list of fields built with F(...)) and the current values; when
the user presses Apply, on_apply(changes) is called with only the values that
changed. If on_apply raises, the message is shown in the window instead.

Scaling: the window sizes everything from one factor - detected automatically
(KDE/Xft) or set by the "ui_scale" field if the caller provides one (0 = auto).
Ctrl +/-/0 and Ctrl+mouse-wheel zoom it live.
"""

import json
import os
import re
import shutil
import subprocess
import tkinter as tk
from tkinter import filedialog, font as tkfont, ttk

from .breeze_theme import Theme, detect_scale

# Quick-pick swatches for colour fields
PALETTE = ["#1a1a1a", "#000000", "#2b2b2b", "#555555", "#ffffff", "#f2f2f2", "#fff3b0", "#0b2540"]


def F(key, label, kind, **opts):
    """Describe one setting.

    kind:  int | float | scale | bool | choice | font | color | text | password | path | rows | button
    opts:  default, min, max, step, choices, help,
           labels={choice_value: shown_text}   (choice only)
           fills={choice_value: {other_key: value}}  (choice only: picking a value
             also sets those other fields, e.g. a preset filling in its numbers)
           show_if=(other_key, [values])  (row is only shown for those values)
           text, on_click(settings_window)  (button only: a push button that runs a function)
           columns=[{"title", "width", "kind": "text"|"path", "optional": bool}, ...]
             (rows only: an editable table; the value is a list of lists of strings)
    """
    return dict(key=key, label=label, kind=kind, **opts)


class _Field:
    """One form row: label + the right widget for its kind + optional help text."""

    def __init__(self, owner, parent, row, spec):
        self.owner, self.spec, self.kind = owner, spec, spec["kind"]
        self.widgets = []            # everything gridded for this row (for show_if)
        self.visible = True
        px, k = owner.px, self.kind
        pad_y = (px(9), 0)

        self.var = (tk.BooleanVar() if k == "bool" else
                    tk.DoubleVar() if k == "scale" else tk.StringVar())
        self.labels = spec.get("labels") or {}
        self.combo = None
        self.row_vars, self.row_widgets, self._next_row = [], [], 1

        if k == "button":
            box = ttk.Button(parent, text=spec["text"], command=lambda: spec["on_click"](owner))
            box.grid(row=row, column=1, sticky="w", pady=pad_y)
            self.widgets.append(box)
        elif k == "bool":
            box = ttk.Checkbutton(parent, text=spec["label"], variable=self.var)
            box.grid(row=row, column=1, sticky="w", pady=pad_y)
            self.widgets.append(box)
        else:
            lab = ttk.Label(parent, text=spec["label"], anchor="e")
            lab.grid(row=row, column=0, sticky="ne", padx=(0, px(14)), pady=(px(9) + px(5), 0))
            box = ttk.Frame(parent)
            box.grid(row=row, column=1, sticky="ew", pady=pad_y)
            self.widgets += [lab, box]
            self._build_input(box)

        if spec.get("help"):
            h = ttk.Label(parent, text=spec["help"], style="Help.TLabel", justify="left",
                          wraplength=px(300))
            h.grid(row=row + 1, column=1, sticky="w", pady=(px(2), 0))
            self.widgets.append(h)
            owner.help_labels.append(h)

    def _build_input(self, box):
        owner, spec, k, px = self.owner, self.spec, self.kind, self.owner.px
        if k in ("int", "float"):
            ttk.Spinbox(box, font=owner.theme.fonts['base'], from_=spec.get("min", 0), to=spec.get("max", 99999),
                        increment=spec.get("step", 1), textvariable=self.var,
                        width=9).pack(side="left")
        elif k == "scale":
            self.value_label = ttk.Label(box, width=5, anchor="e")
            self.value_label.pack(side="right")
            self.var.trace_add("write", lambda *_: self.value_label.config(
                text=f"{self.var.get():.2f}"))
            ttk.Scale(box, from_=spec["min"], to=spec["max"], variable=self.var,
                      command=self._snap).pack(side="left", fill="x", expand=True, padx=(0, px(8)))
        elif k in ("choice", "font"):
            if k == "choice":
                values = [self.labels.get(c, c) for c in spec["choices"]]
            else:
                values = sorted({f for f in tkfont.families(owner.win) if not f.startswith("@")})
            cb = ttk.Combobox(box, font=owner.theme.fonts["base"], textvariable=self.var, values=values, state="readonly", width=26)
            cb.pack(side="left", fill="x", expand=True)
            self.combo = cb
            cb.bind("<<ComboboxSelected>>", lambda e: owner._fill(spec, self.choice_key()))
        elif k == "color":
            self.swatch = tk.Frame(box, width=px(26), height=px(26),
                                   highlightthickness=1, highlightbackground=owner.theme.p["border"])
            self.swatch.pack(side="left", padx=(0, px(6)))
            ttk.Entry(box, font=owner.theme.fonts["base"], textvariable=self.var, width=9).pack(side="left", padx=(0, px(10)))
            for c in PALETTE:
                chip = tk.Frame(box, width=px(18), height=px(18), bg=c, cursor="hand2",
                                highlightthickness=1, highlightbackground=owner.theme.p["border"])
                chip.pack(side="left", padx=px(2))
                chip.bind("<Button-1>", lambda e, c=c: self.var.set(c))
            self.var.trace_add("write", self._paint_swatch)
        elif k == "path":
            ttk.Entry(box, font=owner.theme.fonts["base"], textvariable=self.var, width=30).pack(side="left", fill="x", expand=True)
            ttk.Button(box, text="Browse…", command=lambda: self._browse(self.var)).pack(side="left", padx=(px(6), 0))
        elif k == "rows":
            self._build_rows(box)
        else:  # text | password
            ttk.Entry(box, font=owner.theme.fonts["base"], textvariable=self.var, width=24,
                      show="•" if k == "password" else "").pack(side="left", fill="x", expand=True)

    # ---- "rows": an editable table (add / remove lines) ----
    def _build_rows(self, box):
        px = self.owner.px
        self.table = ttk.Frame(box)
        self.table.pack(fill="x")
        cols = self.spec["columns"]
        for i, c in enumerate(cols):
            self.table.columnconfigure(i, weight=3 if c.get("kind") == "path" else 1)
            ttk.Label(self.table, text=c["title"], style="Help.TLabel").grid(
                row=0, column=i, sticky="w", padx=(0, px(6)))
        ttk.Button(box, text="+ Add", command=lambda: self._add_row()).pack(
            anchor="w", pady=(px(8), 0))

    def _add_row(self, values=None):
        owner, px = self.owner, self.owner.px
        cols = self.spec["columns"]
        values = list(values or [])
        values += [""] * (len(cols) - len(values))
        r, vars_, widgets = self._next_row, [], []
        self._next_row += 1
        for i, c in enumerate(cols):
            var = tk.StringVar(value=str(values[i]))
            vars_.append(var)
            cell = ttk.Frame(self.table)
            cell.grid(row=r, column=i, sticky="ew", padx=(0, px(6)), pady=(px(3), 0))
            ttk.Entry(cell, font=owner.theme.fonts["base"], textvariable=var,
                      width=c.get("width", 12)).pack(side="left", fill="x", expand=True)
            if c.get("kind") == "path":
                ttk.Button(cell, text="…", width=3, command=lambda v=var: self._browse(v)).pack(
                    side="left", padx=(px(4), 0))
            widgets.append(cell)
        rm = ttk.Button(self.table, text="✕", width=3)
        rm.grid(row=r, column=len(cols), pady=(px(3), 0))
        widgets.append(rm)
        rm.configure(command=lambda: self._remove_row(vars_))
        self.row_vars.append(vars_)
        self.row_widgets.append(widgets)

    def _remove_row(self, vars_):
        i = self.row_vars.index(vars_)
        for w in self.row_widgets.pop(i):
            w.destroy()
        self.row_vars.pop(i)

    def _clear_rows(self):
        for widgets in self.row_widgets:
            for w in widgets:
                w.destroy()
        self.row_vars, self.row_widgets = [], []

    def snapshot(self):
        if self.kind == "rows":
            return json.dumps([[v.get() for v in vs] for vs in self.row_vars])
        return self.var.get()

    def restore(self, value):
        if self.kind == "rows":
            self.set(json.loads(value))
        else:
            self.var.set(value)

    def choice_key(self):
        """The stored value for a choice (the combobox shows its label)."""
        shown = self.var.get()
        for key, label in self.labels.items():
            if label == shown:
                return key
        return shown

    def _snap(self, raw):
        step = self.spec.get("step", 0.01)
        self.var.set(round(round(float(raw) / step) * step, 4))

    def _paint_swatch(self, *_):
        try:
            self.swatch.config(bg=self.var.get())
        except tk.TclError:
            pass  # half-typed colour

    def _browse(self, var):
        """Native folder picker (KDE's, else GTK's), falling back to Tk's own."""
        win = self.owner.win
        start = os.path.expanduser(var.get() or "~")
        cmd = None
        if shutil.which("kdialog"):
            cmd = ["kdialog", "--getexistingdirectory", start, "--title", "Choose a folder"]
        elif shutil.which("zenity"):
            cmd = ["zenity", "--file-selection", "--directory",
                   "--filename", start.rstrip("/") + "/"]
        if cmd:
            proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)

            def poll():                      # don't block the caption window while it's open
                try:
                    if proc.poll() is None:
                        win.after(150, poll)
                        return
                    out = proc.stdout.read().strip()
                    if proc.returncode == 0 and out:
                        var.set(out)
                except tk.TclError:
                    pass
            poll()
            return
        chosen = filedialog.askdirectory(parent=win, initialdir=start)
        if chosen:
            var.set(chosen)

    def get(self):
        s, k, label = self.spec, self.kind, self.spec["label"]
        if k in ("int", "float"):
            try:
                v = int(float(self.var.get())) if k == "int" else float(self.var.get())
            except (ValueError, tk.TclError):
                raise ValueError(f"{label}: please enter a number")
            lo, hi = s.get("min"), s.get("max")
            if (lo is not None and v < lo) or (hi is not None and v > hi):
                raise ValueError(f"{label}: must be between {lo} and {hi}")
            return v
        if k == "scale":
            return round(float(self.var.get()), 4)
        if k == "bool":
            return bool(self.var.get())
        if k == "color":
            v = self.var.get().strip()
            try:
                self.owner.win.winfo_rgb(v)
            except tk.TclError:
                raise ValueError(f"{label}: not a valid colour (try #1a1a1a)")
            return v.lower() if re.fullmatch(r"#[0-9a-fA-F]{6}", v) else v
        if k == "choice":
            return self.choice_key()
        if k == "button":
            return None
        if k == "rows":
            cols = s["columns"]
            rows = [[v.get().strip() for v in vs] for vs in self.row_vars]
            rows = [r for r in rows if any(r)]
            for n, r in enumerate(rows, 1):
                for c, cell in zip(cols, r):
                    if not cell and not c.get("optional"):
                        raise ValueError(f"{label}, row {n}: fill in “{c['title']}”")
            return rows
        return self.var.get()

    def set(self, value):
        if self.kind == "rows":
            self._clear_rows()
            for row in value or []:
                self._add_row(row)
        elif self.kind == "float":
            self.var.set(f"{float(value):g}")
        elif self.kind == "bool":
            self.var.set(bool(value))
        elif self.kind == "scale":
            self.var.set(float(value))
        elif self.kind == "choice":
            self.var.set(self.labels.get(value, "" if value is None else str(value)))
        else:
            self.var.set("" if value is None else str(value))


class SettingsWindow:
    def __init__(self, master, tabs, values, on_apply, title="Live Caption Settings", scale=0.0):
        self.master, self.tabs, self.on_apply = master, tabs, on_apply
        self.user_scale = float(scale or 0)
        self.auto_scale = detect_scale(master)
        self.win = tk.Toplevel(master)
        self.win.title(title)
        self.win.attributes("-topmost", True)
        self.fields = {}
        self._current = {}
        self.scale = 0.0
        self._build()
        self.set_values(values)
        self._place()
        for seq in ("<Escape>",):
            self.win.bind(seq, lambda e: self.destroy())
        for seq, d in (("<Control-plus>", .1), ("<Control-equal>", .1), ("<Control-KP_Add>", .1),
                       ("<Control-minus>", -.1), ("<Control-KP_Subtract>", -.1)):
            self.win.bind(seq, lambda e, d=d: self._zoom(d))
        self.win.bind("<Control-0>", lambda e: self._zoom(None))

    # ---- sizing helpers ----
    def px(self, n):
        return self.theme.px(n)

    # ---- building ----
    def _build(self):
        """(Re)create all widgets at the current scale, keeping typed values."""
        keep = {k: f.snapshot() for k, f in self.fields.items()}
        self.scale = self.user_scale or self.auto_scale
        for child in self.win.winfo_children():
            child.destroy()
        self.theme = Theme(self.win, self.scale)
        p, px = self.theme.p, self.px
        self.win.configure(bg=p["window"])
        self.help_labels, self.fields = [], {}

        # footer (packed first so it never scrolls away)
        self._footer = footer = ttk.Frame(self.win, padding=(px(18), px(10), px(18), px(12)))
        footer.pack(side="bottom", fill="x")
        ttk.Separator(self.win).pack(side="bottom", fill="x")
        self.status = ttk.Label(footer, text="", wraplength=px(300))
        ttk.Button(footer, text="Reset to defaults", command=self._reset).pack(side="left")
        ttk.Button(footer, text="Close", command=self.destroy).pack(side="right")
        ttk.Button(footer, text="Apply", style="Accent.TButton",
                   command=self._apply).pack(side="right", padx=(0, px(8)))
        self.status.pack(side="left", fill="x", expand=True, padx=px(14))

        # scrolling page
        body = ttk.Frame(self.win)
        body.pack(fill="both", expand=True)
        self.canvas = tk.Canvas(body, bg=p["window"], highlightthickness=0, bd=0)
        sb = ttk.Scrollbar(body, orient="vertical", command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        self.canvas.pack(side="left", fill="both", expand=True)

        self.page = ttk.Frame(self.canvas, padding=(px(26), px(6), px(22), px(18)))
        self.page.columnconfigure(1, weight=1)
        self._page_id = self.canvas.create_window((0, 0), window=self.page, anchor="nw")
        self.page.bind("<Configure>", lambda e: self.canvas.configure(
            scrollregion=self.canvas.bbox("all")))
        self.canvas.bind("<Configure>", self._on_canvas_configure)

        row = 0
        for n, tab in enumerate(self.tabs):
            ttk.Label(self.page, text=tab["title"], style="Heading.TLabel").grid(
                row=row, column=0, columnspan=2, sticky="w", pady=(px(4 if n == 0 else 22), px(4)))
            ttk.Separator(self.page).grid(row=row + 1, column=0, columnspan=2, sticky="ew")
            row += 2
            for spec in tab["fields"]:
                self.fields[spec["key"]] = _Field(self, self.page, row, spec)
                row += 2
        self._rows = row
        for key, value in keep.items():
            if key in self.fields:
                self.fields[key].restore(value)

        # show_if: rows that depend on another field's value
        for field in self.fields.values():
            cond = field.spec.get("show_if")
            if cond and cond[0] in self.fields:
                self.fields[cond[0]].var.trace_add("write", self._update_visibility)
        self._update_visibility()

        for w in self._descendants(self.win):
            w.bind("<MouseWheel>", self._on_wheel, add="+")
            w.bind("<Button-4>", self._on_wheel, add="+")
            w.bind("<Button-5>", self._on_wheel, add="+")

    def _descendants(self, w):
        yield w
        for c in w.winfo_children():
            yield from self._descendants(c)

    def _on_canvas_configure(self, e):
        self.canvas.itemconfigure(self._page_id, width=e.width)
        self._rewrap(e.width)

    def _rewrap(self, width):
        """Help text re-wraps to the room the field column actually has."""
        try:
            label_w = self.page.grid_bbox(0, 0, 0, max(0, self._rows - 1))[2]
        except tk.TclError:
            label_w = 0
        wrap = max(self.px(200), width - label_w - self.px(26 + 22 + 18))
        for h in self.help_labels:
            h.configure(wraplength=wrap)

    def _update_visibility(self, *_):
        for field in self.fields.values():
            cond = field.spec.get("show_if")
            if not cond:
                continue
            ctrl = self.fields.get(cond[0])
            value = None if ctrl is None else (
                ctrl.choice_key() if ctrl.kind == "choice" else ctrl.var.get())
            show = ctrl is None or value in cond[1]
            field.visible = show
            for w in field.widgets:
                (w.grid if show else w.grid_remove)()

    def _on_wheel(self, e):
        if e.state & 0x4:                                   # Ctrl + wheel = zoom
            up = e.num == 4 or getattr(e, "delta", 0) > 0
            self._zoom(.1 if up else -.1)
            return "break"
        up = e.num == 4 or getattr(e, "delta", 0) > 0
        self.canvas.yview_scroll(-3 if up else 3, "units")
        return "break"

    def _place(self):
        """First-time size and position: fits the content, centred, never taller
        than the screen."""
        px = self.px
        self.win.update_idletasks()
        sw, sh = self.win.winfo_screenwidth(), self.win.winfo_screenheight()
        w = min(int(sw * .9), max(px(700), self.page.winfo_reqwidth() + px(40)))
        footer_h = self._footer.winfo_reqheight() + 2
        h = min(int(sh * .88), self.page.winfo_reqheight() + footer_h)
        self.win.minsize(px(460), px(320))
        self.win.geometry(f"{w}x{h}+{(sw - w) // 2}+{max(0, (sh - h) // 3)}")

    # ---- zoom ----
    def _zoom(self, delta):
        """delta: +/- step, or None for back to automatic."""
        new = 0.0 if delta is None else min(4.0, max(0.6, round((self.user_scale or self.scale) + delta, 2)))
        field = self.fields.get("ui_scale")
        if field is not None:
            field.set(new)
            self._apply(only={"ui_scale"})
        else:
            self._rescale(new)

    def _rescale(self, new):
        old = self.scale
        self.user_scale = float(new or 0)
        w, h = self.win.winfo_width(), self.win.winfo_height()
        self._build()
        ratio = (self.user_scale or self.auto_scale) / old
        self.win.geometry(f"{max(self.px(460), int(w * ratio))}x{max(self.px(320), int(h * ratio))}")

    # ---- public ----
    def set_values(self, values):
        """Load values into the form (on open, and when something else - a menu pick,
        a zoom - changed them). Fields you have edited but not applied are kept."""
        for key, field in self.fields.items():
            if key not in values:
                continue
            try:
                dirty = key in self._current and field.get() != self._current[key]
            except ValueError:
                dirty = True
            mine = field.snapshot() if dirty else None
            field.set(values[key])
            try:
                self._current[key] = field.get()
            except ValueError:
                self._current[key] = values.get(key)
            if dirty:
                field.restore(mine)
        self._update_visibility()

    def set_choices(self, key, choices):
        """Replace the options of a choice field (e.g. after language pairs changed)."""
        f = self.fields.get(key)
        if f is None or f.combo is None:
            return
        f.spec["choices"] = list(choices)
        f.combo["values"] = [f.labels.get(c, c) for c in choices]

    def alive(self):
        try:
            return bool(self.win.winfo_exists())
        except tk.TclError:
            return False

    def lift(self):
        self.win.deiconify()
        self.win.lift()
        self.win.focus_force()

    def destroy(self):
        if self.alive():
            self.win.destroy()

    # ---- internals ----
    def _fill(self, spec, value):
        for key, v in spec.get("fills", {}).get(value, {}).items():
            if key in self.fields:
                self.fields[key].set(v)

    def _say(self, text, error=False):
        self.status.config(text=text, style="Error.TLabel" if error else "Ok.TLabel")

    def _reset(self):
        for field in self.fields.values():
            if "default" in field.spec:
                field.set(field.spec["default"])
        self._say("Defaults loaded. Press Apply to use them.")

    def _apply(self, only=None):
        changes = {}
        for key, field in self.fields.items():
            if only is not None and key not in only:
                continue
            if not field.visible and only is None:
                continue                      # hidden rows keep their stored value
            try:
                value = field.get()
            except ValueError as e:
                self._say(f"⚠ {e}", error=True)
                return
            if value != self._current.get(key):
                changes[key] = value
        if not changes:
            self._say("Nothing to change.")
            return
        try:
            self.on_apply(changes)
        except Exception as e:
            self._say(f"⚠ {e}", error=True)
            return
        self._current.update(changes)
        if "ui_scale" in changes:
            self._rescale(changes["ui_scale"])
        self._say("✓ Applied")
