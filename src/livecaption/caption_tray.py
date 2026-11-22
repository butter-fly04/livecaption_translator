#!/usr/bin/env python3
"""
System tray (StatusNotifierItem / DBusMenu) support for the Live Caption app.

Plasma 6 — especially on Wayland, where the legacy XEmbed systray protocol is
gone — only shows tray icons that are registered over DBus as a
StatusNotifierItem (SNI). This module implements that protocol, plus the
com.canonical.dbusmenu interface Plasma uses to render the icon's context
menu *inside plasmashell*, using dbus-next. No extra GUI toolkit is involved;
the menu is pure data provided by the app, and Plasma draws it.

Interaction model
-----------------
* Left-click (Activate)   -> on_event("toggle_visible", (x, y))  [app hides
  the window + pauses audio, or reverses that]
* Middle-click (SecondaryActivate) -> same toggle.
* Right-click             -> plasmashell imports our DBusMenu tree and shows
  it, so the tray context menu is native Plasma UI. The menu tree is fetched
  from `menu_source` (a plain-data snapshot; must not touch Tk) each time it
  is (re)fetched.
* Fallback: if a host calls SNI.ContextMenu(x, y) directly, we forward
  ("context_menu", (x, y)) so the app can post its Tk menu there instead.

Threading
---------
DBus runs on a private asyncio loop in a background thread. All events are
handed to the app via `on_event`, which must be thread-safe (CaptionWindow
pushes them onto a queue drained on the Tk mainloop). Calls INTO the tray
(refresh / set_paused / stop) are thread-safe from any thread.

Registration handles plasmashell (re)starts: it retries registering until
the org.kde.StatusNotifierWatcher appears, and re-registers if the watcher's
bus name changes owner (Plasma restarted).
"""

from __future__ import annotations

import asyncio
import glob
import os
import queue
import sys
import threading

from dbus_next import Variant
from dbus_next.aio import MessageBus
from dbus_next.service import PropertyAccess, ServiceInterface, dbus_property, method, signal

# NOTE: if the icon only shows in the tray's "hidden icons" popup, it needs to
# be dragged onto the panel once (Plasma remembers per-service visibility).
BUS_NAME = "org.livecaption.tray"
WATCHER_NAME = "org.kde.StatusNotifierWatcher"
WATCHER_PATH = "/StatusNotifierWatcher"
WATCHER_IFACE = "org.kde.StatusNotifierWatcher"
ITEM_PATH = "/StatusNotifierItem"
MENU_PATH = "/StatusNotifierMenu"
MENU_IFACE = "com.canonical.dbusmenu"
SNI_IFACE = "org.kde.StatusNotifierItem"

ICON_SIZES = (16, 22, 24, 32, 48)

_FONT_CANDIDATES = [
    "/usr/share/fonts/liberation-sans-fonts/LiberationSans-Bold.ttf",
    "/usr/share/fonts/abattis-cantarell-fonts/Cantarell-Bold.otf",
    "/usr/share/fonts/adobe-source-code-pro-fonts/SourceCodePro-Bold.otf",
    "/usr/share/fonts/google-droid-sans-fonts/DroidSans-Bold.ttf",
]

_LAYOUT_VARIANT_SIG = "(ia{sv}av)"


def _find_bold_font():
    for path in _FONT_CANDIDATES:
        if os.path.exists(path):
            return path
    for pattern in ("/usr/share/fonts/**/*Sans*Bold*.ttf",
                    "/usr/share/fonts/**/*Bold*.otf",
                    "/usr/share/fonts/**/*Bold*.ttf"):
        hits = sorted(glob.glob(pattern, recursive=True))
        if hits:
            return hits[0]
    return None


