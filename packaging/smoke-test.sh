#!/usr/bin/env bash
#
# Smoke-test a built AppImage without touching the real user config/models.
#
#   packaging/smoke-test.sh dist/LiveCaption-*.AppImage
#
# It checks that the bundle starts, that its Python/sherpa/Tk payload is
# loadable, that the XDG folders are writable, and that the no-FUSE fallback
# works.  Audio and display are reported as warnings by --self-test, so this
# is safe in a headless CI container.
#
set -euo pipefail

APP="${1:?usage: smoke-test.sh /path/to/LiveCaption-*.AppImage}"
APP="$(readlink -f "$APP")"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

export LIVE_CAPTION_CONFIG_DIR="$TMP/config"
export LIVE_CAPTION_DATA_DIR="$TMP/data"

# GitHub runners and minimal containers do not always ship libfuse2, in which
# case the AppImage cannot mount itself.  Detect that once and fall back to the
# built-in extract-and-run mode, which the app supports anyway.
RUN=("$APP")
if ! "$APP" --version >/dev/null 2>&1; then
    echo "==> AppImage could not run directly (no FUSE?); using extract-and-run"
    RUN=("$APP" --appimage-extract-and-run)
fi

echo "==> version"
"${RUN[@]}" --version

echo "==> self-test"
"${RUN[@]}" --self-test

echo "==> no-FUSE fallback (--appimage-extract-and-run)"
"$APP" --appimage-extract-and-run --version

echo "==> fresh XDG folders"
test -d "$TMP/config"
test -d "$TMP/data/models"
echo "ok"

echo "==> re-running self-test with an existing config"
printf '{}\n' > "$TMP/config/config.json"
"${RUN[@]}" --self-test > "$TMP/self-test-2.txt"
grep -q "Models folder" "$TMP/self-test-2.txt"

echo "Smoke test passed."
