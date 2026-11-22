#!/usr/bin/env python3
"""
Live Caption Generator for Linux — backend.
Captures system audio output and transcribes in real-time using a
streaming Zipformer transducer model via sherpa-onnx. Optional real-time
translation. UI lives in caption_ui.py, the settings form in settings_ui.py.
"""

import argparse
import json
import os
import queue
import re
import sys
import threading
import time

try:
    import numpy as np
except ImportError:
    print("Missing dependency: numpy  (pip3 install numpy)")
    sys.exit(1)

try:
    import pulsectl
except ImportError:
    print("Missing dependency: pulsectl  (pip3 install pulsectl)")
    sys.exit(1)

try:
    import sherpa_onnx
except ImportError:
    print("Missing dependency: sherpa-onnx  (pip3 install sherpa-onnx)")
    sys.exit(1)

from . import __version__, paths
from .audio_capture import CaptureError, open_capture
from .model_dialog import ModelDialog
from .caption_translators import ENGINES, SmartCaptionTranslator
from .caption_ui import CaptionWindow
from .settings_ui import F

# Desktop-launched bundles have no terminal, but when started from one it is
# useful to see status lines immediately instead of at process exit.
try:
    sys.stdout.reconfigure(line_buffering=True)
except Exception:
    pass


# ==================== DEFAULTS (also editable from the Settings window) ====================
# Anything changed in the Settings window / right-click menu is saved to
# ~/.config/live-caption/config.json (see paths.py) and overrides these.
# -------------------- SHERPA-ONNX MODEL PATHS --------------------
# Each entry points to a directory containing a streaming (online) Zipformer
# transducer exported for sherpa-onnx: an encoder/decoder/joiner *.onnx trio
# plus tokens.txt. Point this at the extracted repo/tarball root — files are
# located recursively, so it doesn't matter whether the model ships them
# flat (official k2-fsa releases) or nested (alphacep's am-onnx/ + lang/
# layout). The int8-quantized weights are preferred automatically when both
# int8 and fp32 variants exist — that's what keeps RAM close to Chrome's
# Live Caption footprint.

# Built-in defaults; you normally manage models in Settings → Speech models (add a
# folder or download one), which saves them to the config file. Entries here are
# just starting points, e.g.:
#   "ru": "~/models/sherpa-onnx-streaming-zipformer-ru-XXXX",
SHERPA_MODELS = {
}

# -------------------- LANGUAGE PAIRS (for the menu / Settings) --------------------
# Named source->target combinations offered in the "Language Pair" menu, so you
# can switch languages live without restarting. Each value is
# (source_lang, target_lang) — source_lang must be a key in SHERPA_MODELS above;
# target_lang is whatever code your translator engine accepts (e.g. "en").
#
# The STARTING selection comes from --mode / the saved choice /
# DEFAULT_LANGUAGE_PAIR (see resolve_language_pair()).
LANGUAGE_PAIRS = {
    "Russian → English": ("ru", "en"),
    "Chinese → English": ("zh", "en"),
}
DEFAULT_LANGUAGE_PAIR = "Russian → English"

# -------------------- ENDPOINT / SILENCE PRESETS --------------------
# None of the streaming ASR models here emit punctuation, so sentence
# boundaries for translation are decided ENTIRELY by silence — how long a
# pause has to be before it counts as "end of sentence" rather than just a
# breath or a moment of hesitation.
#
#   rule1 (min_trailing_silence): fires on trailing silence even if little
#     or no speech has been confirmed yet — a broad catch-all. Because it
#     doesn't require confirmed speech, it's normally the HIGHER of the two.
#   rule2 (min_trailing_silence): only fires once real speech HAS been
#     recognized in this utterance, so it can be — and normally is — LOWER
#     than rule1. This is the one that does most of the actual
#     sentence-splitting work.
#   rule3 (min_utterance_length): a hard cap regardless of silence at all —
#     last-resort safety net for speech with no usable gap whatsoever.
#
# "edited": tightly-cut continuous narration — YouTube videos, product
#   reviews, podcasts, anything post-produced to remove dead air.
# "natural": live, unscripted speech — livestreams, interviews, conversation.
#
# Priority for the active values: --rule1/2/3 on the command line, then --preset,
# then the saved Settings/menu choice, then ENDPOINT_PRESET below.
ENDPOINT_PRESETS = {
    "edited":  dict(rule1=0.7, rule2=0.25, rule3=10.0),
    "natural": dict(rule1=1.5, rule2=0.80, rule3=15.0),
}
ENDPOINT_PRESET = "natural"  # fallback if nothing else specifies one
RULE_KEYS = ("rule1", "rule2", "rule3")

NUM_THREADS = 2  # CPU threads for the speech model

# Saved settings live in ~/.config/live-caption/config.json (see paths.py), not next to
# this script, so they survive updates and work from a read-only AppImage.
CONFIG_PATH = paths.config_path()
MODELS_DIR = paths.models_dir()   # downloaded models go here

