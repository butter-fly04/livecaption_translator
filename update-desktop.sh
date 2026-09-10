#!/bin/bash
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP_DIR="$HOME/.local/share/applications"
DESKTOP_FILE="$APP_DIR/livecaption.desktop"

DEFAULT_SRC=$(sed -n 's/^source *= *//p' "$DIR/config.conf")
DEFAULT_TGT=$(sed -n 's/^target *= *//p' "$DIR/config.conf")
PAIRS=$(sed -n 's/^pairs *= *//p' "$DIR/config.conf")

cat > "$DESKTOP_FILE" <<EOL
[Desktop Entry]
Name=LiveCaption Translator
Comment=Live captioning and translation
Exec=$DIR/launcher.sh
Terminal=false
Type=Application
Categories=Utility;Accessibility;
Icon=audio-input-microphone
EOL

if [[ -n "$PAIRS" ]]; then
    ACTIONS=""
    IFS=',' read -ra PAIR_ARR <<< "$PAIRS"
    for pair in "${PAIR_ARR[@]}"; do
        action_name="${pair//->/_}"
        ACTIONS+="${action_name};"
    done
    echo "Actions=$ACTIONS" >> "$DESKTOP_FILE"
    
    for pair in "${PAIR_ARR[@]}"; do
        src="${pair%%->*}"
        tgt="${pair##*->}"
        action_name="${pair//->/_}"
        echo "" >> "$DESKTOP_FILE"
        echo "[Desktop Action $action_name]" >> "$DESKTOP_FILE"
        echo "Name=$src -> $tgt" >> "$DESKTOP_FILE"
        echo "Exec=$DIR/launcher.sh $src $tgt" >> "$DESKTOP_FILE"
    done
fi

if command -v update-desktop-database >/dev/null 2>&1; then
    update-desktop-database "$APP_DIR"
fi
echo "Desktop entry updated successfully."
