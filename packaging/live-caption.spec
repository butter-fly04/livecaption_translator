# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller recipe for Live Caption (onedir).

Build from the project root with:

    pyinstaller packaging/live-caption.spec

The AppImage wrapper copies the resulting ``dist/live-caption`` folder into
``AppDir/usr/bin/live-caption``.  Do not switch to onefile: an AppImage is
already a single read-only file, and onefile would unpack the whole runtime to
/tmp on every launch.
"""

import os

from PyInstaller.utils.hooks import collect_all, collect_data_files

project_root = os.path.abspath(os.path.join(SPECPATH, os.pardir))
src_root = os.path.join(project_root, "src")

# sherpa-onnx ships native libraries (libonnxruntime.so, libsherpa-onnx-*.so)
# and a package-local lib directory.  collect_all keeps the layout its rpath
# expects.  Check with `ldd` after building.
sherpa_datas, sherpa_binaries, sherpa_hiddenimports = collect_all("sherpa_onnx")

# caption_tray is imported lazily inside a method, and Pillow is imported inside
# caption_tray._render_icon(), so neither is found by static analysis.
hiddenimports = sherpa_hiddenimports + [
    "livecaption.caption_tray",
    "dbus_next",
    "dbus_next.aio",
    "dbus_next.service",
    "pulsectl",
    "PIL",
    "PIL.Image",
    "PIL.ImageDraw",
    "PIL.ImageFont",
]

datas = sherpa_datas + collect_data_files("certifi")
binaries = sherpa_binaries

a = Analysis(
    [os.path.join(src_root, "livecaption", "__main__.py")],
    pathex=[src_root],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        "pytest",
        "tkinter.test",
        "numpy.tests",
        "numpy.f2py.tests",
        "pydoc_data",
    ],
    noarchive=False,
    optimize=0,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="live-caption",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="live-caption",
)