# -------------------- TRANSLATION --------------------
# Engines (see caption_translators.ENGINES): "auto" (Google → Bing → Yandex),
# "google", "google_gtx", "bing", "yandex", "mymemory", "libretranslate" (needs
# a server URL), "deepl" (needs an API key). "none" disables translation (the
# recognised text is shown as-is).
TRANSLATOR_ENGINE = "auto"
TRANSLATOR_API_KEY = ""     # DeepL key / MyMemory email / LibreTranslate key
TRANSLATOR_URL = ""         # LibreTranslate server, e.g. http://localhost:5000
TRANSLATOR_FALLBACK = True  # if the chosen engine fails, try Google → Bing → Yandex
TRANSLATOR_MIN_DELAY = 0.8  # seconds between translation requests
# =======================================================

SAMPLE_RATE = 16000
CHUNK_BYTES = 4096


# ==================== MODEL FILE RESOLUTION ====================

def detect_model(model_dir):
    """
    Work out which kind of streaming sherpa-onnx model a folder holds and find
    its files (searched recursively; int8 weights are preferred). Returns a dict:
      kind: "transducer"      encoder + decoder + joiner   (Zipformer, Conformer, ...)
            "paraformer"      encoder + decoder, no joiner (streaming Paraformer)
            "zipformer2_ctc"  a single model file          (k2 Zipformer2-CTC)
            "nemo_ctc"        a single model file, folder name mentions "nemo"
      tokens, plus encoder/decoder/joiner or model.
    """
    import glob

    def pick(pattern, exclude=()):
        paths = glob.glob(os.path.join(model_dir, "**", f"*{pattern}*.onnx"), recursive=True)
        paths = [m for m in paths if not any(x in os.path.basename(m).lower() for x in exclude)]
        int8 = sorted(m for m in paths if "int8" in os.path.basename(m).lower())
        if int8:
            return int8[0]
        # Avoid accidentally grabbing a long-chunk fp32 variant when possible
        paths = sorted(paths)
        return ([m for m in paths if "chunk" not in os.path.basename(m).lower()] or paths or [None])[0]

    tokens = sorted(glob.glob(os.path.join(model_dir, "**", "tokens.txt"), recursive=True))
    tokens = tokens[0] if tokens else None
    encoder, decoder, joiner = pick("encoder"), pick("decoder"), pick("joiner")

    if encoder and decoder and joiner:
        info = dict(kind="transducer", encoder=encoder, decoder=decoder, joiner=joiner)
    elif encoder and decoder:
        info = dict(kind="paraformer", encoder=encoder, decoder=decoder)
    else:
        model = pick("", exclude=("encoder", "decoder", "joiner"))
        if not model:
            raise FileNotFoundError(
                f"No streaming model found in:\n{model_dir}\n"
                "Expected encoder/decoder/joiner *.onnx (transducer), encoder/decoder *.onnx "
                "(Paraformer) or a single model *.onnx (CTC), plus tokens.txt."
            )
        kind = "nemo_ctc" if "nemo" in model_dir.lower() else "zipformer2_ctc"
        info = dict(kind=kind, model=model)
    if not tokens:
        raise FileNotFoundError(f"tokens.txt not found in model directory:\n{model_dir}")
    info["tokens"] = tokens
    return info


def make_recognizer(info, cfg):
    """Build an OnlineRecognizer for a model found by detect_model()."""
    common = dict(
        tokens=info["tokens"], num_threads=cfg["num_threads"], sample_rate=SAMPLE_RATE,
        feature_dim=80, decoding_method="greedy_search", enable_endpoint_detection=True,
        rule1_min_trailing_silence=cfg["rule1"],
        rule2_min_trailing_silence=cfg["rule2"],
        rule3_min_utterance_length=cfg["rule3"],
    )
    kind, R = info["kind"], sherpa_onnx.OnlineRecognizer
    if kind == "transducer":
        return R.from_transducer(encoder=info["encoder"], decoder=info["decoder"],
                                 joiner=info["joiner"], **common)
    if kind == "paraformer":
        return R.from_paraformer(encoder=info["encoder"], decoder=info["decoder"], **common)
    if kind == "nemo_ctc":
        return R.from_nemo_ctc(model=info["model"], **common)
    return R.from_zipformer2_ctc(model=info["model"], **common)


def effective_models(config):
    """Language code -> model folder: SHERPA_MODELS, then anything saved in the config
    (a "models" table; an empty value removes a built-in entry)."""
    models = dict(SHERPA_MODELS)
    for lang in SHERPA_MODELS:                    # older single-path keys
        if config.get(f"model_{lang}"):
            models[lang] = config[f"model_{lang}"]
    for code, path in (config.get("models") or {}).items():
        if path:
            models[code] = path
        else:
            models.pop(code, None)
    return models


def effective_pairs(config):
    """Pair name -> (source, target): LANGUAGE_PAIRS plus the saved "language_pairs"
    (null removes a built-in pair)."""
    pairs = dict(LANGUAGE_PAIRS)
    for name, v in (config.get("language_pairs") or {}).items():
        if v and len(v) == 2:
            pairs[name] = (v[0], v[1])
        else:
            pairs.pop(name, None)
    return pairs


