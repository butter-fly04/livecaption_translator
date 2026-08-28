#!/usr/bin/env python3
"""
Drop-in replacement for deep-translator.
Mimics the TWP browser extension's approach:
  - Google: translate-pa.googleapis.com with scraped API key
  - Bing:   edge.microsoft.com/translate (no auth needed currently)
  - Yandex: translate.yandex.net/api/v1/tr.json
Includes LRU cache + in-flight dedup + auto-fallback.
"""

import html
import re
import requests
import threading
import time
import hashlib
from typing import List, Union


# ==================== GOOGLE (TWP-STYLE) ====================

class GoogleTWTranslator:
    """
    Uses translate-pa.googleapis.com/v1/translateHtml
    with the same x-goog-api-key scraping that TWP does.
    """
    def __init__(self, source: str = "auto", target: str = "en"):
        self.source = source
        self.target = target
        self._api_key: str = None
        self._auth_time: float = 0
        # Hardcoded fallback from TWP source (public in their repo)
        self._fallback_key = "AIzaSyATBXajvzQLTDHEQbcpq0Ihe0vWDHmO520"
        self._sess = requests.Session()
        self._sess.headers.update({
            "User-Agent": (
                "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
            ),
            "Accept": "application/json",
            "Origin": "https://translate.google.com",
            "Referer": "https://translate.google.com/",
        })

    def _refresh_auth(self):
        if self._api_key and (time.time() - self._auth_time) < 1200:
            return
        try:
            r = self._sess.get(
                "https://translate.googleapis.com/_/translate_http/_/js/"
                "k=translate_http.tr.en_US.YusFYy3P_ro.O/am=AAg/d=1/exm=el_conf/ed=1/"
                "rs=AN8SPfq1Hb8iJRleQqQc8zhdzXmF9E56eQ/m=el_main",
                timeout=10,
            )
            m = re.search(r'"x-goog-api-key"\s*:\s*"([A-Za-z0-9_-]{39})"', r.text)
            self._api_key = m.group(1) if m else self._fallback_key
        except Exception as e:
            print(f"[GoogleTWP] Auth scrape failed ({e}), using fallback key")
            self._api_key = self._fallback_key
        self._auth_time = time.time()

    def translate(self, texts: Union[str, List[str]]) -> List[str]:
        if isinstance(texts, str):
            texts = [texts]
        if not texts:
            return []

        self._refresh_auth()

        # Build HTML payload exactly like TWP
        parts = []
        for i, t in enumerate(texts):
            parts.append(f'<a i={i}>{html.escape(t)}</a>')
        payload = f'<pre>{"".join(parts)}</pre>'

        body = [[payload, self.source, self.target], "te"]

        r = self._sess.post(
            "https://translate-pa.googleapis.com/v1/translateHtml",
            headers={
                "Content-Type": "application/json+protobuf",
                "X-goog-api-key": self._api_key,
            },
            json=body,
            timeout=15,
        )
        r.raise_for_status()
        data = r.json()

        if not data or not data[0]:
            return texts

        translated_html = data[0][0]

        # --- Parse Google's HTML response ---
        # 1. Remove <i>...</i> (original text interleaved by Google)
        cleaned = re.sub(r'<i>.*?</i>', '', translated_html, flags=re.DOTALL)
        # 2. Remove <b> / </b> wrappers
        cleaned = re.sub(r'</?b>', '', cleaned)
        # 3. Remove <pre> wrapper
        cleaned = re.sub(r'</?pre>', '', cleaned)

        # 4. Extract <a i=N>content</a>
        results: dict[int, str] = {}
        for m in re.finditer(r'<a\s+i=(\d+)>(.*?)</a>', cleaned, re.DOTALL):
            idx = int(m.group(1))
            txt = html.unescape(m.group(2)).strip()
            results[idx] = results.get(idx, "") + (" " if idx in results else "") + txt

        return [results.get(i, texts[i]) for i in range(len(texts))]


# ==================== BING ====================

class BingTranslator:
    """
    Uses edge.microsoft.com/translate/translatetext
    No auth token required as of 2024-2025.
    """
    def __init__(self, source: str = "auto-detect", target: str = "en"):
        self.source = source
        self.target = target
        self._sess = requests.Session()
        self._sess.headers.update({
            "User-Agent": (
                "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
            ),
            "Accept": "application/json",
        })

    def translate(self, texts: Union[str, List[str]]) -> List[str]:
        if isinstance(texts, str):
            texts = [texts]
        if not texts:
            return []

        # Language code remapping (TWP does this)
        lang_map = {
            "zh": "zh-Hans", "zh-cn": "zh-Hans", "zh-tw": "zh-Hant",
            "tl": "fil", "hmn": "mww", "ku": "kmr", "ckb": "ku",
            "mn": "mn-Cyrl", "no": "nb", "lg": "lug",
            "sr": "sr-Cyrl", "mni-mtei": "mni",
        }
        target = lang_map.get(self.target.lower(), self.target)
        source = lang_map.get(self.source.lower(), self.source)

        qs = f"?isEnterpriseClient=false&to={target}"
        if source not in ("auto", "auto-detect"):
            qs += f"&from={source}"

        r = self._sess.post(
            f"https://edge.microsoft.com/translate/translatetext{qs}",
            headers={"Content-Type": "application/json"},
            json=texts,
            timeout=15,
        )
        r.raise_for_status()
        data = r.json()
        return [item["translations"][0]["text"] for item in data]


