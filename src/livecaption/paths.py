#!/usr/bin/env python3
"""
Where Live Caption keeps its files, so the same code works from a source
checkout, a PyInstaller bundle or an AppImage (whose own folder is read-only and
changes on every launch).

    config  ~/.config/live-caption/config.json      (honours $XDG_CONFIG_HOME)
    models  ~/.local/share/live-caption/models/     (honours $XDG_DATA_HOME)

Overrides (handy for tests and portable installs):
    LIVE_CAPTION_CONFIG_DIR, LIVE_CAPTION_DATA_DIR
"""

import os
import shutil
import sys

APP = "live-caption"
LEGACY_CONFIG_NAME = "live_caption_config.json"   # used to live next to the module


def _dir(env_override, xdg_var, fallback):
    path = os.environ.get(env_override) or os.path.join(
        os.environ.get(xdg_var) or os.path.expanduser(fallback), APP)
    os.makedirs(path, exist_ok=True)
    return path


def config_dir():
    return _dir("LIVE_CAPTION_CONFIG_DIR", "XDG_CONFIG_HOME", "~/.config")


def data_dir():
    return _dir("LIVE_CAPTION_DATA_DIR", "XDG_DATA_HOME", "~/.local/share")


def models_dir():
    path = os.path.join(data_dir(), "models")
    os.makedirs(path, exist_ok=True)
    return path


def resource_dir():
    """Folder holding bundled read-only files (the PyInstaller bundle, else this folder)."""
    return getattr(sys, "_MEIPASS", None) or os.path.dirname(os.path.abspath(__file__))


def config_path():
    """The config file. On first run, an old config sitting next to the module is copied over."""
    path = os.path.join(config_dir(), "config.json")
    if not os.path.exists(path):
        legacy = os.path.join(os.path.dirname(os.path.abspath(__file__)), LEGACY_CONFIG_NAME)
        if os.path.exists(legacy):
            try:
                shutil.copy2(legacy, path)
            except OSError:
                pass
    return path