def get_model_path(lang, config):
    """Model folder for a language code."""
    return effective_models(config).get(lang)


def rows_to_models(rows):
    out = {}
    for code, path in rows:
        if not re.fullmatch(r"[A-Za-z0-9_-]+", code):
            raise ValueError(f"Language code '{code}': use letters and digits only (e.g. ru, zh, ja)")
        if code in out:
            raise ValueError(f"Language code '{code}' is listed twice")
        out[code] = os.path.expanduser(path)
    if not out:
        raise ValueError("Add at least one speech model")
    return out


def rows_to_pairs(rows, models, existing=None):
    """Table rows -> {name: (source, target)}. A pair you add or edit must point at a
    model you have; pairs that were already there may point at one you haven't set
    up yet (they just ask you to add it when you switch to them)."""
    existing = existing or {}
    out = {}
    for name, src, tgt in rows:
        name = name or f"{src} → {tgt}"
        if name in out:
            raise ValueError(f"Language pair name '{name}' is listed twice")
        if src not in models and existing.get(name) != (src, tgt):
            raise ValueError(f"Pair '{name}': there is no speech model for '{src}'. "
                             "Add it under Speech models first.")
        out[name] = (src, tgt)
    if not out:
        raise ValueError("Add at least one language pair")
    return out


# ==================== TRANSLATOR FACTORY ====================

def make_translator(cfg, from_code, to_code):
    """Build the translator described by cfg (None = translation off).
    Raises ValueError for a missing key/URL or an unknown engine."""
    if to_code is None or cfg["translator_engine"] == "none":
        return None
    return SmartCaptionTranslator(
        from_code, to_code, min_delay=cfg["translator_min_delay"],
        engine=cfg["translator_engine"], api_key=cfg["translator_api_key"],
        url=cfg["translator_url"], fallback=cfg["translator_fallback"])


# ==================== APP (backend) ====================

