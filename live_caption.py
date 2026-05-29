#!/usr/bin/env python3
"""
Live Caption Generator for Linux Mint
Captures system audio output and transcribes in real-time using VOSK.
Optional real-time translation. Side-by-side layout.
"""

import argparse
import json
import os
import queue
import subprocess
import sys
import threading
import time
import tkinter as tk
from tkinter import font as tkfont

try:
    import pulsectl
except ImportError:
    print("Missing dependency: pulsectl  (pip3 install pulsectl)")
    sys.exit(1)

try:
    import vosk
except ImportError:
    print("Missing dependency: vosk  (pip3 install vosk)")
    sys.exit(1)


# ==================== CUSTOMIZE THESE ====================
# Base window size (pixels)
BASE_WIDTH = 1400
BASE_HEIGHT = 150

# Scale the window size (100 = base size, 150 = 1.5x, 50 = half size)
WINDOW_SIZE_PERCENT = 70

# Window opacity: 0.1 (very transparent) to 1.0 (fully opaque)
# NOTE: Opacity may not work on all window managers (e.g., Cinnamon X11 with frameless windows)
WINDOW_OPACITY = 0.93

# Colors
BG_COLOR = "#1a1a1a"
FG_COLOR = "#ffffff"

# Font
FONT_FAMILY = "Noto Sans"
FONT_SIZE = 10
FONT_WEIGHT = "bold"  # "normal" or "bold"

# Caption behavior
MAX_LINES = 3
FADE_AFTER_SECONDS = 15

# -------------------- VOSK MODEL PATHS --------------------
# Map language codes to VOSK model directories.
# Keys can be any short code you want (e.g., "en", "zh", "es").
# Values must be absolute paths to extracted VOSK model folders.
#
# Language codes are typically ISO 639-1 (2-letter):
#   en=English, es=Spanish, fr=French, de=German, it=Italian,
#   pt=Portuguese, ru=Russian, zh=Chinese, ja=Japanese, ar=Arabic, etc.
# Full list: https://en.wikipedia.org/wiki/List_of_ISO_639-1_codes
#
# NOTE: The source key should also match the 'from' language code expected
# by your chosen translation engine (Argos/DeepL/LibreTranslate).
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))

VOSK_MODELS = {
    "ru": "/path to russian VOSK model",
    "zh": "/path to chinese VOSK model",
    "es": "/path to spanish VOSK model",
}

# -------------------- TRANSLATION --------------------
# Engine: "argos" (offline), "libre" (online), "deepl" (online), "google" (online, free)
TRANSLATOR_ENGINE = "google"

# LibreTranslate endpoint (only for engine="libre")
LIBRETRANSLATE_URL = "https://libretranslate.de/translate"

# DeepL API key (only for engine="deepl")
DEEPL_API_KEY = ""
# =======================================================

SAMPLE_RATE = 16000
CHUNK_BYTES = 4096


# ==================== TRANSLATORS ====================

class TranslatorBase:
    def translate(self, text: str) -> str:
        raise NotImplementedError


class ArgosTranslator(TranslatorBase):
    """Offline translation using Argos Translate (OpenNMT)."""
    def __init__(self, from_code: str, to_code: str):
        self.from_code = from_code
        self.to_code = to_code
        self._ready = False

    def _ensure_ready(self):
        if self._ready:
            return
        try:
            import argostranslate.package
            import argostranslate.translate
        except ImportError:
            raise RuntimeError(
                "Argos Translate not installed. Run: pip3 install argostranslate"
            )
        print(f"[Argos] Checking language package {self.from_code} → {self.to_code} …")
        argostranslate.package.update_package_index()
        available = argostranslate.package.get_available_packages()
        pkg = next(
            (p for p in available if p.from_code == self.from_code and p.to_code == self.to_code),
            None,
        )
        if not pkg:
            raise RuntimeError(
                f"No Argos package for {self.from_code} → {self.to_code}. "
                f"Visit https://github.com/argosopentech/argos-translate for available languages."
            )
        argostranslate.package.install_from_path(pkg.download())
        self._ready = True
        print("[Argos] Package ready.")

    def translate(self, text: str) -> str:
        self._ensure_ready()
        import argostranslate.translate
        return argostranslate.translate.translate(text, self.from_code, self.to_code)


