#!/usr/bin/env python3
"""
"Download a speech model" dialog, opened from the Settings window.

Shows the catalogue (model_manager.catalog), downloads the picked model in a
background thread with a progress bar, and calls on_done(code, folder) when it is
ready. Uses the Settings window's theme.
"""

import queue
import threading
import tkinter as tk
from tkinter import ttk

from . import model_manager as mm


class ModelDialog:
    def __init__(self, settings, on_done, validate=None):
        self.settings, self.on_done, self.validate = settings, on_done, validate
        self.q = queue.Queue()
        self.cancel = threading.Event()
        self.busy = False
        self.entries = []
        px = settings.px

        self.win = tk.Toplevel(settings.win)
        self.win.title("Download a speech model")
        self.win.transient(settings.win)
        self.win.attributes("-topmost", True)
        self.win.configure(bg=settings.theme.p["window"])
        self.win.protocol("WM_DELETE_WINDOW", self._close)
        self.win.bind("<Escape>", lambda e: self._close())

        body = ttk.Frame(self.win, padding=(px(20), px(16), px(20), px(8)))
        body.pack(fill="both", expand=True)
        ttk.Label(body, text="Download a speech model", style="Heading.TLabel").pack(anchor="w")
        self.note = ttk.Label(body, text="Loading the model list…", style="Help.TLabel")
        self.note.pack(anchor="w", pady=(px(2), px(8)))

        box = ttk.Frame(body)
        box.pack(fill="both", expand=True)
        cols = ("lang", "name", "kind", "size")
        self.tree = ttk.Treeview(box, columns=cols, show="headings", selectmode="browse",
                                 height=8)
        for c, title, w in (("lang", "Language", 110), ("name", "Model", 400),
                            ("kind", "Type", 110), ("size", "Size", 80)):
            self.tree.heading(c, text=title)
            self.tree.column(c, width=px(w), anchor="e" if c == "size" else "w",
                             stretch=(c == "name"))
        sb = ttk.Scrollbar(box, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        self.tree.pack(side="left", fill="both", expand=True)
        self.tree.bind("<<TreeviewSelect>>", self._picked)

        form = ttk.Frame(body)
        form.pack(fill="x", pady=(px(12), 0))
        form.columnconfigure(1, weight=1)
        font = settings.theme.fonts["base"]
        ttk.Label(form, text="Language code", anchor="e").grid(row=0, column=0, sticky="e", padx=(0, px(10)))
        self.code = tk.StringVar()
        ttk.Entry(form, textvariable=self.code, width=10, font=font).grid(row=0, column=1, sticky="w")
        ttk.Label(form, text="The short name you will use in Language pairs (ru, zh, ja, …).",
                  style="Help.TLabel").grid(row=1, column=1, sticky="w", pady=(px(2), px(8)))
        ttk.Label(form, text="Or a link", anchor="e").grid(row=2, column=0, sticky="e", padx=(0, px(10)))
        self.url = tk.StringVar()
        ttk.Entry(form, textvariable=self.url, font=font).grid(row=2, column=1, sticky="ew")
        ttk.Label(form, text="Paste the address of a .tar.bz2 / .tar.gz model archive instead of picking one.",
                  style="Help.TLabel").grid(row=3, column=1, sticky="w", pady=(px(2), 0))

        self.bar = ttk.Progressbar(body, mode="determinate", maximum=100)
        self.bar.pack(fill="x", pady=(px(14), px(4)))
        self.status = ttk.Label(body, text="", wraplength=px(620))
        self.status.pack(anchor="w")

        foot = ttk.Frame(self.win, padding=(px(20), px(6), px(20), px(14)))
        foot.pack(fill="x")
        self.go = ttk.Button(foot, text="Download", style="Accent.TButton", command=self._start)
        self.go.pack(side="right")
        self.cancel_btn = ttk.Button(foot, text="Close", command=self._close)
        self.cancel_btn.pack(side="right", padx=(0, px(8)))

        self.win.update_idletasks()
        sw = settings.win
        w, h = px(760), max(px(520), self.win.winfo_reqheight())
        x = sw.winfo_rootx() + max(0, (sw.winfo_width() - w) // 2)
        y = sw.winfo_rooty() + px(30)
        self.win.geometry(f"{w}x{h}+{x}+{y}")
        self.win.minsize(px(560), px(420))

        threading.Thread(target=lambda: self.q.put(("catalog", mm.catalog())), daemon=True).start()
        self._poll()

    # ---- events ----
    def _picked(self, _e=None):
        sel = self.tree.selection()
        if sel:
            self.code.set(self.entries[int(sel[0])]["lang"])

    def _start(self):
        if self.busy:
            return
        url = self.url.get().strip()
        sel = self.tree.selection()
        if url:
            name = url.rstrip("/").rsplit("/", 1)[-1]
            entry = dict(id=name.split(".tar")[0], name=name, url=url, size_mb=0, sha256=None)
        elif sel:
            entry = self.entries[int(sel[0])]
        else:
            self._say("Pick a model from the list, or paste a link.", error=True)
            return
        code = self.code.get().strip()
        if not code:
            self._say("Type a language code first (for example ru, zh or ja).", error=True)
            return
        self.busy = True
        self.cancel.clear()
        self.go.state(["disabled"])
        self.cancel_btn.configure(text="Cancel")
        self.bar.configure(value=0)
        self._say(f"Downloading {entry['name']}…")

        def work():
            try:
                folder = mm.download(entry, lambda *a: self.q.put(("progress", a)),
                                     cancel=self.cancel, validate=self.validate)
                self.q.put(("done", (code, folder)))
            except mm.Cancelled:
                self.q.put(("cancelled", None))
            except Exception as e:
                self.q.put(("error", str(e)))
        threading.Thread(target=work, daemon=True).start()

    def _poll(self):
        try:
            while True:
                kind, data = self.q.get_nowait()
                if kind == "catalog":
                    self.entries, note = data
                    self.note.configure(text=note)
                    for i, e in enumerate(self.entries):
                        size = f"{e['size_mb']} MB" if e.get("size_mb") else ""
                        self.tree.insert("", "end", iid=str(i), values=(
                            mm.pretty_lang(e.get("lang", "")), e["name"], e.get("kind", ""), size))
                elif kind == "progress":
                    phase, done, total = data
                    if phase == "download" and total:
                        self.bar.configure(mode="determinate", value=100 * done / total)
                        self._say(f"Downloading… {done / 1e6:.0f} / {total / 1e6:.0f} MB")
                    elif phase == "extract":
                        self.bar.configure(mode="indeterminate")
                        self.bar.start(12)
                        self._say("Unpacking…")
                elif kind in ("done", "cancelled", "error"):
                    self._finish(kind, data)
        except queue.Empty:
            pass
        try:
            if self.win.winfo_exists():
                self.win.after(100, self._poll)
        except tk.TclError:
            pass

    def _finish(self, kind, data):
        self.busy = False
        self.bar.stop()
        self.bar.configure(mode="determinate", value=100 if kind == "done" else 0)
        self.go.state(["!disabled"])
        self.cancel_btn.configure(text="Close")
        if kind == "done":
            code, folder = data
            self._say(f"✓ Downloaded to {folder}")
            self.on_done(code, folder)
        elif kind == "cancelled":
            self._say("Cancelled.")
        else:
            self._say(f"⚠ {data}", error=True)

    def _say(self, text, error=False):
        self.status.configure(text=text, style="Error.TLabel" if error else "TLabel")

    def _close(self):
        if self.busy:
            self.cancel.set()           # Cancel button while downloading
            return
        self.win.destroy()