class LiveCaptionApp:
    def __init__(self, model_path=None, source_name=None, translate_from="en", translate_to=None,
                 preset_name=ENDPOINT_PRESET, rules=None,
                 language_pair_name=None, config_path=None, config=None):
        self.source_name = source_name
        self.translate_from = translate_from
        self.translate_to = translate_to
        self.model_path = None
        self.running = True

        self.audio_queue = queue.Queue(maxsize=200)
        self.translation_queue = queue.Queue()

        self.last_speech_time = time.time()
        self._last_partial_sent = ""
        self._paused = False
        self._rule_lock = threading.Lock()      # guards recognizer/stream/translator swaps
        self._reload_lock = threading.Lock()    # one reload at a time
        self.recognizer = self.stream = self.translator = None

        # Everything the Settings window / menus can change on this side.
        config = config or {}
        rules = rules or ENDPOINT_PRESETS[preset_name]
        self.cfg = dict(
            endpoint_preset=preset_name,
            rule1=rules["rule1"], rule2=rules["rule2"], rule3=rules["rule3"],
            num_threads=int(config.get("num_threads", NUM_THREADS)),
            language_pair=language_pair_name,
            translator_engine=config.get("translator_engine", TRANSLATOR_ENGINE),
            translator_min_delay=float(config.get("translator_min_delay", TRANSLATOR_MIN_DELAY)),
            translator_api_key=config.get("translator_api_key", TRANSLATOR_API_KEY),
            translator_url=config.get("translator_url", TRANSLATOR_URL),
            translator_fallback=bool(config.get("translator_fallback", TRANSLATOR_FALLBACK)),
        )
        self.cfg["models"] = effective_models(config)
        self.cfg["language_pairs"] = effective_pairs(config)
        if model_path:
            self.cfg["models"][translate_from] = model_path

        tabs = self._settings_tabs()
        self._defaults = {f["key"]: f["default"] for tab in tabs for f in tab["fields"] if "default" in f}

        self.window = CaptionWindow(
            preset_names=list(ENDPOINT_PRESETS.keys()),
            initial_preset=preset_name,
            language_pair_names=list(self.cfg["language_pairs"]),
            initial_language_pair=language_pair_name,
            config_path=config_path,
            on_shutdown=self._on_shutdown,
            on_pause_toggle=self._on_pause_toggle,
            settings_tabs=tabs,
            backend_values=self._backend_values,
            backend_choices=lambda: {"language_pair": list(self.cfg["language_pairs"])},
            on_settings_change=self._on_settings_change,
            normalize_patch=self._normalize_patch,
        )

        # Resolve audio source
        self.monitor_source = self.source_name or self._get_default_monitor()
        if not self.monitor_source:
            self.window.fatal_error(
                "Could not find a system audio monitor source.\n"
                "Ensure PulseAudio / PipeWire is running."
            )
            return

        try:
            make_translator(self.cfg, translate_from, translate_to)
        except ValueError as e:     # e.g. a saved engine that needs a key that's gone
            print(f"[Translation] {e}; using automatic engine instead")
            self.cfg["translator_engine"] = "auto"

        problem = self._model_problem()
        if problem:
            # First run (or a moved folder): start anyway and point the user at Settings.
            self.window.set_status(
                f"{problem}\n\nRight-click → Settings → Speech models to download or add one.")
            self.window.root.after(700, self.window._open_settings)
        else:
            try:
                self.window.set_status("Loading speech model…")
                self.window.root.update()
                self._apply_config()
            except Exception as e:
                self.window.fatal_error(f"Failed to load speech model:\n{e}")
                return
            self.window.set_status("Listening…")

        # Start workers
        self.capture_thread = threading.Thread(target=self._capture_audio, daemon=True)
        self.recognize_thread = threading.Thread(target=self._recognize_audio, daemon=True)
        self.translation_thread = threading.Thread(target=self._translation_worker, daemon=True)
        self.capture_thread.start()
        self.recognize_thread.start()
        self.translation_thread.start()

    def _model_problem(self, cfg=None):
        """A message if the active language has no usable model folder, else None."""
        cfg = cfg or self.cfg
        pair = cfg["language_pairs"].get(cfg.get("language_pair"))
        source = pair[0] if pair else self.translate_from
        path = cfg["models"].get(source)
        if not path:
            return f"No speech model is set up for '{source}'."
        if not os.path.isdir(path):
            return f"Model folder for '{source}' not found:\n{path}"
        return None

    # -------------------- Settings window plumbing --------------------
    def _settings_tabs(self):
        """The Speech and Languages tabs. One F(...) line per option."""
        base = ENDPOINT_PRESETS[ENDPOINT_PRESET]
        speech = [
            F("endpoint_preset", "Pause preset", "choice", choices=list(ENDPOINT_PRESETS),
              default=ENDPOINT_PRESET, fills=ENDPOINT_PRESETS,
              help="How long a silence must last before a caption line is finished. "
                   "Picking a preset fills in the three numbers below."),
            F("rule1", "Silence, no speech yet (s)", "float", min=0.1, max=15, step=0.05,
              default=base["rule1"], help="Rule 1 — normally the larger of the two silences."),
            F("rule2", "Silence after speech (s)", "float", min=0.1, max=15, step=0.05,
              default=base["rule2"], help="Rule 2 — the main sentence splitter."),
            F("rule3", "Longest line (s)", "float", min=1, max=120, step=1,
              default=base["rule3"], help="Rule 3 — forces a break even with no pause at all."),
            F("num_threads", "CPU threads", "int", min=1, max=8, default=NUM_THREADS,
              help="More threads lowers latency but uses more CPU."),
        ]
        models = [
            F("models", "Models", "rows", default=[[c, p] for c, p in SHERPA_MODELS.items()],
              columns=[dict(title="Language code", width=10),
                       dict(title="Model folder", width=34, kind="path")],
              help="One row per spoken language. The folder is an extracted sherpa-onnx "
                   "streaming model (transducer, Paraformer or CTC — detected automatically; "
                   "needs tokens.txt). The code is whatever you want to call the language, "
                   "e.g. ja, ko, de."),
            F("download_model", "", "button", text="Download a model…",
              on_click=self._open_model_dialog),
        ]
        pairs = [
            F("language_pair", "Active pair", "choice", choices=list(self.cfg["language_pairs"]),
              default=DEFAULT_LANGUAGE_PAIR, help="Spoken language → translation language."),
            F("language_pairs", "Pairs", "rows",
              default=[[n, s, t] for n, (s, t) in LANGUAGE_PAIRS.items()],
              columns=[dict(title="Name (optional)", width=20, optional=True),
                       dict(title="Spoken (model code)", width=10),
                       dict(title="Translate to", width=8)],
              help="“Spoken” must match a code in Models. “Translate to” is the target "
                   "language code your translation engine understands (en, de, zh, …). "
                   "A blank name becomes “spoken → target”."),
        ]
        languages = [
            F("translator_engine", "Translation engine", "choice",
              choices=list(ENGINES) + ["none"], labels={**ENGINES, "none": "None (show recognised text)"},
              default=TRANSLATOR_ENGINE,
              help="Keyless web engines are unofficial and may be rate-limited."),
            F("translator_api_key", "API key / email", "password", default=TRANSLATOR_API_KEY,
              show_if=("translator_engine", ["deepl", "mymemory", "libretranslate"]),
              help="DeepL: required. MyMemory: optional email (bigger quota). "
                   "LibreTranslate: optional key."),
            F("translator_url", "Server URL", "text", default=TRANSLATOR_URL,
              show_if=("translator_engine", ["libretranslate"]),
              help="e.g. http://localhost:5000"),
            F("translator_fallback", "Fall back to free engines", "bool", default=TRANSLATOR_FALLBACK,
              show_if=("translator_engine", ["google", "google_gtx", "bing", "yandex",
                                              "mymemory", "libretranslate", "deepl"]),
              help="If the chosen engine fails, try Google → Bing → Yandex."),
            F("translator_min_delay", "Delay between requests (s)", "float", min=0, max=10,
              step=0.1, default=TRANSLATOR_MIN_DELAY,
              help="Minimum gap between translation requests."),
        ]
        return [{"title": "Speech", "fields": speech},
                {"title": "Speech models", "fields": models},
                {"title": "Language pairs", "fields": pairs},
                {"title": "Translation", "fields": languages}]

    def _open_model_dialog(self, settings_win):
        ModelDialog(settings_win, on_done=lambda code, folder: self._model_downloaded(
            settings_win, code, folder), validate=detect_model)

    def _model_downloaded(self, settings_win, code, folder):
        """Add a freshly downloaded model (and a pair for it) to the open form."""
        models, pairs = settings_win.fields["models"], settings_win.fields["language_pairs"]
        rows = [[v.get().strip() for v in vs] for vs in models.row_vars]
        used = {r[0] for r in rows}
        base, n = code, 1
        while code in used:
            n += 1
            code = f"{base}{n}"
        models.set([r for r in rows if any(r)] + [[code, folder]])
        prow = [[v.get().strip() for v in vs] for vs in pairs.row_vars]
        if code != "en" and not any(r[1] == code for r in prow):
            pairs.set([r for r in prow if any(r)] + [["", code, "en"]])
        settings_win._say(f"Added model '{code}' — press Apply to start using it.")

    def _backend_values(self):
        values = dict(self.cfg)
        values["language_pair"] = values.get("language_pair") or ""
        values["models"] = [[c, p] for c, p in self.cfg["models"].items()]
        values["language_pairs"] = [[n, s, t] for n, (s, t) in self.cfg["language_pairs"].items()]
        return values

    def _normalize_patch(self, patch):
        """Tidy what gets written to the config file, so editing the defaults in this
        file later isn't silently overridden by stale saved values."""
        # Rule values are only saved when they differ from the chosen preset.
        if "endpoint_preset" in patch or any(r in patch for r in RULE_KEYS):
            base = ENDPOINT_PRESETS.get(patch.get("endpoint_preset") or self.cfg["endpoint_preset"], {})
            for r in RULE_KEYS:
                if r in patch:
                    patch[r] = None if patch[r] is None or patch[r] == base.get(r) else patch[r]
                elif "endpoint_preset" in patch:
                    patch[r] = None  # a new preset drops old custom numbers
        # Models / pairs: save only what differs from the lists in this file.
        if "models" in patch:
            saved = {c: p for c, p in self.cfg["models"].items() if SHERPA_MODELS.get(c) != p}
            saved.update({c: "" for c in SHERPA_MODELS if c not in self.cfg["models"]})
            patch["models"] = saved or None
            for lang in SHERPA_MODELS:
                patch[f"model_{lang}"] = None          # drop older single-path keys
        if "language_pairs" in patch:
            saved = {n: list(p) for n, p in self.cfg["language_pairs"].items()
                     if LANGUAGE_PAIRS.get(n) != p}
            saved.update({n: None for n in LANGUAGE_PAIRS if n not in self.cfg["language_pairs"]})
            patch["language_pairs"] = saved or None
        # Anything equal to the default in this file isn't saved at all.
        for key, value in list(patch.items()):
            if key not in RULE_KEYS + ("models", "language_pairs") and value is not None \
                    and value == self._defaults.get(key, object()):
                patch[key] = None
        return patch

    def _on_settings_change(self, changes):
        """Called on the UI thread. Validates, then applies in the background.
        Raising here rejects the change (nothing is saved)."""
        new = {**self.cfg, **changes}
        if "endpoint_preset" in changes and not any(r in changes for r in RULE_KEYS):
            new.update(ENDPOINT_PRESETS[changes["endpoint_preset"]])

        # Speech models and language pairs arrive as table rows.
        if "models" in changes:
            new["models"] = rows_to_models(changes["models"])
            for code, path in new["models"].items():
                if path != self.cfg["models"].get(code):        # new or edited rows only
                    try:
                        if not os.path.isdir(path):
                            raise FileNotFoundError(f"Folder not found:\n{path}")
                        detect_model(path)
                    except (OSError, ValueError) as e:
                        raise ValueError(f"Model '{code}': {e}")
        if "models" in changes or "language_pairs" in changes:
            rows = changes["language_pairs"] if "language_pairs" in changes else \
                [[n, s, t] for n, (s, t) in self.cfg["language_pairs"].items()]
            new["language_pairs"] = rows_to_pairs(rows, new["models"], self.cfg["language_pairs"])
        if new.get("language_pair") and new["language_pair"] not in new["language_pairs"]:
            raise ValueError(f"The active pair '{new['language_pair']}' was removed — "
                             "choose another active pair.")

        pair = new["language_pairs"].get(new.get("language_pair"))
        if any(k.startswith("translator_") for k in changes):
            make_translator(new, pair[0] if pair else self.translate_from,
                            pair[1] if pair else self.translate_to)   # ValueError = reject

        reload_asr = any(k in changes for k in
                         ("endpoint_preset", "num_threads", "language_pair") + RULE_KEYS)
        source = pair[0] if pair else self.translate_from
        path = new["models"].get(source)
        if reload_asr or path != self.model_path:
            if path and os.path.isdir(path):
                detect_model(path)  # raises a clear message if files are missing
            elif reload_asr or path:
                raise ValueError(self._model_problem(new) or f"Model folder for '{source}' not found:\n{path}")
            # else: the active language has no model yet; the rest still saves

        self.cfg = new
        threading.Thread(target=self._reload_in_background, args=(reload_asr,), daemon=True).start()

    def _reload_in_background(self, reload_asr):
        try:
            self.window.set_status("Applying settings…")
            self._apply_config(reload_asr)
            self.window.set_status("Listening…")
        except Exception as e:
            print(f"[Settings] Could not apply: {e}")
            self.window.set_status(f"Settings not applied:\n{e}")

    def _apply_config(self, reload_asr=True):
        """(Re)build the recognizer and translator from self.cfg and swap them in.
        sherpa-onnx can't change endpoint rules, threads or model on a live
        recognizer, so those all mean loading a fresh one."""
        with self._reload_lock:
            cfg = dict(self.cfg)
            pair = cfg["language_pairs"].get(cfg.get("language_pair"))
            source, target = pair if pair else (self.translate_from, self.translate_to)
            model_path = cfg["models"].get(source)
            language_changed = source != self.translate_from or model_path != self.model_path

            recognizer = stream = None
            if reload_asr or language_changed:
                if not model_path or not os.path.isdir(model_path):
                    raise FileNotFoundError(self._model_problem(cfg) or
                                            f"Model folder for '{source}' not found:\n{model_path}")
                recognizer = make_recognizer(detect_model(model_path), cfg)
                stream = recognizer.create_stream()

            translator = make_translator(cfg, source, target)
            with self._rule_lock:
                if recognizer is not None:
                    self.recognizer, self.stream = recognizer, stream
                    self._last_partial_sent = ""
                self.translator = translator
                self.translate_from, self.translate_to = source, target
                self.model_path = model_path

            if language_changed:
                # Captions (and pending translations) from the old language don't
                # belong mixed in with the new ones.
                with self.translation_queue.mutex:
                    self.translation_queue.queue.clear()
                self.window.clear_captions()
            self.window.set_translation_enabled(translator is not None)
            if translator:
                print(f"[Translation] {source} -> {target} ({cfg['translator_engine']})")
            print(f"[Speech] {source}: preset '{cfg['endpoint_preset']}' "
                  f"(rule1={cfg['rule1']}, rule2={cfg['rule2']}, rule3={cfg['rule3']}), "
                  f"{cfg['num_threads']} threads")

    def _on_pause_toggle(self, paused):
        self._paused = paused
        if paused:
            # Drop whatever half-recognised text is pending.
            with self._rule_lock:
                if self.recognizer is not None:
                    self.recognizer.reset(self.stream)
                self._last_partial_sent = ""

    def _on_shutdown(self):
        self.running = False
        cap = getattr(self, "_capture", None)
        if cap is not None:
            cap.stop()

    # -------------------- Audio & Recognition --------------------
    def _get_default_monitor(self):
        try:
            with pulsectl.Pulse("live-caption") as pulse:
                sink = pulse.sink_default_get()
                return sink.monitor_source_name
        except Exception as e:
            print(f"PulseAudio error: {e}")
            return None

    def _capture_audio(self):
        try:
            self._capture = open_capture(self.monitor_source, SAMPLE_RATE, latency_ms=100)
        except CaptureError as e:
            self.window.set_status(f"Audio capture failed:\n{e}")
            return
        try:
            while self.running:
                data = self._capture.read(CHUNK_BYTES)
                if not data:
                    break
                try:
                    self.audio_queue.put_nowait(data)
                except queue.Full:
                    try:
                        self.audio_queue.get_nowait()
                        self.audio_queue.put_nowait(data)
                    except queue.Empty:
                        pass
        except Exception as e:
            if self.running:
                self.window.set_status(f"Audio capture stopped:\n{e}")
        finally:
            self._capture.close()

    def _recognize_audio(self):
        while self.running:
            try:
                data = self.audio_queue.get(timeout=0.5)
                if self._paused:
                    continue  # "deaf mode": keep draining the queue, ignore the audio

                # s16le bytes -> float32 in [-1, 1], as required by sherpa-onnx
                samples_int16 = np.frombuffer(data, dtype=np.int16)
                samples = samples_int16.astype(np.float32) / 32768.0

                # Locked so a concurrent settings change can't swap
                # self.recognizer/self.stream out from under a decode cycle.
                with self._rule_lock:
                    recognizer = self.recognizer
                    stream = self.stream
                    translator = self.translator
                    if recognizer is None:
                        continue            # no model loaded yet (first run)

                    stream.accept_waveform(SAMPLE_RATE, samples)
                    while recognizer.is_ready(stream):
                        recognizer.decode_stream(stream)

                    text = recognizer.get_result(stream).strip()
                    is_endpoint = recognizer.is_endpoint(stream)

                    if text:
                        self.last_speech_time = time.time()

                    if is_endpoint:
                        if text:
                            self.window.submit_final(text)
                            if translator:
                                self.translation_queue.put(text)
                        self._last_partial_sent = ""
                        recognizer.reset(stream)
                    elif self.window.settings["show_partial"] and text and text != self._last_partial_sent:
                        self._last_partial_sent = text
                        self.window.submit_partial(text)

            except queue.Empty:
                continue
            except Exception as e:
                print(f"Recognition error: {e}")

    # -------------------- Translation --------------------
    def _translation_worker(self):
        """Translate FINAL captions only. Partials are too noisy and waste quota."""
        while self.running:
            try:
                text = self.translation_queue.get(timeout=0.5)
                translator = self.translator
                if not translator:
                    continue
                translated = translator.translate(text)
                self.window.submit_translated(text, translated)
            except queue.Empty:
                continue
            except Exception as e:
                print(f"Translation error: {e}")

    def run(self):
        self.window.run()


