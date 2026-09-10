#!/bin/bash
DIR="/home/username/livecaption"
source "$DIR/venv/bin/activate"

DEFAULT_SRC=$(sed -n 's/^source *= *//p' "$DIR/config.conf")
DEFAULT_TGT=$(sed -n 's/^target *= *//p' "$DIR/config.conf")

SRC=${1:-$DEFAULT_SRC}
TGT=${2:-$DEFAULT_TGT}

if [[ -z "$TGT" || "$TGT" == "$SRC" ]]; then
    python3 "$DIR/live_caption.py" --mode "$SRC"
else
    python3 "$DIR/live_caption.py" --mode "$SRC-$TGT"
fi
