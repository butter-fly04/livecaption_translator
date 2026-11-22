#!/usr/bin/env bash
#
# Build a Live Caption AppImage with PyInstaller (onedir) + appimagetool.
#
#   PYTHON=.venv/bin/python packaging/build-appimage.sh
#
# The Python used must already have the packages in requirements.txt plus
# PyInstaller.  Building with the host Python is the quick local path; for a
# reproducible artifact use packaging/build-in-container.sh (or the CI
# workflow), which builds on Ubuntu 24.04.
#
# Environment:
#   PYTHON               Python interpreter (default: python3)
#   VERSION              version string (default: git describe, else 0.0.0)
#   SUFFIX               artifact suffix, e.g. ubuntu24.04 (default: none)
#   APPIMAGETOOL         path to appimagetool (default: downloaded to .cache/)
#   APPIMAGE_UPDATE_INFO appimagetool -u value (e.g. gh-releases-zsync|...)
#   APPIMAGE_UPDATE_URL  URL embedded in the generated .zsync file
#   OUTDIR               output directory (default: <project>/dist)
#   CACHE                appimagetool cache (default: <project>/packaging/.cache)
#
set -euo pipefail

ROOT="$(cd "$(dirname "$(readlink -f "$0")")/.." && pwd)"
SRC_DIR="$ROOT/src"
PYTHON="${PYTHON:-python3}"
BUILD_DIR="${BUILD_DIR:-$ROOT/build}"
# Deliberately not called APPDIR: when this script itself runs inside an
# AppImage (including some CI/editor environments), $APPDIR points at the
# read-only mount and must never be used as an output directory.
APPDIR_BUILD="${APPDIR_BUILD:-$BUILD_DIR/AppDir}"
PYINSTALLER_DIST="$BUILD_DIR/pyinstaller-dist"
OUTDIR="${OUTDIR:-$ROOT/dist}"
CACHE="${CACHE:-$ROOT/packaging/.cache}"
VERSION="${VERSION:-$(git -C "$ROOT" describe --tags --always 2>/dev/null || true)}"
if [ -z "$VERSION" ]; then
    VERSION="$(sed -n 's/^__version__ = "\(.*\)"/\1/p' "$SRC_DIR/livecaption/__init__.py" 2>/dev/null | head -n1 || true)"
fi
VERSION="${VERSION:-0.0.0}"
VERSION="${VERSION#v}"
VERSION="${VERSION//[^A-Za-z0-9._-]/-}"
SUFFIX="${SUFFIX:-}"
SUFFIX="${SUFFIX//[^A-Za-z0-9._-]/-}"
OUT="$OUTDIR/LiveCaption-${VERSION}${SUFFIX:+-$SUFFIX}-x86_64.AppImage"

log() { printf '\n==> %s\n' "$*"; }

if [ ! -f "$SRC_DIR/livecaption/live_caption.py" ]; then
    echo "Source package not found at $SRC_DIR/livecaption" >&2
    exit 1
fi

log "Checking build dependencies with $PYTHON"
"$PYTHON" - <<'PY'
import os
import sys
import sysconfig

missing = []
for name in ("PyInstaller", "numpy", "sherpa_onnx", "pulsectl", "requests",
             "dbus_next", "PIL", "tkinter"):
    try:
        __import__(name)
    except Exception as e:
        missing.append(f"{name}: {e}")

# PyInstaller needs the interpreter's shared library.  Debian/Ubuntu keep it in
# a separate package (libpython3-dev / libpython3.12-dev).
if not sysconfig.get_config_var("Py_ENABLE_SHARED"):
    missing.append("Python was built without a shared library (--enable-shared)")
else:
    lib = sysconfig.get_config_var("LDLIBRARY")
    libdir = sysconfig.get_config_var("LIBDIR") or ""
    # Ubuntu ships both libpython3.12.so.1.0 (runtime) and .so (dev).
    candidates = [lib, lib + ".1.0"] if lib else []
    if not any(os.path.exists(os.path.join(libdir, c)) for c in candidates):
        missing.append(f"Python shared library {lib or '?'} not found in {libdir} "
                       "(install libpython3-dev / python3-devel)")

if missing:
    print("Missing build dependencies:\n  " + "\n  ".join(missing))
    print("\nInstall them with:  pip install -r requirements-build.txt")
    sys.exit(1)
print("ok")
PY

log "Cleaning build folders"
case "$BUILD_DIR" in
    ""|"/"|"$HOME"|"$HOME/"|"$ROOT")
        echo "Refusing to delete BUILD_DIR='$BUILD_DIR'" >&2
        exit 1
        ;;
esac
rm -rf "$BUILD_DIR" "$APPDIR_BUILD"
mkdir -p "$BUILD_DIR" "$OUTDIR"