def _render_icon(size, paused):
    """Speech-bubble icon; 'CC' when running, pause bars when paused.

    Returns raw ARGB32 pixel ints (B,G,R,A order per pixel, as the SNI spec's
    a(iiay) pixmap format expects).
    """
    from PIL import Image, ImageDraw, ImageFont
    import numpy as np

    ss = 4
    S = size * ss
    img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)

    lw = max(ss, S // 34)
    m = int(S * 0.10)
    radius = int(S * 0.20)
    tail_drop = int(S * 0.14)
    box = (m, m, S - m, S - m - tail_drop)

    if paused:
        # Opaque bubble so plasmashell doesn't apply its translucent-icon
        # treatment; red bars read as "stopped" at a glance.
        fill = (238, 238, 238, 245)
        border = (196, 60, 60, 255)
        text = None
    else:
        fill = (238, 238, 238, 245)
        border = (110, 110, 110, 235)
        text = (25, 25, 25, 255)

    d.rounded_rectangle(box, radius=radius, fill=fill, outline=border, width=lw)

    # Speech-bubble tail (bottom left) — fill only; at tray sizes the seam
    # isn't discernible.
    tx = box[0] + int(S * 0.22)
    tw = int(S * 0.20)
    d.polygon([(tx, box[3] - lw), (tx + tw, box[3] - lw), (tx, box[3] + tail_drop)],
              fill=fill, outline=border, width=lw)

    cx = (box[0] + box[2]) // 2
    cy = (box[1] + box[3]) // 2

    if text:
        try:
            font = ImageFont.truetype(_find_bold_font(), int(S * 0.46))
        except Exception:
            font = ImageFont.load_default(size=int(S * 0.46))
        d.text((cx, cy), "CC", font=font, fill=text, anchor="mm")
    else:
        bw = max(ss * 2, int(S * 0.09))
        gap = max(ss * 2, int(S * 0.13))
        bh = int(S * 0.34)
        for dx in (-gap // 2 - bw // 2, gap // 2 + bw // 2):
            d.rounded_rectangle(
                (cx + dx - bw // 2, cy - bh // 2, cx + dx + bw // 2, cy + bh // 2),
                radius=max(1, bw // 4), fill=(196, 40, 40, 255), outline=None)

    img = img.resize((size, size), Image.Resampling.LANCZOS)
    arr = np.asarray(img, dtype=np.uint8)
    arr = arr[:, :, [3, 0, 1, 2]]  # RGBA -> A,R,G,B — the spec sends ARGB32
    # data in NETWORK byte order (Plasma ntohl()s each 32-bit word before
    # treating it as Format_ARGB32), so the first byte of every pixel is A.
    return arr.tobytes()


class _IconProvider:
    def __init__(self):
        self._paused = False
        self._cache = {}

    def set_paused(self, paused):
        self._paused = bool(paused)

    def pixmap(self):
        out = []
        for size in ICON_SIZES:
            key = (size, self._paused)
            data = self._cache.get(key)
            if data is None:
                data = _render_icon(size, self._paused)
                self._cache[key] = data
            out.append([size, size, data])
        return out


class _ItemInterface(ServiceInterface):
    """org.kde.StatusNotifierItem: the tray icon itself."""

    def __init__(self, ctrl, icon_provider):
        super().__init__(SNI_IFACE)
        self._ctrl = ctrl
        self._icon = icon_provider

    # ---- methods the host may call ----
    @method("Activate")
    def Activate(self, x: "i", y: "i"):
        self._ctrl.on_event("toggle_visible", (x, y))

    @method("SecondaryActivate")
    def SecondaryActivate(self, x: "i", y: "i"):
        self._ctrl.on_event("toggle_visible", (x, y))

    # Fallback path: hosts that don't use the Menu property still pop a menu
    # this way; we let the app show its Tk menu at that position.
    @method("ContextMenu")
    def ContextMenu(self, x: "i", y: "i"):
        self._ctrl.on_event("context_menu", (x, y))

    @method("Scroll")
    def Scroll(self, delta: "i", orientation: "s"):
        pass  # not mapped to anything; reserved

    # ---- properties ----
    @dbus_property(access=PropertyAccess.READ)
    def Category(self) -> "s":
        return "ApplicationStatus"

    @dbus_property(access=PropertyAccess.READ)
    def Id(self) -> "s":
        return "Live Captions"

    @dbus_property(access=PropertyAccess.READ)
    def Title(self) -> "s":
        return "Live Captions"

    @dbus_property(access=PropertyAccess.READ)
    def Status(self) -> "s":
        return "Active"

    @dbus_property(access=PropertyAccess.READ)
    def WindowId(self) -> "u":
        return 0

    @dbus_property(access=PropertyAccess.READ)
    def IconName(self) -> "s":
        return ""

    @dbus_property(access=PropertyAccess.READ)
    def IconPixmap(self) -> "a(iiay)":
        return self._icon.pixmap()

    @dbus_property(access=PropertyAccess.READ)
    def ItemIsMenu(self) -> "b":
        # False: the host activates (our toggle) on LEFT click and uses the
        # Menu for right click. True would make BOTH clicks open just the menu.
        return False

    @dbus_property(access=PropertyAccess.READ)
    def Menu(self) -> "s":
        return MENU_PATH

    @dbus_property(access=PropertyAccess.READ)
    def ToolTip(self) -> "(s(iiay)ss)":
        small = self._icon.pixmap()[1]  # (22,22,pixmap) entry — tooltip takes
        # a single (iiay) struct, NOT an array like the IconPixmap property.
        return ["", [small[0], small[1], small[2]], "Live Captions",
                "Left-click: hide + pause audio (click again to restore)\n"
                "Right-click: menu"]

    @signal("NewIcon")
    def new_icon(self):
        return None

    def icon_source_changed(self):
        """Called (on the loop thread) when the icon pixmap changed."""
        self.emit_properties_changed({"IconPixmap": self._icon.pixmap()})
        self.new_icon()


class _MenuInterface(ServiceInterface):
    """com.canonical.dbusmenu: the tree plasmashell renders as the context menu."""

    def __init__(self, ctrl):
        super().__init__(MENU_IFACE)
        self._ctrl = ctrl
        self._revision = 0
        self._dirty_all = True
        self._dirty = set()
        self._nodes = {}  # id -> node dict (latest built tree; 0 = root)

    # ---- properties ----
    @dbus_property(access=PropertyAccess.READ)
    def Version(self) -> "u":
        return 3

    @dbus_property(access=PropertyAccess.READ)
    def TextDirection(self) -> "s":
        return "ltr"

    @dbus_property(access=PropertyAccess.READ)
    def Status(self) -> "s":
        return "normal"

    @dbus_property(access=PropertyAccess.READ)
    def IconThemePath(self) -> "as":
        return []

    # ---- methods ----
    # NOTE: dbus-next requires multi-out methods to return a *list* whose
    # items match each out type in order (GetLayout, EventGroup below).
    @method("GetLayout")
    def GetLayout(self, parent_id: "i", depth: "i", property_names: "as") -> "u(ia{sv}av)":
        self._build()
        parent_id = int(parent_id)
        root = self._nodes[0]
        if parent_id == 0:
            struct = self._serialize_root(depth)
            self._dirty_all = False
            self._dirty.clear()
        else:
            node = self._nodes.get(parent_id)
            struct = self._serialize(node, depth) if node else [0, {}, []]
            self._dirty.discard(parent_id)
        return [self._revision, struct]

    @method("GetGroupProperties")
    def GetGroupProperties(self, ids: "ai", property_names: "as") -> "a(iia{sv})":
        out = []
        for i in ids:
            node = self._nodes.get(int(i))
            if node:
                out.append([int(i), 0, self._item_props(node)])
        return out

    @method("GetItemProperties")
    def GetItemProperties(self, item_id: "i", property_names: "as") -> "a{sv}":
        node = self._nodes.get(int(item_id))
        return self._item_props(node) if node else {}

    @method("GetPropertyNames")
    def GetPropertyNames(self) -> "as":
        return ["label", "type", "enabled", "visible", "checked", "check-type",
                "children-display", "shortcut", "shortcut-type", "icon-name"]

    @method("Event")
    def Event(self, item_id: "i", event_id: "s", data: "v", timestamp: "u"):
        if event_id == "clicked":
            node = self._nodes.get(int(item_id))
            action = node and node.get("action")
            if action:
                kind, payload = action
                self._ctrl.on_event(kind, payload)

    @method("EventGroup")
    def EventGroup(self, events: "a(isvu)") -> "ai":
        failed = []
        for item_id, event_id, data, timestamp in events:
            try:
                self.Event(int(item_id), event_id, data, int(timestamp))
            except Exception:
                failed.append(int(item_id))
        return failed

    @method("AboutToShow")
    def AboutToShow(self, item_id: "i") -> "b":
        self._build()
        if int(item_id) == 0:
            return bool(self._dirty_all)
        return bool(self._dirty_all) or int(item_id) in self._dirty

    # ---- signals ----
    @signal("LayoutUpdated")
    def layout_updated(self) -> "ui":
        return [self._revision, 0]

    def refresh(self):
        """Called (on the loop thread) when menu-affecting state changed.

        Rebuilds the tree (so click events resolve immediately even before
        the host refetches) and tells Plasma to refetch the root layout."""
        self._build()
        self._revision += 1
        self._dirty_all = True
        self.layout_updated()

    # ---- tree building ----
    def _build(self):
        """Materialize the menu spec (plain data from the app) into a tree.

        Called on the loop thread; `menu_source` must therefore not touch Tk
        (it only reads plain Python attributes). IDs are assigned
        deterministically in DFS order, so they are stable across rebuilds
        whenever the menu structure itself doesn't change.
        """
        root = {"id": 0, "type": "root", "children": []}
        nodes = {0: root}
        next_id = 1

        def expand(parent, spec):
            nonlocal next_id
            kids = []
            for item in spec or []:
                if item.get("separator"):
                    nid = next_id
                    next_id += 1
                    node = {"id": nid, "type": "separator"}
                else:
                    nid = next_id
                    next_id += 1
                    node = {
                        "id": nid, "type": "standard",
                        "label": item.get("label", ""),
                        "action": item.get("action"),
                        "check_type": item.get("check_type"),
                        "checked": bool(item.get("checked", False)),
                    }
                nodes[nid] = node
                kids.append(node)
                sub = item.get("submenu")
                if sub:
                    expand(node, sub)
                node.setdefault("children", [])
            parent["children"] = kids

        expand(root, self._ctrl.menu_source())
        self._nodes = nodes
        return root

    def _serialize_root(self, depth):
        root = self._build()
        child_variants = []
        if depth != 0:
            child_variants = [
                Variant(_LAYOUT_VARIANT_SIG, self._serialize(k, depth - 1 if depth > 0 else depth))
                for k in root["children"]
            ]
        return [0, {}, child_variants]

    def _serialize(self, node, depth):
        if node is None or node["type"] == "separator":
            return [node["id"] if node else 0,
                    {"type": Variant("s", "separator"), "visible": Variant("b", True)},
                    []]
        props = self._item_props(node)
        child_variants = []
        kids = node.get("children")
        if kids:
            props["children-display"] = Variant("s", "submenu")
            if depth != 0:
                child_variants = [
                    Variant(_LAYOUT_VARIANT_SIG, self._serialize(k, depth - 1 if depth > 0 else depth))
                    for k in kids
                ]
        return [node["id"], props, child_variants]

    def _item_props(self, node):
        if node is None or node["type"] == "separator":
            return {"type": Variant("s", "separator"), "visible": Variant("b", True)}
        props = {
            "type": Variant("s", "standard"),
            "label": Variant("s", node.get("label", "")),
            "enabled": Variant("b", True),
            "visible": Variant("b", True),
        }
        if node.get("check_type"):
            props["check-type"] = Variant("s", node["check_type"])
            # Per the dbusmenu spec, "checked" is type "u" (0/1), which hosts
            # also accept as a bool.
            props["checked"] = Variant("u", 1 if node.get("checked") else 0)
        return props


class TrayController:
    """Public API. All methods are thread-safe.

    `menu_source`:   callable -> list of plain-dict items (may be called from
                     the DBus thread; must not touch Tk). Item dicts:
                       {"separator": True}
                       {"label": str, "action": (kind, payload)[,
                        "check_type": "radio"|"checkbox", "checked": bool][,
                        "submenu": [...]]}
                     `action` tuples are forwarded verbatim to `on_event`.
    `on_event`:      thread-safe callback invoked as on_event(kind, payload);
                     the app must marshal it onto the Tk thread itself.
    """

    def __init__(self, menu_source, on_event,
                 id_hint="live-captions", title="Live Captions"):
        if not callable(menu_source) or not callable(on_event):
            raise ValueError("menu_source and on_event must be callables")
        self.menu_source = menu_source
        self.on_event = on_event
        self.id_hint = id_hint
        self.title = title

        self._q = queue.Queue()
        self._loop = None
        self._thread = None
        self._running = True
        self._started = False
        self._bus = None
        self._sni = None
        self._menu_if = None
        self._icon = _IconProvider()
        self._registered = False
        self._service = BUS_NAME
        self._tasks = set()

    # ---------- thread-safe public API ----------
    def start(self):
        if self._started:
            return
        self._started = True
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(
            target=self._run_loop, args=(self._loop,), name="caption-tray", daemon=True)
        self._thread.start()

    def refresh(self):
        """Menu-affecting state changed: bump revision -> host refetches."""
        self._q.put(("refresh", None))

    def set_paused(self, paused):
        """Swap the icon to/from its paused variant."""
        self._q.put(("icon", bool(paused)))

    def stop(self):
        if not self._started:
            return
        self._q.put(("stop", None))
        self._thread.join(timeout=5)

    # ---------- internals (loop thread only) ----------
    def _run_loop(self, loop):
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(self._serve())
        except Exception as e:
            print(f"[Tray] Stopped: {e}", file=sys.stderr)
        finally:
            try:
                loop.run_until_complete(loop.shutdown_asyncgens())
            except Exception:
                pass
            loop.close()

    async def _serve(self):
        bus = await MessageBus().connect()
        self._bus = bus

        try:
            await bus.request_name(BUS_NAME)
            self._service = BUS_NAME
        except Exception:
            # Another app instance owns the name; register under the unique
            # name so this instance's icon still shows up.
            self._service = bus.unique_name
            print(f"[Tray] Bus name '{BUS_NAME}' already owned; registering as "
                  f"{bus.unique_name} (are two instances running?)")

        self._sni = _ItemInterface(self, self._icon)
        self._menu_if = _MenuInterface(self)
        bus.export(ITEM_PATH, self._sni)
        bus.export(MENU_PATH, self._menu_if)
        self._menu_if._build()  # eager: click events must resolve before any host fetch

        tasks = {
            "register": asyncio.create_task(self._register_loop()),
            "restart": asyncio.create_task(self._watch_watcher_restarts()),
            "worker": asyncio.create_task(self._worker()),
        }
        self._tasks = set(tasks.values())
        try:
            await tasks["worker"]
        finally:
            for t in self._tasks:
                if not t.done():
                    t.cancel()
            await asyncio.gather(*self._tasks, return_exceptions=True)

    async def _register_loop(self):
        attempt = 0
        while self._running:
            try:
                introspection = await self._bus.introspect(WATCHER_NAME, WATCHER_PATH)
                proxy = self._bus.get_proxy_object(WATCHER_NAME, WATCHER_PATH, introspection)
                watcher = proxy.get_interface(WATCHER_IFACE)
                await watcher.call_register_status_notifier_item(self._service)
                if not self._registered:
                    self._registered = True
                    print(f"[Tray] Registered '{self._service}{ITEM_PATH}' with "
                          f"{WATCHER_NAME}")
                return
            except asyncio.CancelledError:
                raise
            except Exception as e:
                attempt += 1
                if attempt == 1:
                    print(f"[Tray] Waiting for {WATCHER_NAME} ...")
                await asyncio.sleep(min(2 * attempt, 10))

    async def _watch_watcher_restarts(self):
        # If plasmashell restarts, re-register our item.
        try:
            intro = await self._bus.introspect("org.freedesktop.DBus",
                                               "/org/freedesktop/DBus")
            proxy = self._bus.get_proxy_object("org.freedesktop.DBus",
                                               "/org/freedesktop/DBus", intro)
            dbus_iface = proxy.get_interface("org.freedesktop.DBus")
            dbus_iface.on_name_owner_changed(self._on_name_owner_changed)
        except Exception as e:
            print(f"[Tray] Could not watch for plasmashell restarts: {e}")

    def _on_name_owner_changed(self, name, old_owner, new_owner):
        if name == WATCHER_NAME and new_owner and self._bus:
            self._registered = False
            asyncio.get_event_loop().create_task(self._register_loop())

    async def _worker(self):
        loop = asyncio.get_running_loop()
        while True:
            kind, payload = await loop.run_in_executor(None, self._q.get)
            try:
                if kind == "stop":
                    for t in self._tasks:
                        t.cancel()
                    try:
                        await self._bus.disconnect()
                    except Exception:
                        pass
                    return
                elif kind == "refresh":
                    if self._menu_if:
                        self._menu_if.refresh()
                elif kind == "icon":
                    self._icon.set_paused(payload)
                    if self._sni:
                        self._sni.icon_source_changed()
            except Exception as e:
                print(f"[Tray] worker error ({kind}): {e}")


def main():
    """Standalone test: registers a tray icon with a demo menu; prints events."""
    run_state = {"mode": "translation_only", "proto": False}

    def demo_menu():
        return [
            {"label": "Pause Audio", "action": ("toggle_pause", None)},
            {"separator": True},
            {"label": "Increase Font", "action": ("increase_font", None)},
            {"label": "Decrease Font", "action": ("decrease_font", None)},
            {"separator": True},
            {"label": "Display Mode", "submenu": [
                {"label": "Translation Only", "check_type": "radio",
                 "checked": run_state["mode"] == "translation_only",
                 "action": ("display_mode", "translation_only")},
                {"label": "Source + Translation", "check_type": "radio",
                 "checked": run_state["mode"] == "with_transcription",
                 "action": ("display_mode", "with_transcription")},
            ]},
            {"label": "Endpoint Preset", "submenu": [
                {"label": "edited", "check_type": "radio", "checked": True,
                 "action": ("preset", "edited")},
                {"label": "natural", "check_type": "radio", "checked": False,
                 "action": ("preset", "natural")},
            ]},
            {"separator": True},
            {"label": "Exit", "action": ("exit", None)},
        ]

    def on_event(kind, payload):
        print(f"[Test] event: {kind} {payload}", flush=True)
        if kind == "display_mode":
            run_state["mode"] = payload
        elif kind == "toggle_pause":
            ctrl.set_paused(not run_state["proto"])
            run_state["proto"] = not run_state["proto"]
            ctrl.refresh()
        elif kind == "exit":
            ctrl.stop()

    ctrl = TrayController(menu_source=demo_menu, on_event=on_event)
    ctrl.start()
    threading.Event().wait()


if __name__ == "__main__":
    main()
