# livecaption_translator
Live caption genarator from system audio output for Linux


### Installation & Setup Guide

### Installation on Linux (Virtual Environment)

### Step 1: Clone the Repository

```bash
git clone https://github.com/butter-fly04/livecaption_translator.git
cd livecaption_translator
```

### Step 2: Create a Virtual Environment

```bash
python3 -m venv venv
```

### Step 3: Activate the Virtual Environment

```bash
source venv/bin/activate
```

You should see `(venv)` at the beginning of your terminal prompt.

### Step 4: Upgrade pip

```bash
pip install --upgrade pip
```

### Step 5: Install Dependencies

```bash
pip install -r requirements.txt
```

---

## Setup

### Step 1: Download VOSK Models

VOSK is an offline speech recognition engine. You need to download language models before using the script.

1. Visit **[https://alphacephei.com/vosk/models](https://alphacephei.com/vosk/models)**
2. Download the model(s) for your language (e.g., English, Spanish, Chinese, Russian)
3. Extract each `.zip` file to a location on your system (e.g., `~/vosk_models/`)

Example directory structure:
```
~/vosk_models/
├── vosk-model-en-us-0.22/
├── vosk-model-es-0.42/
└── vosk-model-ru-0.10/
```

### Step 2: Configure the Script

Edit `live_caption.py` and update the **VOSK_MODELS** section with the full paths to your extracted models:

```python
VOSK_MODELS = {
    "en": "/home/username/vosk_models/vosk-model-en-us-0.22",
    "es": "/home/username/vosk_models/vosk-model-es-0.42",
    "ru": "/home/username/vosk_models/vosk-model-ru-0.10",
}
```

Use **absolute paths** (starting with `/home/...`), not relative paths.

### Step 3: Run the Script

```bash
python3 live_caption.py --mode es-en
```

Common command-line options:
- `--mode`: Language code from your VOSK_MODELS (e.g., `en`, `es`, `ru`)

---

## Customization Options

All customization is done by editing the **"CUSTOMIZE THESE"** section in `live_caption.py`.

### Window Appearance

| Option | Default | Purpose | Example |
|---|---|---|---|
| `BASE_WIDTH` | `1400` | Window width in pixels | `1400` |
| `BASE_HEIGHT` | `150` | Window height in pixels | `150` |
| `WINDOW_SIZE_PERCENT` | `70` | Scale factor (100 = base, 150 = 1.5x, 50 = 0.5x) | `100` for full size |
| `WINDOW_OPACITY` | `0.93` | Transparency (0.1 = very transparent, 1.0 = opaque) | `0.7` for more transparent |

**Note:** Opacity may not work on all window managers 

---

### Caption Behavior

| Option | Default | Purpose |
|---|---|---|
| `MAX_LINES` | `3` | Maximum number of caption lines displayed at once |
| `FADE_AFTER_SECONDS` | `15` | Seconds before captions automatically fade out |


---

### VOSK Models (Speech Recognition Languages)

The `VOSK_MODELS` dictionary maps language codes to model paths. Add or modify entries for languages you want to recognize:

```python
VOSK_MODELS = {
    "en": "/home/username/vosk_models/vosk-model-en-us-0.22",
    "es": "/home/username/vosk_models/vosk-model-es-0.42",
    "ru": "/home/username/vosk_models/vosk-model-ru-0.10",
    "fr": "/home/username/vosk_models/vosk-model-fr-0.22",
    "de": "/home/username/vosk_models/vosk-model-de-0.21",
    "zh": "/home/username/vosk_models/vosk-model-cn-0.22",
}
```

**Language codes** follow ISO 639-1 (2-letter codes):
- `en` = English
- `es` = Spanish
- `fr` = French
- `de` = German
- `ru` = Russian
- `zh` = Chinese
- `ja` = Japanese
- `pt` = Portuguese
- `ar` = Arabic

See the **[full list of ISO 639-1 codes](https://en.wikipedia.org/wiki/List_of_ISO_639-1_codes)**.

---
