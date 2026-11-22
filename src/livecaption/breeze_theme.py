#!/usr/bin/env python3
"""
A Breeze-like ttk theme for Tk, plus display-scale detection.

* Colours come from the user's KDE colour scheme (~/.config/kdeglobals), so the
  Settings window matches Plasma, light or dark. Without KDE it falls back to
  Breeze Dark.
* Everything is sized from one scale factor, so fractional scaling works:
  Tk on XWayland is NOT scaled by KDE (apps are expected to scale themselves),
  so we detect the factor and size fonts, padding and widgets ourselves.
* Only the ttk "clam" theme + a few generated checkbox images (Pillow, optional)
  are used - no extra toolkit.

Public API:
    detect_scale(root)            -> float (override with LIVE_CAPTION_SCALE)
    Theme(root, scale)            -> .p palette, .s scale, .px(n), .fonts, .setup()
"""

import base64
import configparser
import io
import json
import os
import shutil
import subprocess
import tkinter as tk
from tkinter import font as tkfont, ttk

try:
    from PIL import Image, ImageDraw
except ImportError:  # checkboxes fall back to clam's own indicator
    Image = None

# ---------------------------------------------------------------- palettes
BREEZE_DARK = dict(window="#31363b", view="#232629", button="#3b4045", text="#eff0f1",
                   inactive="#a1a9b1", border="#575d62", highlight="#3daee9", sel_text="#ffffff")
BREEZE_LIGHT = dict(window="#eff0f1", view="#fcfcfc", button="#e7e8e9", text="#31363b",
                    inactive="#7f8c8d", border="#bcc0c3", highlight="#3daee9", sel_text="#ffffff")
NEGATIVE, POSITIVE = "#da4453", "#27ae60"


def _rgb(h):
    h = h.lstrip("#")
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


def _hex(c):
    return "#%02x%02x%02x" % tuple(max(0, min(255, int(round(v)))) for v in c)


def mix(a, b, t):
    """Blend colour a towards b by t (0..1)."""
    return _hex([x + (y - x) * t for x, y in zip(_rgb(a), _rgb(b))])


def _luma(h):
    r, g, b = _rgb(h)
    return (0.299 * r + 0.587 * g + 0.114 * b) / 255


def _kdeglobals():
    path = os.path.join(os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config"),
                        "kdeglobals")
    cp = configparser.RawConfigParser(strict=False, interpolation=None)
    cp.optionxform = str
    try:
        cp.read(path, encoding="utf-8")
    except (configparser.Error, OSError, UnicodeDecodeError):
        return None
    return cp if cp.sections() else None


def _kde_colour(cp, section, key):
    try:
        r, g, b = (int(x) for x in cp.get(section, key).split(",")[:3])
        return _hex((r, g, b))
    except (configparser.Error, ValueError):
        return None


def palette():
    """The user's KDE colours (or Breeze Dark)."""
    cp = _kdeglobals()
    if cp is None:
        return dict(BREEZE_DARK), None
    win = _kde_colour(cp, "Colors:Window", "BackgroundNormal")
    base = dict(BREEZE_LIGHT if win and _luma(win) > 0.5 else BREEZE_DARK)
    pick = {
        "window": ("Colors:Window", "BackgroundNormal"),
        "view": ("Colors:View", "BackgroundNormal"),
        "button": ("Colors:Button", "BackgroundNormal"),
        "text": ("Colors:Window", "ForegroundNormal"),
        "inactive": ("Colors:Window", "ForegroundInactive"),
        "highlight": ("Colors:Selection", "BackgroundNormal"),
        "sel_text": ("Colors:Selection", "ForegroundNormal"),
    }
    for key, (sec, name) in pick.items():
        v = _kde_colour(cp, sec, name)
        if v:
            base[key] = v
    base["border"] = mix(base["window"], base["text"], 0.28)
    # Qt draws buttons with a gradient from the button colour; flatten it a little.
    base["button"] = mix(base["button"], base["text"], 0.04)
    font = cp.get("General", "font", fallback=None) if cp.has_section("General") else None
    return base, font


