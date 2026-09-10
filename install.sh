#!/bin/bash
# LiveCaption Translator Installer v4

set -e

INSTALL_DIR="$HOME/livecaption"
APP_DIR="$HOME/.local/share/applications"
DESKTOP_FILE="$APP_DIR/livecaption.desktop"

# VOSK Small Models Registry (Name | Code | URL | Approx Size)
declare -A VOSK_MODELS
VOSK_MODELS=(
    ["English (US)"]="en-us|https://alphacephei.com/vosk/models/vosk-model-small-en-us-0.15.zip|40MB"
    ["English (India)"]="en-in|https://alphacephei.com/vosk/models/vosk-model-small-en-in-0.4.zip|40MB"
    ["Spanish"]="es|https://alphacephei.com/vosk/models/vosk-model-small-es-0.42.zip|40MB"
    ["French"]="fr|https://alphacephei.com/vosk/models/vosk-model-small-fr-0.22.zip|40MB"
    ["German"]="de|https://alphacephei.com/vosk/models/vosk-model-small-de-0.15.zip|40MB"
    ["Chinese"]="cn|https://alphacephei.com/vosk/models/vosk-model-small-cn-0.22.zip|42MB"
    ["Russian"]="ru|https://alphacephei.com/vosk/models/vosk-model-small-ru-0.22.zip|40MB"
)

# Colors
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

echo -e "${GREEN}=== LiveCaption Translator Installer ===${NC}"

# Check for existing installation
EXISTING_SRC=""
EXISTING_TGT=""
EXISTING_PAIRS=""
if [[ -d "$INSTALL_DIR" ]]; then
    echo -e "${YELLOW}Existing installation detected in $INSTALL_DIR.${NC}"
    if [[ -f "$INSTALL_DIR/config.conf" ]]; then
        EXISTING_SRC=$(sed -n 's/^source *= *//p' "$INSTALL_DIR/config.conf")
        EXISTING_TGT=$(sed -n 's/^target *= *//p' "$INSTALL_DIR/config.conf")
        EXISTING_PAIRS=$(sed -n 's/^pairs *= *//p' "$INSTALL_DIR/config.conf")
    fi
fi

# 1. System Packages
echo -e "\n${YELLOW}[Step 1] System Package Installation${NC}"
if [[ -n "$EXISTING_SRC" ]]; then
    read -p "Skip system package installation? [Y/n] " yn
    if [[ "$yn" == "n" || "$yn" == "N" ]]; then
        EXISTING_SRC="" # Force install
    else
        echo "Skipping system packages."
    fi
fi

if [[ -z "$EXISTING_SRC" ]]; then
    if command -v apt >/dev/null 2>&1; then
        PKGS="python3 python3-pip python3-venv python3-tk pulseaudio-utils unzip wget"
        echo "Detected Debian/Ubuntu. Packages to install: $PKGS"
        read -p "Proceed with sudo apt install? [y/N] " yn
        if [[ "$yn" == "y" || "$yn" == "Y" ]]; then
            sudo apt update && sudo apt install -y $PKGS
        else echo "Skipping system packages. Script might fail."; fi
    elif command -v dnf >/dev/null 2>&1; then
        PKGS="python3 python3-pip python3-tkinter pulseaudio-utils unzip wget"
        echo "Detected Fedora. Packages to install: $PKGS"
        read -p "Proceed with sudo dnf install? [y/N] " yn
        if [[ "$yn" == "y" || "$yn" == "Y" ]]; then
            sudo dnf install -y $PKGS
        else echo "Skipping system packages. Script might fail."; fi
    elif command -v pacman >/dev/null 2>&1; then
        PKGS="python python-pip tk libpulse unzip wget"
        echo "Detected Arch Linux. Packages to install: $PKGS"
        read -p "Proceed with sudo pacman -S? [y/N] " yn
        if [[ "$yn" == "y" || "$yn" == "Y" ]]; then
            sudo pacman -S --noconfirm $PKGS
        else echo "Skipping system packages. Script might fail."; fi
    else
        echo -e "${RED}Unsupported package manager. Please install dependencies manually.${NC}"
    fi
fi

# 2. Python Environment
echo -e "\n${YELLOW}[Step 2] Python Environment Setup${NC}"
if [[ -d "$INSTALL_DIR/venv" ]]; then
    echo "Virtual environment already exists. Skipping venv creation and pip install."
