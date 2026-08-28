```python
#!/usr/bin/env python3
"""
Live Caption Generator for Linux
Captures system audio output and transcribes in real-time using VOSK.
Optional real-time translation with multi-service fallback chain.
Side-by-side layout.
"""

import argparse
import json
import os
import queue
import random
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

try:
    import requests
except ImportError:
    print("Missing dependency: requests  (pip3 install requests)")
    sys.exit(1)


# ==================== CUSTOMIZE THESE ====================
BASE_WIDTH = 1400
BASE_HEIGHT = 150
WINDOW_SIZE_PERCENT = 70
WINDOW_OPACITY = 0.93
BG_COLOR = "#1a1a1a"
FG_COLOR = "#ffffff"
FONT_FAMILY = "Noto Sans"
FONT_SIZE = 10
FONT_WEIGHT = "bold"
MAX_LINES = 3
FADE_AFTER_SECONDS = 15

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))

VOSK_MODELS = {
    "ru": "/path/to/vosk/medel/vosk-model-small-ru-0.22",
    "zh": "/path/to/vosk/medel/vosk-model-small-cn-0.22",
    "es": "/path/to/vosk/medel/vosk-model-small-es-0.42",
    "ar": "/path/to/vosk/medel/vosk-model-ar-mgb2-0.4",
}

# ==================== TRANSLATION CONFIG ====================
# Services to use, in order of preference.
# MyMemory is the primary, Google is the fallback.
TRANSLATION_SERVICES = ["mymemory", "google"]

# Per-service rate limiting (seconds between requests to the SAME service)
MIN_DELAY = 1.0
MAX_DELAY = 2.5

# Circuit breaker: after this many consecutive failures, stop trying the service
# for CIRCUIT_RESET seconds, then try again.
MAX_FAILURES = 3
CIRCUIT_RESET = 60  # seconds

# Cache
CACHE_SIZE = 500

SAMPLE_RATE = 16000
CHUNK_BYTES = 4096


# ==================== ERROR CLASSES ====================

class TranslationError(Exception):
    """Base translation error."""
    pass

class RateLimitError(TranslationError):
    """Service returned 429 or equivalent."""
    pass

class ServiceError(TranslationError):
    """Service returned an error."""
    pass


# ==================== CACHE ====================

class TranslationCache:
    """Thread-safe cache for translations."""

    def __init__(self, max_size=500):
        self.max_size = max_size
        self._cache = {}
        self._lock = threading.Lock()

    def get(self, key):
        with self._lock:
            return self._cache.get(key)

    def set(self, key, value):
        with self._lock:
            if len(self._cache) >= self.max_size:
                oldest = next(iter(self._cache))
                del self._cache[oldest]
            self._cache[key] = value


# ==================== TRANSLATION SERVICES ====================

class TranslationService:
    """Base class for translation services with circuit breaker."""

    def __init__(self, name, from_code, to_code):
        self.name = name
        self.from_code = from_code
        self.to_code = to_code
        self.session = requests.Session()
        # NO automatic retries — we handle everything manually
        self.failure_count = 0
        self.circuit_open_until = 0
        self.last_request_time = 0
        self._lock = threading.Lock()

    def is_available(self):
        """Check if circuit breaker allows this service."""
        if time.time() < self.circuit_open_until:
            remaining = self.circuit_open_until - time.time()
            return False
        return True

    def _wait_rate_limit(self):
        """Enforce per-service rate limiting."""
        with self._lock:
            now = time.time()
            elapsed = now - self.last_request_time
            delay = random.uniform(MIN_DELAY, MAX_DELAY)
            if elapsed < delay:
                time.sleep(delay - elapsed)
            self.last_request_time = time.time()

    def record_success(self):
        """Reset failure count on success."""
        self.failure_count = 0
        self.circuit_open_until = 0

    def record_failure(self):
        """Increment failure count and maybe open circuit."""
        self.failure_count += 1
        if self.failure_count >= MAX_FAILURES:
            self.circuit_open_until = time.time() + CIRCUIT_RESET
            print(f"  [{self.name}] Circuit breaker OPEN for {CIRCUIT_RESET}s "
                  f"(failures: {self.failure_count})")

    def translate(self, text):
        """Override in subclass."""
        raise NotImplementedError


class MyMemoryTranslator(TranslationService):
    """MyMemory translation API — free, 5000 words/day without key."""

    def __init__(self, from_code, to_code):
        super().__init__("MyMemory", from_code, to_code)
        self.url = "https://api.mymemory.translated.net/get"

    def translate(self, text):
        self._wait_rate_limit()

        params = {
            "q": text,
            "langpair": f"{self.from_code}|{self.to_code}",
        }

        try:
            response = self.session.get(self.url, params=params, timeout=8)

            if response.status_code == 429:
                raise RateLimitError("MyMemory returned 429")

            response.raise_for_status()
            data = response.json()

            # Check for errors in response
            status = data.get("responseStatus", 200)
            if status != 200:
                raise ServiceError(f"MyMemory error: status={status}")

            translated = data.get("responseData", {}).get("translatedText", "")
            if not translated:
                raise ServiceError("MyMemory returned empty translation")

            return translated

        except RateLimitError:
            raise
        except ServiceError:
            raise
        except Exception as e:
            raise ServiceError(f"MyMemory error: {e}")


class GoogleTranslator(TranslationService):
    """Google Translate free web endpoint."""

    # Rotate between endpoints
    ENDPOINTS = [
        "https://translate.google.com/translate_a/single",
        "https://translate.googleapis.com/translate_a/single",
    ]

    HEADERS = [
        {
            "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                          "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.5",
            "Referer": "https://translate.google.com/",
        },
        {
            "User-Agent": "Mozilla/5.0 (X11; Linux x86_64; rv:121.0) Gecko/20100101 Firefox/121.0",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.5",
            "Referer": "https://translate.google.com/",
        },
    ]

    def __init__(self, from_code, to_code):
        gmap = {"zh": "zh-CN", "zh-cn": "zh-CN", "zh-tw": "zh-TW"}
        super().__init__("Google", from_code, to_code)
        self.from_code = gmap.get(from_code.lower(), from_code) if from_code else "auto"
        self.to_code = gmap.get(to_code.lower(), to_code)
        self._endpoint_idx = 0
        self._header_idx = 0

        # Initialize session with cookies by visiting the main page
        try:
            self.session.get("https://translate.google.com/", timeout=5)
        except Exception:
            pass

    def translate(self, text):
        self._wait_rate_limit()

        endpoint = self.ENDPOINTS[self._endpoint_idx]
        self._endpoint_idx = (self._endpoint_idx + 1) % len(self.ENDPOINTS)

        headers = self.HEADERS[self._header_idx].copy()
        self._header_idx = (self._header_idx + 1) % len(self.HEADERS)

        params = {
            "client": "gtx",
            "sl": self.from_code,
            "tl": self.to_code,
            "dt": "t",
            "q": text,
        }

        try:
            response = self.session.get(
                endpoint, params=params, headers=headers, timeout=8
            )

            # Check for rate limiting FIRST — don't retry, just raise
            if response.status_code == 429:
                raise RateLimitError(f"Google returned 429")

            if response.status_code == 503:
                raise ServiceError(f"Google returned 503 (service unavailable)")

            response.raise_for_status()

            # Check content type — Google sometimes returns HTML on errors
            content_type = response.headers.get("Content-Type", "")
            if "json" not in content_type:
                raise ServiceError(f"Google returned non-JSON response")

            data = response.json()
            if not data or not data[0]:
                raise ServiceError("Google returned empty response")

            # Parse: [[["translated", "original", ...], ...], ...]
            translated_parts = []
            for part in data[0]:
                if part and len(part) > 0:
                    translated_parts.append(str(part[0]))

            result = "".join(translated_parts)
            if not result.strip():
                raise ServiceError("Google returned empty translation")

            return result

        except RateLimitError:
            raise
        except ServiceError:
            raise
        except requests.exceptions.Timeout:
            raise ServiceError("Google timeout")
        except Exception as e:
            raise ServiceError(f"Google error: {e}")


# ==================== TRANSLATION MANAGER ====================

class TranslationManager:
    """
    Manages multiple translation services with automatic fallback.
    Tries services in order. On failure (429, timeout, error),
    immediately falls back to the next service.
    Circuit breaker prevents hammering a failing service.
    """

    def __init__(self, from_code, to_code, service_order=None):
        self.from_code = from_code
        self.to_code = to_code
        self.cache = TranslationCache(CACHE_SIZE)

        if service_order is None:
            service_order = TRANSLATION_SERVICES

        # Build service instances
        self.services = {}
        for name in service_order:
            if name == "mymemory":
                self.services["mymemory"] = MyMemoryTranslator(from_code, to_code)
            elif name == "google":
                self.services["google"] = GoogleTranslator(from_code, to_code)

        self.order = [s for s in service_order if s in self.services]

        print(f"[Translation] Services: {' → '.join(self.order)}")
        print(f"[Translation] Rate limit: {MIN_DELAY}-{MAX_DELAY}s per service")
        print(f"[Translation] Circuit breaker: {MAX_FAILURES} failures → {CIRCUIT_RESET}s cooldown")
        print(f"[Translation] Cache: {CACHE_SIZE} entries")

    def translate(self, text):
        """Translate with fallback chain."""
        if not text or not text.strip():
            return text

        # Skip very short text
        if len(text.strip()) < 2:
            return text

        # Check cache
        cache_key = f"{self.from_code}:{self.to_code}:{text}"
        cached = self.cache.get(cache_key)
        if cached:
            return cached

        # Try each service in order
        for service_name in self.order:
            service = self.services[service_name]

            # Check circuit breaker
            if not service.is_available():
                continue

            try:
                result = service.translate(text)
                if result and result.strip():
                    service.record_success()
                    self.cache.set(cache_key, result)
                    return result
                else:
                    raise ServiceError(f"{service_name} returned empty")

            except RateLimitError:
                service.record_failure()
                print(f"  [{service_name}] 429 rate limited → trying next service")
                continue

            except ServiceError as e:
                service.record_failure()
                print(f"  [{service_name}] {e} → trying next service")
                continue

            except Exception as e:
                service.record_failure()
                print(f"  [{service_name}] Unexpected: {e} → trying next service")
                continue

        # All services failed
        print(f"  [Translation] ALL services failed for: {text[:40]}...")
        return text


# ==================== APP ====================

class LiveCaptionApp:
    def __init__(self, model_path, source_name=None, translate_from="en", translate_to=None):
        self.model_path = model_path
        self.source_name = source_name
        self.running = True

        self.audio_queue = queue.Queue(maxsize=200)
        self.text_queue = queue.Queue()
        self.translation_queue = queue.Queue()

        # Track pending translations to avoid duplicates
        self.pending_translations = set()
        self._pending_lock = threading.Lock()

        self.caption_lines = []
        self.current_partial = ""
        self.last_speech_time = time.time()

        # Create translator
        self.translator = None
        if translate_to:
            self.translator = TranslationManager(translate_from, translate_to)
            print(f"[Translation] Enabled: {translate_from} → {translate_to}")

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
        self.root.withdraw()

        self.root.overrideredirect(True)
        self.root.attributes("-topmost", True)
        self.caption_font = tkfont.Font(family=FONT_FAMILY, size=FONT_SIZE, weight=FONT_WEIGHT)

        self.caption_frame = tk.Frame(self.root, bg=BG_COLOR)
        self.caption_frame.pack(expand=True, fill="both", padx=20, pady=10)

        self.left_labels = []
        self.right_labels = []

        for i in range(MAX_LINES):
            left = tk.Label(
                self.caption_frame, text="", font=self.caption_font,
                bg=BG_COLOR, fg=FG_COLOR, anchor="w", justify="left",
            )
            left.grid(row=i, column=0, sticky="nsew", padx=(0, 10))

            right = tk.Label(
                self.caption_frame, text="", font=self.caption_font,
                bg=BG_COLOR, fg=FG_COLOR, anchor="w", justify="left",
            )
            right.grid(row=i, column=1, sticky="nsew", padx=(10, 0))

            self.left_labels.append(left)
            self.right_labels.append(right)

        self.caption_frame.grid_columnconfigure(0, weight=1, uniform="col")
        self.caption_frame.grid_columnconfigure(1, weight=1, uniform="col")

        self.root.bind("<Button-1>", self._start_drag)
        self.root.bind("<B1-Motion>", self._on_drag)
        self.root.bind("<Button-3>", self._show_menu)
        self.menu = tk.Menu(
            self.root, tearoff=0, bg="#2d2d2d", fg="#ffffff",
            activebackground="#444444", activeforeground="#ffffff",
        )
        self.menu.add_command(label="Increase Font", command=self._increase_font)
        self.menu.add_command(label="Decrease Font", command=self._decrease_font)
        self.menu.add_separator()
        self.menu.add_command(label="Exit", command=self.shutdown)
        self.root.bind("<Escape>", lambda e: self.shutdown())

    def _set_status(self, text):
        for i in range(MAX_LINES):
            if i == MAX_LINES // 2:
                self.left_labels[i].config(text=text)
            else:
                self.left_labels[i].config(text="")
            self.right_labels[i].config(text="")

    def _apply_appearance(self):
        scale = WINDOW_SIZE_PERCENT / 100.0
        w = int(BASE_WIDTH * scale)
        h = int(BASE_HEIGHT * scale)

        sw = self.root.winfo_screenwidth()
        sh = self.root.winfo_screenheight()
        x = (sw - w) // 2
        y = sh - h - 60

        self.root.geometry(f"{w}x{h}+{x}+{y}")
        self.root.configure(bg=BG_COLOR)
        self.caption_frame.configure(bg=BG_COLOR)

        self.root.deiconify()
        self.root.wait_visibility()
        self.root.attributes("-alpha", WINDOW_OPACITY)

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
                            with self._pending_lock:
                                if text not in self.pending_translations:
                                    self.pending_translations.add(text)
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

                if len(text.strip()) < 2:
                    with self._pending_lock:
                        self.pending_translations.discard(text)
                    continue

                translated = self.translator.translate(text)

                with self._pending_lock:
                    self.pending_translations.discard(text)

                self.text_queue.put(("translated", (text, translated)))

            except queue.Empty:
                continue
            except Exception as e:
                print(f"Translation worker error: {e}")
                with self._pending_lock:
                    pass

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
    if "-" in mode_str:
        source, target = mode_str.rsplit("-", 1)
        return source, target
    return mode_str, None


def main():
    parser = argparse.ArgumentParser(description="Live Caption Generator for Linux")
    parser.add_argument(
        "--mode", default="en",
        help="Language mode: SOURCE-TARGET (e.g., 'ru-en', 'es-en') or just SOURCE (e.g., 'en').",
    )
    parser.add_argument("-d", "--device", help="PulseAudio source name")
    parser.add_argument("--list-devices", action="store_true", help="List audio sources and exit")
    args = parser.parse_args()

    if args.list_devices:
        list_sources()
        return

    source_lang, target_lang = parse_mode(args.mode)

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
```
