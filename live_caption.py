#!/usr/bin/env python3
"""
Live Caption Generator for Linux 
Captures system audio output and transcribes in real-time using VOSK.
Optional real-time translation. Side-by-side scrollable layout.
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

from caption_translators import SmartCaptionTranslator


# ==================== CUSTOMIZE THESE ====================
# Base window size (pixels)
BASE_WIDTH = 1400
BASE_HEIGHT = 220

# Scale the window size (100 = base size, 150 = 1.5x, 50 = half size)
WINDOW_SIZE_PERCENT = 70

# Window opacity: 0.1 (very transparent) to 1.0 (fully opaque)
WINDOW_OPACITY = 0.93

# Colors
BG_COLOR = "#1a1a1a"
FG_COLOR = "#ffffff"

# Font
FONT_FAMILY = "Noto Sans"
FONT_SIZE = 10
FONT_WEIGHT = "bold"  # "normal" or "bold"

# Scrollback buffer limit (lines). Older lines are trimmed automatically.
MAX_BUFFER_LINES = 500

# If True, hides the source-text pane and halves the window width.
# Only translated text appears in the scrollback.
SHOW_ONLY_TRANSLATION = True

# -------------------- VOSK MODEL PATHS --------------------
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))

VOSK_MODELS = {
    "ru": "/path/to/vosk/model/vosk-model-small-ru-0.22",
    "zh": "/path/to/vosk/model/vosk-model-small-cn-0.22",
    "es": "/path/to/vosk/model/vosk-model-small-es-0.42",
    "ar": "/path/to/vosk/model/vosk-model-ar-mgb2-0.4",
}

# -------------------- TRANSLATION --------------------
# "google" uses SmartCaptionTranslator (Google/Bing/Yandex fallback)
# "none" disables translation
TRANSLATOR_ENGINE = "google"
# =======================================================

SAMPLE_RATE = 16000
CHUNK_BYTES = 4096


# ==================== TRANSLATOR FACTORY ====================

def make_translator(engine, from_code, to_code):
    if to_code is None or engine == "none":
        return None
    return SmartCaptionTranslator(from_code, to_code)


# ==================== APP ====================

class LiveCaptionApp:
    def __init__(self, model_path, source_name=None, translate_from="en", translate_to=None):
        self.model_path = model_path
        self.source_name = source_name
        self.running = True

        self.audio_queue = queue.Queue(maxsize=200)
        self.text_queue = queue.Queue()
        self.translation_queue = queue.Queue()

        self.caption_entries = []   # {"source": str, "translated": str|None}
        self.current_partial = ""
        self.last_speech_time = time.time()

        # Sentence buffering state
        self._sentence_buffer = ""
        self._sentence_lock = threading.Lock()
        self._sentence_timer = None

        self.translator = make_translator(TRANSLATOR_ENGINE, translate_from, translate_to)
        if self.translator:
            print(f"[Translation] Enabled: {translate_from} -> {translate_to} ({TRANSLATOR_ENGINE})")

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

    # -------------------- UI --------------------
    def _build_ui(self):
        self.root = tk.Tk()
        self.root.title("Live Captions")
        self.root.withdraw()

        # Frameless, always on top
        self.root.overrideredirect(True)
        self.root.attributes("-topmost", True)

        self.caption_font = tkfont.Font(family=FONT_FAMILY, size=FONT_SIZE, weight=FONT_WEIGHT)
        self.partial_font = tkfont.Font(
            family=FONT_FAMILY, size=FONT_SIZE, weight="normal", slant="italic"
        )

        # Main container
        main_frame = tk.Frame(self.root, bg=BG_COLOR)
        main_frame.pack(expand=True, fill="both")

        # ---- Scrollable text area ----
        text_frame = tk.Frame(main_frame, bg=BG_COLOR)
        text_frame.pack(expand=True, fill="both", padx=10, pady=(10, 0))

        self.left_text = tk.Text(
            text_frame,
            font=self.caption_font,
            bg=BG_COLOR,
            fg=FG_COLOR,
            wrap=tk.WORD,
            state=tk.DISABLED,
            padx=5,
            pady=5,
            height=6,
            spacing1=2,
            spacing3=2,
            highlightthickness=0,
            borderwidth=0,
        )
        self.right_text = tk.Text(
            text_frame,
            font=self.caption_font,
            bg=BG_COLOR,
            fg=FG_COLOR,
            wrap=tk.WORD,
            state=tk.DISABLED,
            padx=5,
            pady=5,
            height=6,
            spacing1=2,
            spacing3=2,
            highlightthickness=0,
            borderwidth=0,
        )
        self.scrollbar = tk.Scrollbar(text_frame, command=self._on_scrollbar)

        if SHOW_ONLY_TRANSLATION:
            # Single pane: translation only
            self.right_text.grid(row=0, column=0, sticky="nsew")
            self.scrollbar.grid(row=0, column=1, sticky="ns")
            self.right_text.config(yscrollcommand=self.scrollbar.set)
            text_frame.grid_columnconfigure(0, weight=1)
        else:
            # Dual pane: source + translation
            self.left_text.grid(row=0, column=0, sticky="nsew")
            self.right_text.grid(row=0, column=1, sticky="nsew", padx=(5, 0))
            self.scrollbar.grid(row=0, column=2, sticky="ns")
            self.left_text.config(yscrollcommand=self._on_text_scroll)
            self.right_text.config(yscrollcommand=self._on_text_scroll)
            text_frame.grid_columnconfigure(0, weight=1, uniform="col")
            text_frame.grid_columnconfigure(1, weight=1, uniform="col")

        # Mousewheel scroll
        scroll_widgets = [self.right_text, text_frame] if SHOW_ONLY_TRANSLATION else [self.left_text, self.right_text, text_frame]
        for w in scroll_widgets:
            w.bind("<MouseWheel>", self._on_mousewheel)
            w.bind("<Button-4>", self._on_mousewheel)
            w.bind("<Button-5>", self._on_mousewheel)

        # ---- Live partial indicator (bottom) ----
        partial_frame = tk.Frame(main_frame, bg=BG_COLOR)
        partial_frame.pack(fill="x", padx=10, pady=(5, 10))

        self.partial_left = tk.Label(
            partial_frame,
            text="",
            font=self.partial_font,
            bg=BG_COLOR,
            fg="#aaaaaa",
            anchor="w",
            justify="left",
        )
        self.partial_left.pack(side=tk.LEFT, expand=True, fill="x")

        if not SHOW_ONLY_TRANSLATION:
            self.partial_right = tk.Label(
                partial_frame,
                text="",
                font=self.partial_font,
                bg=BG_COLOR,
                fg="#888888",
                anchor="w",
                justify="left",
            )
            self.partial_right.pack(side=tk.RIGHT, expand=True, fill="x", padx=(5, 0))

        # Drag to move
        for w in (self.root, main_frame, partial_frame):
            w.bind("<Button-1>", self._start_drag)
            w.bind("<B1-Motion>", self._on_drag)

        # Right-click menu
        menu_widgets = [self.root, main_frame, text_frame, partial_frame,
                        self.left_text, self.right_text, self.partial_left]
        if not SHOW_ONLY_TRANSLATION:
            menu_widgets.append(self.partial_right)
        for w in menu_widgets:
            w.bind("<Button-3>", self._show_menu)

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
        """Show a status message in the text widgets (used during init)."""
        self.right_text.config(state=tk.NORMAL)
        self.right_text.delete("1.0", tk.END)
        self.right_text.insert("1.0", text)
        self.right_text.config(state=tk.DISABLED)
        if not SHOW_ONLY_TRANSLATION:
            self.left_text.config(state=tk.NORMAL)
            self.left_text.delete("1.0", tk.END)
            self.left_text.insert("1.0", text)
            self.left_text.config(state=tk.DISABLED)

    def _apply_appearance(self):
        scale = WINDOW_SIZE_PERCENT / 100.0
        if SHOW_ONLY_TRANSLATION:
            w = int((BASE_WIDTH * scale) / 2)
        else:
            w = int(BASE_WIDTH * scale)
        h = int(BASE_HEIGHT * scale)

        sw = self.root.winfo_screenwidth()
        sh = self.root.winfo_screenheight()
        x = (sw - w) // 2
        y = sh - h - 60

        self.root.geometry(f"{w}x{h}+{x}+{y}")
        self.root.configure(bg=BG_COLOR)

        # Opacity fix for Cinnamon / X11
        self.root.deiconify()
        self.root.wait_visibility()
        self.root.attributes("-alpha", WINDOW_OPACITY)

        # Wraplength for partial labels
        if SHOW_ONLY_TRANSLATION:
            col_width = max(200, w - 80)
        else:
            col_width = max(200, (w - 100) // 2)
        self.partial_left.config(wraplength=col_width)
        if not SHOW_ONLY_TRANSLATION:
            self.partial_right.config(wraplength=col_width)

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
        self.partial_font.config(size=min(s + 2, 72))
        self.right_text.config(font=self.caption_font)
        if not SHOW_ONLY_TRANSLATION:
            self.left_text.config(font=self.caption_font)

    def _decrease_font(self):
        s = self.caption_font.cget("size")
        self.caption_font.config(size=max(s - 2, 8))
        self.partial_font.config(size=max(s - 2, 8))
        self.right_text.config(font=self.caption_font)
        if not SHOW_ONLY_TRANSLATION:
            self.left_text.config(font=self.caption_font)

    # -------------------- Scroll Sync --------------------
    def _on_scrollbar(self, *args):
        self.right_text.yview(*args)
        if not SHOW_ONLY_TRANSLATION:
            self.left_text.yview(*args)

    def _on_text_scroll(self, first, last):
        self.scrollbar.set(first, last)
        self.right_text.yview_moveto(first)
        if not SHOW_ONLY_TRANSLATION:
            self.left_text.yview_moveto(first)

    def _on_mousewheel(self, event):
        delta = 0
        if event.num == 4 or getattr(event, "delta", 0) > 0:
            delta = -3
        elif event.num == 5 or getattr(event, "delta", 0) < 0:
            delta = 3
        self.right_text.yview_scroll(delta, "units")
        if not SHOW_ONLY_TRANSLATION:
            self.left_text.yview_scroll(delta, "units")
        return "break"

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
                        with self._sentence_lock:
                            self._sentence_buffer = (self._sentence_buffer + " " + text).strip()
                            self.last_speech_time = time.time()
                            if self._sentence_timer:
                                self._sentence_timer.cancel()
                            if self._is_sentence_complete(self._sentence_buffer):
                                self._flush_sentence_buffer()
                            else:
                                self._sentence_timer = threading.Timer(1.5, self._flush_sentence_buffer)
                                self._sentence_timer.start()
                else:
                    partial = json.loads(self.recognizer.PartialResult())
                    text = partial.get("partial", "").strip()
                    if text:
                        # Only display partial locally — NEVER translate it
                        self.text_queue.put(("partial", text))
            except queue.Empty:
                continue
            except Exception as e:
                print(f"Recognition error: {e}")

    def _is_sentence_complete(self, text: str) -> bool:
        text = text.rstrip()
        if not text:
            return False
        # Sentence-ending punctuation across languages
        return text[-1] in ".!?…。！？؛؟"

    def _flush_sentence_buffer(self):
        with self._sentence_lock:
            text = self._sentence_buffer.strip()
            self._sentence_buffer = ""
            self._sentence_timer = None
        if text:
            self.text_queue.put(("final", text))
            if self.translator:
                self.translation_queue.put(text)

    # -------------------- Translation --------------------
    def _translation_worker(self):
        """Translate FINAL captions only. Partials are too noisy and waste quota."""
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
                    self._append_entry(payload, translated=None)
                    changed = True
                elif kind == "partial":
                    self.current_partial = payload
                    changed = True
                elif kind == "translated":
                    self.current_partial = ""
                    source, translated = payload
                    self._update_translation(source, translated)
                    changed = True
        except queue.Empty:
            pass

        if changed:
            self._update_partial_display()

        if self.running:
            self.root.after(100, self._update_caption)

    def _append_entry(self, source_text, translated=None):
        entry = {"source": source_text, "translated": translated}
        self.caption_entries.append(entry)
        self._trim_buffer()

        if SHOW_ONLY_TRANSLATION:
            # Don't show source text. If translation already available, rebuild.
            if translated:
                self._rebuild_text_widgets()
            return

        # Incremental insert for dual-pane mode
        self.left_text.config(state=tk.NORMAL)
        self.right_text.config(state=tk.NORMAL)

        if len(self.caption_entries) > 1:
            self.left_text.insert(tk.END, "\n")
            self.right_text.insert(tk.END, "\n")

        self.left_text.insert(tk.END, source_text)
        if translated:
            self.right_text.insert(tk.END, translated)

        self.left_text.config(state=tk.DISABLED)
        self.right_text.config(state=tk.DISABLED)
        self._scroll_to_end()

    def _update_translation(self, source_text, translated_text):
        # Find the most recent matching entry that hasn't been translated yet
        for i in range(len(self.caption_entries) - 1, -1, -1):
            if self.caption_entries[i]["source"] == source_text and self.caption_entries[i]["translated"] is None:
                self.caption_entries[i]["translated"] = translated_text
                self._rebuild_text_widgets()
                break

    def _rebuild_text_widgets(self):
        if not SHOW_ONLY_TRANSLATION:
            self.left_text.config(state=tk.NORMAL)
            self.left_text.delete("1.0", tk.END)

        self.right_text.config(state=tk.NORMAL)
        self.right_text.delete("1.0", tk.END)

        if SHOW_ONLY_TRANSLATION:
            # Only show entries that have been translated
            translated_entries = [e for e in self.caption_entries if e["translated"]]
            for i, entry in enumerate(translated_entries):
                if i > 0:
                    self.right_text.insert(tk.END, "\n")
                self.right_text.insert(tk.END, entry["translated"])
        else:
            for i, entry in enumerate(self.caption_entries):
                if i > 0:
                    self.left_text.insert(tk.END, "\n")
                    self.right_text.insert(tk.END, "\n")
                self.left_text.insert(tk.END, entry["source"])
                if entry["translated"]:
                    self.right_text.insert(tk.END, entry["translated"])

        if not SHOW_ONLY_TRANSLATION:
            self.left_text.config(state=tk.DISABLED)
        self.right_text.config(state=tk.DISABLED)
        self._scroll_to_end()

    def _trim_buffer(self):
        if len(self.caption_entries) > MAX_BUFFER_LINES:
            remove_count = len(self.caption_entries) - MAX_BUFFER_LINES
            self.caption_entries = self.caption_entries[remove_count:]
            self._rebuild_text_widgets()

    def _scroll_to_end(self):
        self.right_text.see(tk.END)
        if not SHOW_ONLY_TRANSLATION:
            self.left_text.see(tk.END)

    def _update_partial_display(self):
        if self.current_partial:
            self.partial_left.config(text=f"{self.current_partial} …")
        else:
            self.partial_left.config(text="")

    # -------------------- Lifecycle --------------------
    def _fatal_error(self, msg):
        self.right_text.config(state=tk.NORMAL)
        self.right_text.delete("1.0", tk.END)
        self.right_text.insert("1.0", msg)
        self.right_text.config(fg="#ff6666")
        self.right_text.config(state=tk.DISABLED)
        if not SHOW_ONLY_TRANSLATION:
            self.left_text.config(state=tk.NORMAL)
            self.left_text.delete("1.0", tk.END)
            self.left_text.insert("1.0", msg)
            self.left_text.config(fg="#ff6666")
            self.left_text.config(state=tk.DISABLED)
        self.root.after(8000, self.shutdown)

    def shutdown(self):
        self.running = False
        if self._sentence_timer:
            self._sentence_timer.cancel()
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