class LibreTranslator(TranslatorBase):
    """Online translation using a LibreTranslate instance."""
    def __init__(self, url: str, from_lang: str, to_lang: str, api_key: str = None):
        self.url = url
        self.from_lang = from_lang
        self.to_lang = to_lang
        self.api_key = api_key

    def translate(self, text: str) -> str:
        try:
            import requests
        except ImportError:
            raise RuntimeError("requests not installed. Run: pip3 install requests")
        payload = {
            "q": text,
            "source": self.from_lang,
            "target": self.to_lang,
            "format": "text",
        }
        if self.api_key:
            payload["api_key"] = self.api_key
        r = requests.post(self.url, data=payload, timeout=10)
        r.raise_for_status()
        return r.json()["translatedText"]


class DeepLTranslator(TranslatorBase):
    """Online translation using DeepL API."""
    def __init__(self, api_key: str, target_lang: str):
        self.api_key = api_key
        self.target_lang = target_lang.upper()
        self.url = "https://api-free.deepl.com/v2/translate"

    def translate(self, text: str) -> str:
        if not self.api_key:
            raise RuntimeError("DeepL API key is empty. Set DEEPL_API_KEY in the script.")
        try:
            import requests
        except ImportError:
            raise RuntimeError("requests not installed. Run: pip3 install requests")
        r = requests.post(
            self.url,
            headers={"Authorization": f"DeepL-Auth-Key {self.api_key}"},
            data={"text": text, "target_lang": self.target_lang},
            timeout=10,
        )
        r.raise_for_status()
        return r.json()["translations"][0]["text"]


class GoogleTranslator(TranslatorBase):
    """Free translation using Google Translate (via deep-translator)."""
    def __init__(self, from_code: str, to_code: str):
        self.from_code = from_code
        self.to_code = to_code
        self._translator = None

    def translate(self, text: str) -> str:
        if not self._translator:
            try:
                from deep_translator import GoogleTranslator as GT
            except ImportError:
                raise RuntimeError(
                    "deep-translator not installed. Run: pip3 install deep-translator"
                )
            # deep-translator accepts "auto" as source for auto-detection
            source = self.from_code if self.from_code else "auto"
            self._translator = GT(source=source, target=self.to_code)
        return self._translator.translate(text)


def make_translator(engine, from_code, to_code):
    if to_code is None or engine == "none":
        return None
    if engine == "argos":
        return ArgosTranslator(from_code, to_code)
    if engine == "libre":
        return LibreTranslator(LIBRETRANSLATE_URL, from_code, to_code)
    if engine == "deepl":
        return DeepLTranslator(DEEPL_API_KEY, to_code)
    if engine == "google":
        return GoogleTranslator(from_code, to_code)
    raise ValueError(f"Unknown translator engine: {engine}")


# ==================== APP ====================

