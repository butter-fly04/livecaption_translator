#!/usr/bin/env python3
"""
Speech-model catalogue and downloader (no Tk in here).

* catalog():  the models offered for download. Tries the live list of sherpa-onnx
  release assets from GitHub, falls back to the built-in list when offline.
  Extra entries can be added in <data dir>/catalog.json (a list of
  {"name", "lang", "kind", "size_mb", "url", "sha256"(optional)}).
* download(): streams an archive, checks its SHA-256 when one is known, extracts
  it safely (no path escapes, no links out of the folder) and returns the folder.

Models are third-party files with their own licences - check them before use.
"""

import hashlib
import json
import os
import re
import shutil
import tarfile
import tempfile

import requests

from . import paths

RELEASE_API = "https://api.github.com/repos/k2-fsa/sherpa-onnx/releases/tags/asr-models"
RELEASE_URL = "https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models/{}.tar.bz2"
_UA = "live-caption (model downloader)"


class Cancelled(Exception):
    pass


def _entry(archive, title, lang, kind, size_mb):
    return dict(id=archive, name=title, lang=lang, kind=kind, size_mb=size_mb,
                url=RELEASE_URL.format(archive), sha256=None)


# Built-in fallback list (sizes are the archive sizes).
BUILTIN = [
    _entry("sherpa-onnx-streaming-zipformer-zh-int8-2025-06-30",
           "Chinese · Zipformer int8 (2025-06-30)", "zh", "transducer", 133),
    _entry("sherpa-onnx-streaming-zipformer-small-ctc-zh-int8-2025-04-01",
           "Chinese · small Zipformer CTC int8 (2025-04-01)", "zh", "zipformer2_ctc", 21),
    _entry("sherpa-onnx-streaming-zipformer-bilingual-zh-en-2023-02-20",
           "Chinese + English · Zipformer (2023-02-20)", "zh", "transducer", 511),
    _entry("sherpa-onnx-streaming-paraformer-bilingual-zh-en",
           "Chinese + English · Paraformer", "zh", "paraformer", 1047),
    _entry("sherpa-onnx-streaming-zipformer-en-2023-06-26",
           "English · Zipformer (2023-06-26)", "en", "transducer", 310),
    _entry("sherpa-onnx-streaming-zipformer-en-20M-2023-02-17",
           "English · small Zipformer 20M (2023-02-17)", "en", "transducer", 128),
    _entry("sherpa-onnx-streaming-zipformer-korean-2024-06-16",
           "Korean · Zipformer (2024-06-16)", "ko", "transducer", 418),
    _entry("sherpa-onnx-streaming-zipformer-fr-2023-04-14",
           "French · Zipformer (2023-04-14)", "fr", "transducer", 398),
    _entry("sherpa-onnx-streaming-zipformer-ar_en_id_ja_ru_th_vi_zh-2025-02-10",
           "Multilingual (ar en id ja ru th vi zh) · Zipformer (2025-02-10)", "", "transducer", 259),
]

_LANGS = {"zh": "zh", "en": "en", "ru": "ru", "ko": "ko", "korean": "ko", "fr": "fr", "de": "de",
          "es": "es", "ar": "ar", "ja": "ja", "vi": "vi", "th": "th", "id": "id", "it": "it",
          "pt": "pt", "tr": "tr", "uk": "uk", "pl": "pl", "nl": "nl", "hi": "hi"}
_LANG_NAMES = {"zh": "Chinese", "en": "English", "ru": "Russian", "ko": "Korean", "fr": "French",
               "de": "German", "es": "Spanish", "ar": "Arabic", "ja": "Japanese", "vi": "Vietnamese",
               "th": "Thai", "id": "Indonesian", "it": "Italian", "pt": "Portuguese",
               "tr": "Turkish", "uk": "Ukrainian", "pl": "Polish", "nl": "Dutch", "hi": "Hindi"}


def _from_asset(asset):
    """A catalogue entry for a GitHub release asset, or None if it isn't a streaming model."""
    fname = asset["name"]
    m = re.fullmatch(r"(sherpa-onnx-streaming-.+)\.tar\.bz2", fname)
    if not m:
        return None
    stem = m.group(1)
    langs = []
    for tok in re.split(r"[-_]", stem):
        code = _LANGS.get(tok.lower())
        if code and code not in langs:
            langs.append(code)
    if len(langs) > 2:
        lang = ""                                 # multilingual: the user picks the code
    elif len(langs) == 2 and "en" in langs:
        lang = [c for c in langs if c != "en"][0]
    else:
        lang = langs[0] if langs else ""
    low = stem.lower()
    kind = "paraformer" if "paraformer" in low else "zipformer2_ctc" if "ctc" in low else "transducer"
    pretty = re.sub(r"^sherpa-onnx-streaming-", "", stem).replace("-", " ")
    digest = (asset.get("digest") or "")
    return dict(id=stem, name=pretty, lang=lang, kind=kind,
                size_mb=round(asset.get("size", 0) / 1e6),
                url=asset["browser_download_url"],
                sha256=digest.split(":", 1)[1] if digest.startswith("sha256:") else None)


