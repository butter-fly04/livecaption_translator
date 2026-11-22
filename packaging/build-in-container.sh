#!/usr/bin/env bash
#
# Build the AppImage in a clean container.  This keeps the artifact independent
# of the host's Python/glibc and is the path used for releases.
#
#   packaging/build-in-container.sh            # Ubuntu 24.04, docker
#   CONTAINER=podman packaging/build-in-container.sh
#
#   # Fedora variant
#   DOCKERFILE=Dockerfile.fedora IMAGE=live-caption-build:fedora \
#       SUFFIX=fedora packaging/build-in-container.sh
#
# The artifact is named ...-ubuntu24.04-... by default so it can sit next to the
# Fedora build in dist/.  Set SUFFIX= to drop the suffix.
#
# Environment:
#   CONTAINER   docker or podman (default: docker)
#   IMAGE       image tag to build/use (default: live-caption-build:24.04)
#   DOCKERFILE  Dockerfile inside packaging/ (default: Dockerfile)
#   SUFFIX      filename suffix (default: ubuntu24.04)
#   SMOKE_TEST  set to 1 to smoke-test the artifact inside the container
#
set -euo pipefail

ROOT="$(cd "$(dirname "$(readlink -f "$0")")/.." && pwd)"
CONTAINER="${CONTAINER:-docker}"
IMAGE="${IMAGE:-live-caption-build:24.04}"
DOCKERFILE="${DOCKERFILE:-Dockerfile}"
SUFFIX="${SUFFIX-ubuntu24.04}"

if ! command -v "$CONTAINER" >/dev/null 2>&1; then
    echo "$CONTAINER not found; install Docker or Podman, or build locally with" >&2
    echo "  PYTHON=.venv/bin/python packaging/build-appimage.sh" >&2
    exit 1
fi

echo "==> Building $IMAGE from packaging/$DOCKERFILE"
"$CONTAINER" build -f "$ROOT/packaging/$DOCKERFILE" -t "$IMAGE" "$ROOT/packaging"

# Resolve the version on the host: the container mount may not include the
# repository's .git directory, and git describe needs the tags.
VERSION="${VERSION:-$(git -C "$ROOT" describe --tags --always 2>/dev/null || true)}"
if [ -z "$VERSION" ]; then
    VERSION="$(sed -n 's/^__version__ = "\(.*\)"/\1/p' "$ROOT/src/livecaption/__init__.py" 2>/dev/null | head -n1 || true)"
fi
VERSION="${VERSION:-0.0.0}"

echo "==> Building AppImage in $IMAGE (version $VERSION${SUFFIX:+, suffix $SUFFIX})"
RUN_ARGS=(
    --rm
    -e HOME=/tmp
    -e VERSION="$VERSION"
    -e SUFFIX="$SUFFIX"
    -e APPIMAGE_UPDATE_INFO="${APPIMAGE_UPDATE_INFO:-}"
    -e APPIMAGE_UPDATE_URL="${APPIMAGE_UPDATE_URL:-}"
    -e BUILD_DIR=/tmp/live-caption-build
    -e CACHE=/tmp/live-caption-cache
    -e SMOKE_TEST="${SMOKE_TEST:-0}"
    -w /src
)

# Rootless Podman maps a requested uid through subuids, so --user alone cannot
# read the mounted source.  keep-id makes the container user match the host
# user; :Z handles SELinux labels on enforcing hosts.
case "$(basename "$CONTAINER")" in
    podman)
        RUN_ARGS+=(--userns=keep-id -v "$ROOT:/src:Z")
        ;;
    *)
        RUN_ARGS+=(--user "$(id -u):$(id -g)" -v "$ROOT:/src")
        ;;
esac

"$CONTAINER" run "${RUN_ARGS[@]}" "$IMAGE" \
    bash -lc '
        set -euo pipefail
        python3 -m venv /tmp/venv
        . /tmp/venv/bin/activate
        pip install --retries 5 --timeout 60 -r requirements-build.txt
        PYTHON=/tmp/venv/bin/python packaging/build-appimage.sh

        if [ "${SMOKE_TEST:-0}" = "1" ]; then
            app="$(find dist -maxdepth 1 \
                -name "LiveCaption-*${SUFFIX:+-$SUFFIX}-x86_64.AppImage" \
                -print -quit)"
            if [ -z "$app" ]; then
                echo "No AppImage found for smoke test" >&2
                exit 1
            fi
            packaging/smoke-test.sh "$app"
        fi
    '

echo "Artifacts are in $ROOT/dist/"
