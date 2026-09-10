#!/bin/bash
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP_DIR="$HOME/.local/share/applications"
DESKTOP_FILE="$APP_DIR/livecaption.desktop"

read -p "Remove Python venv? [y/N] " yn
[[ "$yn" == "y" ]] && rm -rf "$DIR/venv"

read -p "Remove VOSK models? [y/N] " yn
[[ "$yn" == "y" ]] && rm -rf "$DIR/models"

read -p "Remove scripts, config, and launcher? [y/N] " yn
if [[ "$yn" == "y" ]]; then
    rm -f "$DIR/live_caption.py" "$DIR/caption_translators.py" "$DIR/launcher.sh" "$DIR/config.conf" "$DIR/update-desktop.sh"
    rm -f "$DESKTOP_FILE"
    if command -v update-desktop-database >/dev/null 2>&1; then
        update-desktop-database "$APP_DIR"
    fi
    echo "Removed desktop entry."
fi

read -p "Remove $DIR entirely? [y/N] " yn
[[ "$yn" == "y" ]] && rmdir "$DIR"

echo "Uninstall complete."