else
    read -p "Create venv in $INSTALL_DIR and install Python packages? [y/N] " yn
    if [[ "$yn" == "y" || "$yn" == "Y" ]]; then
        mkdir -p "$INSTALL_DIR"
        python3 -m venv "$INSTALL_DIR/venv"
        "$INSTALL_DIR/venv/bin/pip" install --upgrade pip
        "$INSTALL_DIR/venv/bin/pip" install -r requirements.txt
        echo -e "${GREEN}Python environment ready.${NC}"
    else
        echo "Aborting."; exit 1
    fi
fi

# 3. VOSK Models
echo -e "\n${YELLOW}[Step 3] VOSK Models Setup${NC}"
mkdir -p "$INSTALL_DIR/models"

read -p "Download/Update VOSK models from internet? [y/N] (Select N to use local zips) " yn
if [[ "$yn" == "y" || "$yn" == "Y" ]]; then
    echo "Available models:"
    i=1
    for key in "${!VOSK_MODELS[@]}"; do
        IFS='|' read -r code url size <<< "${VOSK_MODELS[$key]}"
        status=""
        for dir in "$INSTALL_DIR/models"/vosk-model-*; do
            if [[ "$(basename "$dir")" == "$(echo "$url" | sed 's/.*\///' | sed 's/\.zip//')" ]]; then
                status=" (Already installed)"
            fi
        done
        echo "  $i) $key ($size)$status"
        i=$((i+1))
    done
    read -p "Enter numbers of models to download (e.g., 1 3). Already installed will be skipped: " selections
    for sel in $selections; do
        i=1
        for key in "${!VOSK_MODELS[@]}"; do
            if [[ "$sel" == "$i" ]]; then
                IFS='|' read -r code url size <<< "${VOSK_MODELS[$key]}"
                zip_name=$(echo "$url" | sed 's/.*\///')
                model_dir_name="${zip_name%.zip}"
                
                if [[ -d "$INSTALL_DIR/models/$model_dir_name" ]]; then
                    echo "$key already exists. Skipping."
                else
                    echo "Downloading $key..."
                    wget -q --show-progress -O "/tmp/$zip_name" "$url"
                    unzip -q "/tmp/$zip_name" -d "$INSTALL_DIR/models/"
                    rm "/tmp/$zip_name"
                    echo -e "${GREEN}Installed $key.${NC}"
                fi
            fi
            i=$((i+1))
        done
    done
else
    echo "Looking for local .zip files in current directory..."
    for f in vosk-model-*.zip; do
        if [[ -f "$f" ]]; then
            echo "Found $f. Extracting..."
            unzip -o -q "$f" -d "$INSTALL_DIR/models/"
        fi
    done
fi

# 4. Configure Models
echo -e "\n${YELLOW}[Step 4] Configuration${NC}"
declare -A AVAILABLE_MODELS
declare -a SORTED_KEYS

for dir in "$INSTALL_DIR/models"/vosk-model-*; do
    if [[ -d "$dir" ]]; then
        base=$(basename "$dir")
        code=$(echo "$base" | sed -E 's/vosk-model-(small-)?([a-z]{2,3}(-[a-z]{2})?).*/\2/')
        
        # FIX: Map VOSK's 'cn' to standard ISO 'zh' for translation compatibility
        if [[ "$code" == "cn" ]]; then
            code="zh"
        fi
        
        AVAILABLE_MODELS["$code"]="$dir"
    fi
done

# Sort keys to prevent Bash associative array random ordering bugs
while IFS= read -r k; do
    SORTED_KEYS+=("$k")
done < <(printf "%s\n" "${!AVAILABLE_MODELS[@]}" | sort)