def list_sources():
    print("Available PulseAudio sources:")
    with pulsectl.Pulse("live-caption") as pulse:
        for src in pulse.source_list():
            print(f"  {src.name}\n      {src.description}")
    print("\nTip: Use the monitor source of your default sink to capture system audio.")


def self_test():
    """Check the things that commonly break in an AppImage build.

    Used by packaging/smoke-test.sh and by users who want a quick diagnosis.
    Returns 0 when the core runtime is usable, 1 otherwise. Audio and display
    problems are reported as warnings, not failures, so this can run in CI.
    """
    import ctypes
    import ctypes.util
    import platform
    import shutil
    import tempfile

    ok = True

    def row(label, value):
        print(f"  {label:<26} {value}")

    print(f"Live Caption {__version__}")
    row("Python", sys.version.split()[0])
    row("Frozen bundle", bool(getattr(sys, "frozen", False)))
    row("Resource folder", paths.resource_dir())
    row("Platform", platform.platform())
    row("glibc", " ".join(platform.libc_ver()))

    for name, mod in (("numpy", np), ("sherpa-onnx", sherpa_onnx),
                      ("pulsectl", pulsectl)):
        row(name, getattr(mod, "__version__", "present"))

    try:
        import tkinter
        row("Tk", f"{tkinter.TkVersion} ({tkinter.__file__})")
    except Exception as e:
        ok = False
        row("Tk", f"FAILED: {e}")

    try:
        from .caption_tray import TrayController  # noqa: F401
        row("Tray module", "present")
    except Exception as e:
        ok = False
        row("Tray module", f"FAILED: {e}")
    row("DBus session", os.environ.get("DBUS_SESSION_BUS_ADDRESS") or "not set")

    for label, folder in (("Config folder", paths.config_dir()),
                          ("Models folder", paths.models_dir())):
        try:
            with tempfile.NamedTemporaryFile(dir=folder, prefix=".write-test-"):
                pass
            row(label, f"{folder} (writable)")
        except OSError as e:
            ok = False
            row(label, f"{folder} (NOT WRITABLE: {e})")

    row("Config file", CONFIG_PATH)
    row("Display", os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")
        or "not set (GUI will not start)")

    lib = ctypes.util.find_library("pulse-simple")
    try:
        ctypes.CDLL(lib or "libpulse-simple.so.0")
        row("libpulse-simple", lib or "libpulse-simple.so.0")
    except OSError as e:
        row("libpulse-simple", f"missing ({e}); will try parec")
    row("parec", shutil.which("parec") or "not found (optional fallback)")

    try:
        config = load_config(CONFIG_PATH)
        models = effective_models(config)
        row("Configured models", ", ".join(models) or "none")
        for code, folder in models.items():
            try:
                info = detect_model(folder)
                row(f"  {code}", f"{info['kind']} · {folder}")
            except Exception as e:
                ok = False
                row(f"  {code}", f"INVALID: {e}")
    except Exception as e:
        ok = False
        row("Configured models", f"FAILED: {e}")

    print("Result:", "OK" if ok else "FAILED")
    return 0 if ok else 1