log "Running PyInstaller"
"$PYTHON" -m PyInstaller \
    --noconfirm --clean --log-level WARN \
    --distpath "$PYINSTALLER_DIST" \
    --workpath "$BUILD_DIR/pyinstaller" \
    "$ROOT/packaging/live-caption.spec"

if [ ! -x "$PYINSTALLER_DIST/live-caption/live-caption" ]; then
    echo "PyInstaller did not produce build/pyinstaller-dist/live-caption/live-caption" >&2
    exit 1
fi

log "Assembling AppDir"
install -d "$APPDIR_BUILD/usr/bin"
cp -a "$PYINSTALLER_DIST/live-caption" "$APPDIR_BUILD/usr/bin/live-caption"
install -Dm755 "$ROOT/packaging/AppRun" "$APPDIR_BUILD/AppRun"
install -Dm644 "$ROOT/packaging/live-caption.desktop" "$APPDIR_BUILD/live-caption.desktop"
install -Dm644 "$ROOT/packaging/live-caption.desktop" \
    "$APPDIR_BUILD/usr/share/applications/live-caption.desktop"
install -Dm644 "$ROOT/packaging/live-caption.appdata.xml" \
    "$APPDIR_BUILD/usr/share/metainfo/live-caption.appdata.xml"
"$PYTHON" "$ROOT/packaging/make-icon.py" "$APPDIR_BUILD"

if command -v desktop-file-validate >/dev/null 2>&1; then
    desktop-file-validate "$APPDIR_BUILD/live-caption.desktop" || true
fi

log "Fetching appimagetool (if needed)"
mkdir -p "$CACHE"
TOOL="${APPIMAGETOOL:-$CACHE/appimagetool-x86_64.AppImage}"
if [ ! -x "$TOOL" ]; then
    curl -fL --retry 3 -o "$TOOL" \
        "https://github.com/AppImage/AppImageKit/releases/download/continuous/appimagetool-x86_64.AppImage"
    chmod +x "$TOOL"
fi

# appimagetool is itself an AppImage, so running it normally needs FUSE - which
# is missing in containers and on many newer distros.  "--appimage-version" is
# handled by the runtime before mounting, so it cannot be used to test whether
# mounting works.  Extract once instead: extraction never needs FUSE and the
# extracted AppRun behaves identically.
TOOL_EXTRACTED="$CACHE/appimagetool-extracted"
if "$TOOL" --appimage-version >/dev/null 2>&1; then
    if [ ! -x "$TOOL_EXTRACTED/AppRun" ]; then
        rm -rf "$CACHE/squashfs-root" "$TOOL_EXTRACTED"
        ( cd "$CACHE" && "$TOOL" --appimage-extract >/dev/null )
        mv "$CACHE/squashfs-root" "$TOOL_EXTRACTED"
    fi
    run_appimagetool() { "$TOOL_EXTRACTED/AppRun" "$@"; }
else
    # A distro-provided, non-AppImage appimagetool.
    run_appimagetool() { "$TOOL" "$@"; }
fi

log "Building AppImage"
# AppStream validation needs a real homepage/licence and is stricter than the
# metadata we ship, so it is opt-in.  The .appdata.xml is still installed.
TOOL_ARGS=(-n)
if [ "${APPIMAGE_APPSTREAM:-0}" = "1" ]; then
    TOOL_ARGS=()
fi
if [ -n "${APPIMAGE_UPDATE_INFO:-}" ]; then
    TOOL_ARGS+=(-u "$APPIMAGE_UPDATE_INFO")
fi
# Unlink an existing artifact first.  If it is currently running, mksquashfs
# would otherwise fail with "Text file busy"; unlinking lets the old process
# keep its inode while the new file is written.
rm -f "$OUT" "$OUT.sha256" "$OUT.zsync"
ARCH=x86_64 VERSION="$VERSION" run_appimagetool "${TOOL_ARGS[@]}" "$APPDIR_BUILD" "$OUT"

if command -v zsyncmake >/dev/null 2>&1; then
    log "Creating zsync delta file"
    if [ -n "${APPIMAGE_UPDATE_URL:-}" ]; then
        zsyncmake -u "$APPIMAGE_UPDATE_URL" -o "$OUT.zsync" "$OUT"
    else
        zsyncmake -o "$OUT.zsync" "$OUT"
    fi
elif [ -n "${APPIMAGE_UPDATE_INFO:-}" ]; then
    echo "Warning: APPIMAGE_UPDATE_INFO is set but zsyncmake is not installed." >&2
fi

( cd "$OUTDIR" && sha256sum "$(basename "$OUT")" > "$(basename "$OUT").sha256" )

log "Done"
ls -lh "$OUT" "$OUT.sha256"
if [ -f "$OUT.zsync" ]; then
    ls -lh "$OUT.zsync"
fi
echo
echo "Run it with:  $OUT"
echo "If FUSE is unavailable:  $OUT --appimage-extract-and-run"