if [[ ${#SORTED_KEYS[@]} -eq 0 ]]; then
    echo -e "${RED}No VOSK models found in $INSTALL_DIR/models/. Cannot continue.${NC}"
    exit 1
fi

echo "Available VOSK Source languages:"
i=1
for code in "${SORTED_KEYS[@]}"; do
    echo "  $i) $code"
    i=$((i+1))
done

prompt_src="Select DEFAULT Source language number"
[[ -n "$EXISTING_SRC" ]] && prompt_src+=" (current: $EXISTING_SRC)"
read -p "$prompt_src: " src_sel

DEFAULT_SRC=""
if [[ -n "$src_sel" ]]; then
    i=1
    for code in "${SORTED_KEYS[@]}"; do
        if [[ "$src_sel" == "$i" ]]; then DEFAULT_SRC="$code"; fi
        i=$((i+1))
    done
fi
if [[ -z "$DEFAULT_SRC" ]]; then DEFAULT_SRC="$EXISTING_SRC"; fi

prompt_tgt="Enter DEFAULT Target language code (e.g., en, es, zh)"
[[ -n "$EXISTING_TGT" ]] && prompt_tgt+=" (current: $EXISTING_TGT)"
read -p "$prompt_tgt: " input_tgt
DEFAULT_TGT="${input_tgt:-$EXISTING_TGT}"

echo ""
echo "Setup Right-Click language pairs (optional)."
echo "Format: source->target (e.g., en-us->es, ru->en)"
echo "Separate multiple pairs with commas. Leave empty to skip."
prompt_pairs="Right-click pairs"
[[ -n "$EXISTING_PAIRS" ]] && prompt_pairs+=" (current: $EXISTING_PAIRS)"
read -p "$prompt_pairs: " input_pairs
right_pairs="${input_pairs:-$EXISTING_PAIRS}"

# Build the Python dict string for live_caption.py
PYTHON_DICT="{"
for code in "${SORTED_KEYS[@]}"; do
    PYTHON_DICT+="\"$code\": \"${AVAILABLE_MODELS[$code]}\","
done
PYTHON_DICT="${PYTHON_DICT%,}}"

# 5. Copy & Patch Scripts
echo -e "\n${YELLOW}[Step 5] Installing Scripts${NC}"
cp -f live_caption.py "$INSTALL_DIR/"
cp -f caption_translators.py "$INSTALL_DIR/"

# Patch live_caption.py to use the discovered models dynamically
python3 -c "
import re
with open('$INSTALL_DIR/live_caption.py', 'r') as f:
    content = f.read()
new_dict = '''VOSK_MODELS = $PYTHON_DICT'''
content = re.sub(r'VOSK_MODELS = \{.*?\}', new_dict, content, flags=re.DOTALL)
with open('$INSTALL_DIR/live_caption.py', 'w') as f:
    f.write(content)
"
echo -e "${GREEN}Patched live_caption.py with model paths.${NC}"

# Write config.conf
cat > "$INSTALL_DIR/config.conf" <<EOF
[defaults]
source = $DEFAULT_SRC
target = $DEFAULT_TGT

[actions]
# Format: source->target,source->target
# After editing this, run update-desktop.sh to apply changes
pairs = $right_pairs
EOF
echo -e "${GREEN}Created config.conf.${NC}"

# Write launcher.sh (Upgraded to use sed for reliable config parsing)
cat > "$INSTALL_DIR/launcher.sh" <<EOF
#!/bin/bash
DIR="$INSTALL_DIR"
source "\$DIR/venv/bin/activate"

DEFAULT_SRC=\$(sed -n 's/^source *= *//p' "\$DIR/config.conf")
DEFAULT_TGT=\$(sed -n 's/^target *= *//p' "\$DIR/config.conf")

SRC=\${1:-\$DEFAULT_SRC}
TGT=\${2:-\$DEFAULT_TGT}

if [[ -z "\$TGT" || "\$TGT" == "\$SRC" ]]; then
    python3 "\$DIR/live_caption.py" --mode "\$SRC"
else
    python3 "\$DIR/live_caption.py" --mode "\$SRC-\$TGT"
fi
EOF
chmod +x "$INSTALL_DIR/launcher.sh"

# Write update-desktop.sh
cat > "$INSTALL_DIR/update-desktop.sh" <<'EOF'
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
EOF
chmod +x "$INSTALL_DIR/update-desktop.sh"

# Write uninstall.sh
cat > "$INSTALL_DIR/uninstall.sh" <<'EOF'
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
EOF
chmod +x "$INSTALL_DIR/uninstall.sh"

# 6. Create Desktop Entry
echo -e "\n${YELLOW}[Step 6] Creating Desktop Entry${NC}"
bash "$INSTALL_DIR/update-desktop.sh"

echo -e "${GREEN}=== Installation Complete! ===${NC}"
echo "You can now launch 'LiveCaption Translator' from your app menu."
echo "To change defaults later, edit: $INSTALL_DIR/config.conf"
echo "To add/remove right-click pairs, edit config.conf and run: $INSTALL_DIR/update-desktop.sh"
echo "To uninstall, run: $INSTALL_DIR/uninstall.sh"