# ---------------------------------------------------------------- scale
def _kscreen_scale():
    exe = shutil.which("kscreen-doctor")
    if not exe:
        return None
    try:
        out = subprocess.run([exe, "-j"], capture_output=True, text=True, timeout=3).stdout
        outputs = [o for o in json.loads(out).get("outputs", []) if o.get("enabled")]
    except (OSError, subprocess.SubprocessError, ValueError):
        return None
    if not outputs:
        return None
    outputs.sort(key=lambda o: o.get("priority", 99))
    try:
        return float(outputs[0].get("scale") or 1.0)
    except (TypeError, ValueError):
        return None


def detect_scale(root):
    """Display scale factor, e.g. 1.3 for 130%.

    Order: LIVE_CAPTION_SCALE, then what Tk/X already reports (Xft.dpi), then
    KDE's own setting (kscreen-doctor), then QT_SCALE_FACTOR / GDK_SCALE."""
    for name in ("LIVE_CAPTION_SCALE",):
        try:
            v = float(os.environ.get(name, ""))
            if 0.5 <= v <= 5:
                return v
        except ValueError:
            pass
    try:
        x = root.winfo_fpixels("1i") / 96.0
    except tk.TclError:
        x = 1.0
    if x > 1.05:                       # X already scales (Xft.dpi) - don't double it
        return round(x, 2)
    k = _kscreen_scale()
    if k and k > 0:
        return round(k, 2)
    for name in ("QT_SCALE_FACTOR", "GDK_SCALE"):
        try:
            v = float(os.environ.get(name, ""))
            if v > 0:
                return v
        except ValueError:
            pass
    return 1.0