def parse_mode(mode_str):
    """
    Parse a SOURCE-TARGET string (e.g. 'zh-en') or a bare SOURCE ('es' for
    no translation). Splits on the LAST hyphen so locale codes like 'en-us'
    work as source keys.
    """
    if "-" in mode_str:
        source, target = mode_str.rsplit("-", 1)
        return source, target
    return mode_str, None


def find_language_pair_name(source_lang, target_lang, pairs=None):
    """Reverse-lookup: which LANGUAGE_PAIRS entry (if any) matches this
    source/target combination — used to pre-select the right radio button
    in the menu, and left unmatched (None) for a custom --mode."""
    for name, (s, t) in (pairs or LANGUAGE_PAIRS).items():
        if s == source_lang and t == target_lang:
            return name
    return None


def resolve_language_pair(args, config=None):
    """
    Priority for the starting language pair:
      1. --mode            (explicit CLI choice, e.g. '--mode zh-en')
      2. config file's "language_pair"  (last choice made in the menu or
         Settings window) — only if it still exists
      3. DEFAULT_LANGUAGE_PAIR, or the first pair, or "en" (no translation)
         as the ultimate fallback if there are no pairs at all
    Pairs = LANGUAGE_PAIRS plus any added in the Settings window.
    Returns (source_lang, target_lang, pair_name_or_None).
    """
    config = config if config is not None else load_config(CONFIG_PATH)
    pairs = effective_pairs(config)
    if args.mode:
        source_lang, target_lang = parse_mode(args.mode)
        return source_lang, target_lang, find_language_pair_name(source_lang, target_lang, pairs)

    pair_name = config.get("language_pair")
    if pair_name not in pairs:
        pair_name = DEFAULT_LANGUAGE_PAIR if DEFAULT_LANGUAGE_PAIR in pairs else next(iter(pairs), None)

    if pair_name:
        source_lang, target_lang = pairs[pair_name]
    else:
        source_lang, target_lang = "en", None
    return source_lang, target_lang, pair_name