# ==================== YANDEX ====================

class YandexTranslator:
    """
    Uses translate.yandex.net/api/v1/tr.json/translate
    Scrapes a SID from TurboPages proxy.
    """
    def __init__(self, source: str = "auto", target: str = "en"):
        self.source = source
        self.target = target
        self._sid: str = None
        self._sid_time: float = 0
        self._sess = requests.Session()
        self._sess.headers.update({
            "User-Agent": (
                "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
            ),
        })

    def _refresh_sid(self):
        if self._sid and (time.time() - self._sid_time) < 1200:
            return
        try:
            r = self._sess.get(
                "https://translated.turbopages.org/proxy_u/en-es.en/https/example.com/",
                timeout=10,
            )
            m = re.search(r"sid:\s*'([0-9a-f.]+)'", r.text)
            if m and len(m.group(1)) > 7:
                self._sid = m.group(1)
        except Exception as e:
            print(f"[Yandex] SID fetch failed: {e}")
            self._sid = None
        self._sid_time = time.time()

    def translate(self, texts: Union[str, List[str]]) -> List[str]:
        if isinstance(texts, str):
            texts = [texts]
        self._refresh_sid()

        srv = "tr-touch-url" if self._sid and len(self._sid) > 40 else "tr-url-widget"
        lang = (
            f"{self.source}-{self.target}"
            if self.source not in ("auto", "")
            else self.target
        )

        data = [
            ("srv", srv),
            ("id", f"{self._sid}-0-0" if self._sid else "0-0-0"),
            ("format", "html"),
            ("lang", lang),
        ]
        for t in texts:
            data.append(("text", t))

        r = self._sess.post(
            "https://translate.yandex.net/api/v1/tr.json/translate",
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            data=data,
            timeout=15,
        )
        r.raise_for_status()
        return r.json().get("text", texts)


# ==================== SMART WRAPPER ====================

class SmartCaptionTranslator:
    """
    - LRU cache (avoids re-translating identical captions)
    - In-flight deduplication (two identical texts in queue share one HTTP call)
    - Auto-fallback Google -> Bing -> Yandex
    - Min delay between HTTP calls to stay under the radar
    """
    def __init__(self, source: str, target: str, min_delay: float = 0.8):
        self.source = source
        self.target = target
        self.min_delay = min_delay
        self._last_call = 0

        # Cache: sha1(text) -> translation
        self._cache: dict[str, str] = {}
        self._cache_max = 500

        # In-flight tracking: sha1(text) -> list of callbacks waiting
        self._inflight: dict[str, List[threading.Event]] = {}

        self._backends = [
            GoogleTWTranslator(source, target),
            BingTranslator("auto-detect", target),
            YandexTranslator(source, target),
        ]

    def _hash(self, text: str) -> str:
        return hashlib.sha1(text.encode("utf-8")).hexdigest()

    def _enforce_delay(self):
        elapsed = time.time() - self._last_call
        if elapsed < self.min_delay:
            time.sleep(self.min_delay - elapsed)
        self._last_call = time.time()

    def translate(self, text: str) -> str:
        if not text or not text.strip():
            return ""

        h = self._hash(text)

        # 1. Cache hit?
        if h in self._cache:
            return self._cache[h]

        # 2. In-flight dedup?
        if h in self._inflight:
            evt = threading.Event()
            self._inflight[h].append(evt)
            evt.wait(timeout=15)
            return self._cache.get(h, text)

        # 3. Mark in-flight
        self._inflight[h] = []

        result = text
        try:
            self._enforce_delay()

            for backend in self._backends:
                try:
                    results = backend.translate([text])
                    if results and results[0]:
                        result = results[0]
                        break
                except Exception as e:
                    print(f"[SmartCaption] {backend.__class__.__name__} failed: {e}")
                    continue
            else:
                print("[SmartCaption] All backends failed, returning original text")

        finally:
            # Store in cache and wake waiters
            if len(self._cache) >= self._cache_max:
                self._cache.pop(next(iter(self._cache)))
            self._cache[h] = result
            waiters = self._inflight.pop(h, [])
            for evt in waiters:
                evt.set()

        return result