class LiveCaptionApp:
    def __init__(self, model_path, source_name=None, translate_from="en", translate_to=None):
        self.model_path = model_path
        self.source_name = source_name
        self.running = True

        self.audio_queue = queue.Queue(maxsize=200)
        self.text_queue = queue.Queue()
        self.translation_queue = queue.Queue()

        self.caption_lines = []
        self.current_partial = ""
        self.last_speech_time = time.time()

        self.translator = make_translator(TRANSLATOR_ENGINE, translate_from, translate_to)
        if self.translator:
            print(f"[Translation] Enabled: {translate_from} → {translate_to} ({TRANSLATOR_ENGINE})")

        self._build_ui()
        self._apply_appearance()

        # Resolve audio source
        self.monitor_source = self.source_name or self._get_default_monitor()
        if not self.monitor_source:
            self._fatal_error(
                "Could not find a system audio monitor source.\n"
                "Ensure PulseAudio / PipeWire is running."
            )
            return

        # Load VOSK
        if not os.path.exists(self.model_path):
            self._fatal_error(
                f"VOSK model not found:\n{self.model_path}\n\n"
                "Download a model from:\n"
                "https://alphacephei.com/vosk/models"
            )
            return

        try:
            self._set_status("Loading VOSK model…")
            self.root.update()
            self.model = vosk.Model(self.model_path)
            self.recognizer = vosk.KaldiRecognizer(self.model, SAMPLE_RATE)
        except Exception as e:
            self._fatal_error(f"Failed to load VOSK model:\n{e}")
            return

        self._set_status("Listening…")

        # Start workers
        self.capture_thread = threading.Thread(target=self._capture_audio, daemon=True)
        self.recognize_thread = threading.Thread(target=self._recognize_audio, daemon=True)
        self.capture_thread.start()
        self.recognize_thread.start()

        if self.translator:
            self.translation_thread = threading.Thread(target=self._translation_worker, daemon=True)
            self.translation_thread.start()

        # GUI loops
        self.root.after(100, self._update_caption)
        self._schedule_fade_check()

    # -------------------- UI --------------------
    def _build_ui(self):
        self.root = tk.Tk()
        self.root.title("Live Captions")
        self.root.withdraw()                      # <-- ADD THIS: hide until fully configured

        # Frameless, always on top
        self.root.overrideredirect(True)
        self.root.attributes("-topmost", True)
        self.caption_font = tkfont.Font(family=FONT_FAMILY, size=FONT_SIZE, weight=FONT_WEIGHT)

        # Side-by-side grid: MAX_LINES rows × 2 columns
        self.caption_frame = tk.Frame(self.root, bg=BG_COLOR)
        self.caption_frame.pack(expand=True, fill="both", padx=20, pady=10)

        self.left_labels = []
        self.right_labels = []

        for i in range(MAX_LINES):
            left = tk.Label(
                self.caption_frame,
                text="",
                font=self.caption_font,
                bg=BG_COLOR,
                fg=FG_COLOR,
                anchor="w",
                justify="left",
            )
            left.grid(row=i, column=0, sticky="nsew", padx=(0, 10))

            right = tk.Label(
                self.caption_frame,
                text="",
                font=self.caption_font,
                bg=BG_COLOR,
                fg=FG_COLOR,
                anchor="w",
                justify="left",
            )
            right.grid(row=i, column=1, sticky="nsew", padx=(10, 0))

            self.left_labels.append(left)
            self.right_labels.append(right)

        # Lock both columns to exactly equal width regardless of content
        self.caption_frame.grid_columnconfigure(0, weight=1, uniform="col")
        self.caption_frame.grid_columnconfigure(1, weight=1, uniform="col")

        # Drag to move
        self.root.bind("<Button-1>", self._start_drag)
        self.root.bind("<B1-Motion>", self._on_drag)

        # Right-click menu
        self.root.bind("<Button-3>", self._show_menu)
        self.menu = tk.Menu(
            self.root,
            tearoff=0,
            bg="#2d2d2d",
            fg="#ffffff",
            activebackground="#444444",
            activeforeground="#ffffff",
        )
        self.menu.add_command(label="Increase Font", command=self._increase_font)
        self.menu.add_command(label="Decrease Font", command=self._decrease_font)
        self.menu.add_separator()
        self.menu.add_command(label="Exit", command=self.shutdown)

        # Keyboard shortcut
        self.root.bind("<Escape>", lambda e: self.shutdown())

    def _set_status(self, text):
        """Show a status message across all rows (used during init)."""
        for i in range(MAX_LINES):
            if i == MAX_LINES // 2:
                self.left_labels[i].config(text=text)
            else:
                self.left_labels[i].config(text="")
            self.right_labels[i].config(text="")

    def _apply_appearance(self):
        # Calculate scaled size
        scale = WINDOW_SIZE_PERCENT / 100.0
        w = int(BASE_WIDTH * scale)
        h = int(BASE_HEIGHT * scale)

        # Center at bottom of screen
        sw = self.root.winfo_screenwidth()
        sh = self.root.winfo_screenheight()
        x = (sw - w) // 2
        y = sh - h - 60

        self.root.geometry(f"{w}x{h}+{x}+{y}")
        self.root.configure(bg=BG_COLOR)
        self.caption_frame.configure(bg=BG_COLOR)

        # --- OPACITY FIX for Cinnamon / X11 ---
        # Frameless windows must be mapped by the compositor before -alpha works.
        # We withdraw in _build_ui, do all setup, then deiconify and wait.
        self.root.deiconify()
        self.root.wait_visibility()
        self.root.attributes("-alpha", WINDOW_OPACITY)
        # ---------------------------------------

        # Update wraplengths to fit half the window width
        col_width = max(200, (w - 100) // 2)
        for i in range(MAX_LINES):
            self.left_labels[i].config(wraplength=col_width, bg=BG_COLOR, fg=FG_COLOR)
            self.right_labels[i].config(wraplength=col_width, bg=BG_COLOR, fg=FG_COLOR)

        try:
            self.caption_font.configure(family=FONT_FAMILY, size=FONT_SIZE, weight=FONT_WEIGHT)
        except tk.TclError:
            self.caption_font.configure(family="DejaVu Sans", size=FONT_SIZE, weight=FONT_WEIGHT)

    def _start_drag(self, event):
        self._drag_x = event.x
        self._drag_y = event.y

    def _on_drag(self, event):
        x = self.root.winfo_x() + event.x - self._drag_x
        y = self.root.winfo_y() + event.y - self._drag_y
        self.root.geometry(f"+{x}+{y}")

    def _show_menu(self, event):
        self.menu.post(event.x_root, event.y_root)

    def _increase_font(self):
        s = self.caption_font.cget("size")
        self.caption_font.config(size=min(s + 2, 72))

    def _decrease_font(self):
        s = self.caption_font.cget("size")
        self.caption_font.config(size=max(s - 2, 8))

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
        cmd = [
            "parec",
            f"--device={self.monitor_source}",
            f"--rate={SAMPLE_RATE}",
            "--format=s16le",
            "--channels=1",
            "--latency-msec=100",
        ]
        try:
            self.audio_proc = subprocess.Popen(
                cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE
            )
            while self.running:
                data = self.audio_proc.stdout.read(CHUNK_BYTES)
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
            self.text_queue.put(("final", f"Audio error: {e}"))

    def _recognize_audio(self):
        while self.running:
            try:
                data = self.audio_queue.get(timeout=0.5)
                if self.recognizer.AcceptWaveform(data):
                    result = json.loads(self.recognizer.Result())
                    text = result.get("text", "").strip()
                    if text:
                        self.text_queue.put(("final", text))
                        if self.translator:
                            self.translation_queue.put(text)
                else:
                    partial = json.loads(self.recognizer.PartialResult())
                    text = partial.get("partial", "").strip()
                    if text:
                        self.text_queue.put(("partial", text))
            except queue.Empty:
                continue
            except Exception as e:
                print(f"Recognition error: {e}")

    # -------------------- Translation --------------------
    def _translation_worker(self):
        while self.running:
            try:
                text = self.translation_queue.get(timeout=0.5)
                translated = self.translator.translate(text)
                self.text_queue.put(("translated", (text, translated)))
            except queue.Empty:
                continue
            except Exception as e:
                print(f"Translation error: {e}")

    # -------------------- Display --------------------
    def _update_caption(self):
        changed = False
        try:
            while True:
                kind, payload = self.text_queue.get_nowait()
                self.last_speech_time = time.time()
                if kind == "final":
                    self.current_partial = ""
                    if payload:
                        self.caption_lines.append(payload)
                        if len(self.caption_lines) > MAX_LINES:
                            self.caption_lines = self.caption_lines[-MAX_LINES:]
                    changed = True
                elif kind == "partial":
                    self.current_partial = payload
                    changed = True
                elif kind == "translated":
                    self.current_partial = ""
                    source, translated = payload
                    # Replace the most recent matching source line with a tuple
                    for i in range(len(self.caption_lines) - 1, -1, -1):
                        if self.caption_lines[i] == source:
                            self.caption_lines[i] = (source, translated)
                            break
                    changed = True
        except queue.Empty:
            pass

        if changed:
            self._render()

        if self.running:
            self.root.after(100, self._update_caption)

    def _render(self):
        # Build row tuples: (original, translated_or_empty)
        rows = []
        for item in self.caption_lines:
            if isinstance(item, tuple):
                source, translated = item
                rows.append((source, translated))
            else:
                rows.append((item, ""))

        if self.current_partial:
            rows.append((f"{self.current_partial} …", ""))

        if not rows:
            rows.append(("Listening…", ""))

        # Keep only the last MAX_LINES rows
        start = max(0, len(rows) - MAX_LINES)
        display = rows[start:]

        for i in range(MAX_LINES):
            if i < len(display):
                left_text, right_text = display[i]
                self.left_labels[i].config(text=left_text)
                self.right_labels[i].config(text=right_text)
            else:
                self.left_labels[i].config(text="")
                self.right_labels[i].config(text="")

    def _schedule_fade_check(self):
        self.root.after(int(FADE_AFTER_SECONDS * 1000), self._check_fade)

    def _check_fade(self):
        if time.time() - self.last_speech_time > FADE_AFTER_SECONDS:
            if self.caption_lines or self.current_partial:
                self.caption_lines = []
                self.current_partial = ""
                self._render()
        if self.running:
            self._schedule_fade_check()

    # -------------------- Lifecycle --------------------
    def _fatal_error(self, msg):
        for i in range(MAX_LINES):
            if i == MAX_LINES // 2:
                self.left_labels[i].config(text=msg, fg="#ff6666")
            else:
                self.left_labels[i].config(text="")
            self.right_labels[i].config(text="")
        self.root.after(8000, self.shutdown)

    def shutdown(self):
        self.running = False
        if hasattr(self, "audio_proc"):
            self.audio_proc.terminate()
        self.root.destroy()
        sys.exit(0)

    def run(self):
        self.root.mainloop()


def list_sources():
    print("Available PulseAudio sources:")
    with pulsectl.Pulse("live-caption") as pulse:
        for src in pulse.source_list():
            print(f"  {src.name}\n      {src.description}")
    print("\nTip: Use the monitor source of your default sink to capture system audio.")


def parse_mode(mode_str):
    """
    Parse --mode string.
    Format: SOURCE-TARGET  (e.g., 'zh-en', 'en-us-es')
            or just SOURCE (e.g., 'es' for no translation)
    Splits on the LAST hyphen so locale codes like 'en-us' work as source keys.
    """
    if "-" in mode_str:
        source, target = mode_str.rsplit("-", 1)
        return source, target
    return mode_str, None


def main():
    parser = argparse.ArgumentParser(description="Live Caption Generator for Linux")
    parser.add_argument(
        "--mode",
        default="en",
        help="Language mode: SOURCE-TARGET (e.g., 'zh-en', 'es-en') or just SOURCE (e.g., 'en'). "
             "Source must be a key in VOSK_MODELS. Target is the translation language.",
    )
    parser.add_argument(
        "-d", "--device", help="PulseAudio source name (default: monitor of default sink)"
    )
    parser.add_argument(
        "--list-devices", action="store_true", help="List audio sources and exit"
    )
    args = parser.parse_args()

    if args.list_devices:
        list_sources()
        return

    source_lang, target_lang = parse_mode(args.mode)

    # Validate source language key
    model_path = VOSK_MODELS.get(source_lang)
    if not model_path:
        print(f"Error: No VOSK model configured for language key '{source_lang}'.")
        print(f"Configured keys: {', '.join(VOSK_MODELS.keys())}")
        sys.exit(1)

    if not os.path.exists(model_path):
        print(f"Error: VOSK model not found at: {model_path}")
        sys.exit(1)

    app = LiveCaptionApp(
        model_path,
        source_name=args.device,
        translate_from=source_lang,
        translate_to=target_lang,
    )
    app.run()


if __name__ == "__main__":
    main()