def fetch_live_catalog(timeout=10):
    r = requests.get(RELEASE_API, timeout=timeout, headers={"User-Agent": _UA})
    r.raise_for_status()
    entries = [e for e in (_from_asset(a) for a in r.json().get("assets", [])) if e]
    if not entries:
        raise ValueError("no streaming models in the release listing")
    return sorted(entries, key=lambda e: (e["lang"] == "", e["lang"], e["name"]))


def user_catalog():
    """Entries the user added in <data dir>/catalog.json (ignored if malformed)."""
    try:
        with open(os.path.join(paths.data_dir(), "catalog.json"), encoding="utf-8") as f:
            data = json.load(f)
        return [e for e in data if isinstance(e, dict) and e.get("url") and e.get("name")]
    except (OSError, ValueError):
        return []


def catalog(live=True):
    """(entries, note). note says where the list came from."""
    note = "Built-in list"
    entries = list(BUILTIN)
    if live:
        try:
            entries, note = fetch_live_catalog(), "Live list from GitHub"
        except Exception:
            note = "Built-in list (couldn't reach GitHub)"
    extra = user_catalog()
    return extra + entries, note


def pretty_lang(code):
    return _LANG_NAMES.get(code, code or "multiple")


# ---------------------------------------------------------------- download
def _safe_members(tar, dest):
    """Refuse archive entries that would land outside `dest` or link out of it."""
    root = os.path.realpath(dest)
    for m in tar.getmembers():
        target = os.path.realpath(os.path.join(dest, m.name))
        if target != root and not target.startswith(root + os.sep):
            raise ValueError(f"Unsafe path in archive: {m.name}")
        if m.issym() or m.islnk():
            link = os.path.realpath(os.path.join(os.path.dirname(target), m.linkname)) \
                if m.issym() else os.path.realpath(os.path.join(dest, m.linkname))
            if link != root and not link.startswith(root + os.sep):
                raise ValueError(f"Unsafe link in archive: {m.name} -> {m.linkname}")
        if m.isdev():
            raise ValueError(f"Unsafe special file in archive: {m.name}")
        yield m


def download(entry, progress=None, cancel=None, validate=None, models_root=None):
    """Download + extract one catalogue entry; returns the model folder.

    progress(phase, done, total): phase is "download" or "extract" (total 0 = unknown).
    cancel: a threading.Event; set it to abort (raises Cancelled, leaves nothing behind).
    validate(folder): called on the extracted folder; raise to reject it (it is removed).
    """
    progress = progress or (lambda *a: None)
    models_root = models_root or paths.models_dir()
    work = tempfile.mkdtemp(prefix=".download-", dir=models_root)
    try:
        url = entry["url"]
        archive = os.path.join(work, "model.archive")
        h = hashlib.sha256()
        with requests.get(url, stream=True, timeout=30, headers={"User-Agent": _UA}) as r:
            r.raise_for_status()
            total = int(r.headers.get("Content-Length") or 0) or int(entry.get("size_mb", 0) * 1e6)
            free = shutil.disk_usage(models_root).free
            if total and free < total * 2.2:
                raise OSError(f"Not enough free space: need about {total * 2.2 / 1e6:.0f} MB, "
                              f"have {free / 1e6:.0f} MB.")
            done = 0
            with open(archive, "wb") as f:
                for chunk in r.iter_content(1 << 16):
                    if cancel is not None and cancel.is_set():
                        raise Cancelled()
                    f.write(chunk)
                    h.update(chunk)
                    done += len(chunk)
                    progress("download", done, total)
        want = (entry.get("sha256") or "").lower()
        if want and h.hexdigest() != want:
            raise ValueError("The downloaded file is corrupt (checksum mismatch). Try again.")

        progress("extract", 0, 0)
        out = os.path.join(work, "out")
        os.makedirs(out)
        try:
            with tarfile.open(archive, "r:*") as tar:
                members = list(_safe_members(tar, out))
                if hasattr(tarfile, "data_filter"):
                    tar.extractall(out, members=members, filter="data")
                else:
                    tar.extractall(out, members=members)
        except tarfile.TarError as e:
            raise ValueError(f"Could not unpack the archive: {e}")
        if cancel is not None and cancel.is_set():
            raise Cancelled()

        tops = os.listdir(out)
        if len(tops) == 1 and os.path.isdir(os.path.join(out, tops[0])):
            folder, name = os.path.join(out, tops[0]), tops[0]
        else:                                           # loose files: wrap them
            name = re.sub(r"[^\w.-]+", "-", entry.get("id") or entry["name"])
            folder = out
        if validate:
            validate(folder)

        final = os.path.join(models_root, name)
        n = 1
        while os.path.exists(final):                    # never overwrite an existing model
            n += 1
            final = os.path.join(models_root, f"{name}-{n}")
        shutil.move(folder, final)
        return final
    finally:
        shutil.rmtree(work, ignore_errors=True)
