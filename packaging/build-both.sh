#!/usr/bin/env bash
#
# Build both AppImages and keep them side by side in dist/:
#
#   LiveCaption-<version>-x86_64.AppImage
#       Built with the host Python and Tk.  This is the native build: it uses
#       the host glibc and the host Tk, so it only runs on systems with a glibc
#       at least as new as the build host.  Use it locally.
#
#   LiveCaption-<version>-ubuntu24.04-x86_64.AppImage
#       Built in an Ubuntu 24.04 container, so it needs only glibc 2.39 and
#       runs on Ubuntu 24.04, Linux Mint 22.3 and newer.  This is the one to
#       distribute.
#
#   PYTHON=.venv/bin/python packaging/build-both.sh
#   CONTAINER=podman PYTHON=.venv/bin/python packaging/build-both.sh
#
# Environment:
#   PYTHON      Python for the native build (default: python3)
#   CONTAINER   docker or podman for the Ubuntu build (default: whichever exists)
#   VERSION     override the version string (default: git describe)
#
set -euo pipefail

ROOT="$(cd "$(dirname "$(readlink -f "$0")")/.." && pwd)"
PYTHON="${PYTHON:-python3}"

if [ -z "${CONTAINER:-}" ]; then
    if command -v podman >/dev/null 2>&1; then
        CONTAINER=podman
    elif command -v docker >/dev/null 2>&1; then
        CONTAINER=docker
    else
        echo "Neither podman nor docker found; cannot build the Ubuntu AppImage." >&2
        echo "Build only the native one with: PYTHON=... packaging/build-appimage.sh" >&2
        exit 1
    fi
fi

echo "==> Native host build"
PYTHON="$PYTHON" SUFFIX= VERSION="${VERSION:-}" "$ROOT/packaging/build-appimage.sh"

echo "==> Ubuntu 24.04 compatible build"
CONTAINER="$CONTAINER" SUFFIX=ubuntu24.04 VERSION="${VERSION:-}" \
    "$ROOT/packaging/build-in-container.sh"

echo
echo "Artifacts in $ROOT/dist/:"
ls -lh "$ROOT"/dist/LiveCaption-*.AppImage