def load_config(path):
    try:
        with open(path) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {}


def resolve_endpoint_rules(args, config):
    """
    Priority for the active endpoint preset/rules:
      1. --rule1/--rule2/--rule3  (always win, individually)
      2. --preset                 (explicit CLI choice; ignores saved custom numbers)
      3. saved Settings/menu choice: "endpoint_preset" plus any custom
         rule1/rule2/rule3 saved with it
      4. ENDPOINT_PRESET           (fallback default)
    """
    preset_name = args.preset or config.get("endpoint_preset") or ENDPOINT_PRESET
    if preset_name not in ENDPOINT_PRESETS:
        print(f"Warning: unknown preset '{preset_name}', falling back to '{ENDPOINT_PRESET}'")
        preset_name = ENDPOINT_PRESET

    rules = dict(ENDPOINT_PRESETS[preset_name])
    for r in RULE_KEYS:
        if getattr(args, r) is not None:
            rules[r] = getattr(args, r)
        elif args.preset is None and config.get(r) is not None:
            rules[r] = float(config[r])
    return preset_name, rules


def main():
    parser = argparse.ArgumentParser(description="Live Caption Generator for Linux")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    parser.add_argument(
        "--self-test", action="store_true",
        help="Check the frozen/source runtime (imports, Tk, config and model folders, "
             "audio libraries) and exit.",
    )
    parser.add_argument(
        "--mode",
        default=None,
        help="Language mode: SOURCE-TARGET (e.g., 'zh-en', 'es-en') or just SOURCE (e.g., 'en'). "
             "Source must be a configured speech-model code. Overrides the saved 'Language Pair' choice "
             "and DEFAULT_LANGUAGE_PAIR. If omitted, the saved choice (or "
             "DEFAULT_LANGUAGE_PAIR) is used.",
    )
    parser.add_argument(
        "-d", "--device", help="PulseAudio source name (default: monitor of default sink)"
    )
    parser.add_argument(
        "--list-devices", action="store_true", help="List audio sources and exit"
    )
    parser.add_argument(
        "--preset", choices=list(ENDPOINT_PRESETS.keys()), default=None,
        help="Endpoint/silence preset. Overrides the saved choice and the "
             "hardcoded ENDPOINT_PRESET default.",
    )
    parser.add_argument(
        "--rule1", type=float, default=None,
        help="Override rule1_min_trailing_silence directly, in seconds.",
    )
    parser.add_argument(
        "--rule2", type=float, default=None,
        help="Override rule2_min_trailing_silence directly, in seconds.",
    )
    parser.add_argument(
        "--rule3", type=float, default=None,
        help="Override rule3_min_utterance_length (max utterance length) directly, in seconds.",
    )
    args = parser.parse_args()

    if args.self_test:
        return self_test()

    if args.list_devices:
        try:
            list_sources()
        except Exception as e:
            print(f"Could not connect to PulseAudio/PipeWire: {e}")
            return 1
        return

    config = load_config(CONFIG_PATH)
    source_lang, target_lang, pair_name = resolve_language_pair(args, config)

    # The model may be missing on first run: the app starts anyway and opens Settings.
    model_path = get_model_path(source_lang, config)
    if model_path and not os.path.isdir(model_path):
        print(f"Warning: model directory not found at: {model_path}")

    preset_name, rules = resolve_endpoint_rules(args, config)
    print(f"[Preset] Using '{preset_name}' "
          f"(rule1={rules['rule1']}, rule2={rules['rule2']}, rule3={rules['rule3']})")
    print(f"[Language] Using {source_lang} -> {target_lang}"
          + (f" ('{pair_name}')" if pair_name else " (custom --mode, not in LANGUAGE_PAIRS)"))

    app = LiveCaptionApp(
        model_path,
        source_name=args.device,
        translate_from=source_lang,
        translate_to=target_lang,
        preset_name=preset_name,
        rules=rules,
        language_pair_name=pair_name,
        config_path=CONFIG_PATH,
        config=config,
    )
    app.run()


if __name__ == "__main__":
    sys.exit(main())