# ---------------------------------------------------------------- theme
class Theme:
    _serial = 0

    def __init__(self, root, scale=1.0):
        self.root = root
        self.s = max(0.5, float(scale))
        self.p, kde_font = palette()
        family, pt = "Noto Sans", 10
        if kde_font:
            parts = kde_font.split(",")
            family = parts[0] or family
            try:
                pt = float(parts[1])
            except (IndexError, ValueError):
                pass
        have = set(tkfont.families(root))
        for cand in (family, "Noto Sans", "Cantarell", "DejaVu Sans"):
            if cand in have:
                family = cand
                break
        else:
            family = tkfont.nametofont("TkDefaultFont").actual("family")
        size = -max(8, round(pt * 96 / 72 * self.s))       # negative = pixels
        self.fonts = dict(
            base=tkfont.Font(root, family=family, size=size),
            bold=tkfont.Font(root, family=family, size=size, weight="bold"),
            small=tkfont.Font(root, family=family, size=min(-8, round(size * 0.88))),
            heading=tkfont.Font(root, family=family, size=round(size * 1.15), weight="bold"),
        )
        self._images = []
        self.setup()

    def px(self, n):
        return max(1, round(n * self.s))

    # ---- images ----
    def _photo(self, img):
        buf = io.BytesIO()
        img.save(buf, "PNG")
        ph = tk.PhotoImage(master=self.root, data=base64.b64encode(buf.getvalue()))
        self._images.append(ph)
        return ph

    def _box(self, size, fill, outline, check=None):
        S = size * 4
        img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
        d = ImageDraw.Draw(img)
        inset = S * 0.07
        d.rounded_rectangle((inset, inset, S - inset - 1, S - inset - 1), radius=S * 0.13,
                            fill=fill, outline=outline, width=max(4, S // 17))
        if check:
            pts = [(0.27, 0.52), (0.44, 0.69), (0.75, 0.32)]
            d.line([(x * S, y * S) for x, y in pts], fill=check, width=int(S * 0.12), joint="curve")
        resample = getattr(Image, "Resampling", Image).LANCZOS
        return self._photo(img.resize((size, size), resample))

    # ---- ttk setup ----
    def setup(self):
        p, f, px = self.p, self.fonts, self.px
        root = self.root
        st = ttk.Style(root)
        if "breeze" not in st.theme_names():
            st.theme_create("breeze", parent="clam")
        st.theme_use("breeze")
        hl, border = p["highlight"], p["border"]
        hover_bg = mix(p["button"], hl, 0.18)
        press_bg = mix(p["button"], p["text"], -0.12 if _luma(p["window"]) < 0.5 else 0.12)

        st.configure(".", background=p["window"], foreground=p["text"], font=f["base"],
                     bordercolor=border, lightcolor=border, darkcolor=border,
                     troughcolor=p["view"], focuscolor=p["window"], insertcolor=p["text"],
                     fieldbackground=p["view"], selectbackground=hl, selectforeground=p["sel_text"])
        st.map(".", foreground=[("disabled", p["inactive"])])

        st.configure("TFrame", background=p["window"])
        st.configure("TLabel", background=p["window"], foreground=p["text"])
        st.configure("Heading.TLabel", font=f["heading"], foreground=p["text"])
        st.configure("Help.TLabel", font=f["small"], foreground=p["inactive"])
        st.configure("Ok.TLabel", foreground=POSITIVE)
        st.configure("Error.TLabel", foreground=NEGATIVE)
        st.configure("TSeparator", background=border)

        # buttons
        st.configure("TButton", background=p["button"], foreground=p["text"], anchor="center",
                     padding=(px(14), px(5)), bordercolor=border, lightcolor=p["button"],
                     darkcolor=p["button"], relief="flat")
        st.map("TButton", background=[("pressed", press_bg), ("active", hover_bg)],
               bordercolor=[("focus", hl), ("active", hl)],
               lightcolor=[("pressed", press_bg), ("active", hover_bg)],
               darkcolor=[("pressed", press_bg), ("active", hover_bg)])
        st.configure("Accent.TButton", background=hl, foreground=p["sel_text"],
                     bordercolor=hl, lightcolor=hl, darkcolor=hl)
        st.map("Accent.TButton", background=[("pressed", mix(hl, "#000000", .2)), ("active", mix(hl, "#ffffff", .15))],
               lightcolor=[("pressed", mix(hl, "#000000", .2)), ("active", mix(hl, "#ffffff", .15))],
               darkcolor=[("pressed", mix(hl, "#000000", .2)), ("active", mix(hl, "#ffffff", .15))],
               foreground=[("disabled", p["inactive"])])

        # text inputs
        field = dict(fieldbackground=p["view"], foreground=p["text"], bordercolor=border,
                     lightcolor=border, darkcolor=border, padding=(px(6), px(4)),
                     insertcolor=p["text"], arrowcolor=p["text"], arrowsize=px(16), font=f["base"],
                     background=p["button"])
        focus = dict(bordercolor=[("focus", hl)], lightcolor=[("focus", hl)], darkcolor=[("focus", hl)])
        for name in ("TEntry", "TSpinbox", "TCombobox"):
            st.configure(name, **field)
            st.map(name, **focus)
        st.map("TCombobox", fieldbackground=[("readonly", p["view"])],
               foreground=[("readonly", p["text"])],
               selectbackground=[("readonly", p["view"])],
               selectforeground=[("readonly", p["text"])],
               background=[("active", hover_bg)], **focus)
        st.map("TSpinbox", background=[("active", hover_bg)], **focus)
        root.option_add("*TCombobox*Listbox.background", p["view"])
        root.option_add("*TCombobox*Listbox.foreground", p["text"])
        root.option_add("*TCombobox*Listbox.selectBackground", hl)
        root.option_add("*TCombobox*Listbox.selectForeground", p["sel_text"])
        root.option_add("*TCombobox*Listbox.font", f["base"])
        root.option_add("*TCombobox*Listbox.borderWidth", 0)
        root.option_add("*TCombobox*Listbox.highlightThickness", 0)

        # slider
        st.configure("Horizontal.TScale", background=hl, troughcolor=mix(p["window"], p["text"], .16),
                     bordercolor=border, lightcolor=p["button"], darkcolor=p["button"],
                     sliderlength=px(18), sliderthickness=px(18))
        st.map("Horizontal.TScale", background=[("active", hl)])

        # thin Breeze-style scrollbar (no arrows)
        thumb = mix(p["window"], p["text"], .3)
        st.layout("Vertical.TScrollbar", [("Vertical.Scrollbar.trough", {
            "sticky": "ns", "children": [("Vertical.Scrollbar.thumb", {"expand": "1", "sticky": "nswe"})]})])
        st.configure("Vertical.TScrollbar", background=thumb, troughcolor=p["window"],
                     bordercolor=p["window"], lightcolor=thumb, darkcolor=thumb,
                     arrowsize=px(12), gripcount=0, width=px(12))
        st.map("Vertical.TScrollbar", background=[("pressed", hl), ("active", mix(thumb, hl, .6))],
               lightcolor=[("pressed", hl), ("active", mix(thumb, hl, .6))],
               darkcolor=[("pressed", hl), ("active", mix(thumb, hl, .6))])

        # list view + progress bar (model download dialog)
        st.configure("Treeview", background=p["view"], fieldbackground=p["view"],
                     foreground=p["text"], rowheight=px(26), bordercolor=border,
                     lightcolor=p["view"], darkcolor=p["view"], font=f["base"])
        st.map("Treeview", background=[("selected", hl)], foreground=[("selected", p["sel_text"])])
        st.configure("Treeview.Heading", background=p["button"], foreground=p["text"],
                     font=f["bold"], relief="flat", padding=(px(6), px(4)),
                     bordercolor=border, lightcolor=p["button"], darkcolor=p["button"])
        st.map("Treeview.Heading", background=[("active", hover_bg)])
        st.configure("Horizontal.TProgressbar", background=hl, troughcolor=mix(p["window"], p["text"], .16),
                     bordercolor=border, lightcolor=hl, darkcolor=hl, thickness=px(10))

        self._checkbutton(st)

    def _checkbutton(self, st):
        p, px = self.p, self.px
        if Image is None:
            st.configure("TCheckbutton", indicatorbackground=p["view"],
                         indicatorforeground=p["sel_text"], padding=px(3))
            st.map("TCheckbutton", indicatorbackground=[("selected", p["highlight"])])
            return
        n = px(18)
        hl, border, view = p["highlight"], p["border"], p["view"]
        dis = mix(p["window"], p["text"], .12)
        off = self._box(n, view, border)
        off_h = self._box(n, view, hl)
        on = self._box(n, hl, hl, p["sel_text"])
        on_h = self._box(n, mix(hl, "#ffffff", .15), hl, p["sel_text"])
        off_d = self._box(n, p["window"], dis)
        on_d = self._box(n, dis, dis, p["window"])
        Theme._serial += 1                       # a theme can't redefine an element: new name per build
        name = f"Breeze.checkbox{Theme._serial}"
        st.element_create(name, "image", off,
                          ("disabled", "selected", on_d), ("disabled", off_d),
                          ("selected", "active", on_h), ("selected", on),
                          ("active", off_h), ("focus", off_h))
        st.layout("TCheckbutton", [("Checkbutton.padding", {"sticky": "nswe", "children": [
            (name, {"side": "left", "sticky": ""}),
            ("Checkbutton.focus", {"side": "left", "sticky": "w", "children": [
                ("Checkbutton.label", {"sticky": "nswe"})]})]})])
        st.configure("TCheckbutton", padding=(px(2), px(3)))
