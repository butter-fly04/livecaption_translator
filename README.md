# livecaption_translator
Live caption generator from system audio output for Linux. Features optional real-time translation via web APIs.

---

## Installation & Setup Guide

There are two ways to install this script. The **Automated Installer (Recommended)** handles everything for you. The **Manual Setup** is also available if you prefer doing things yourself.

### Method 1: Automated Installer (Recommended)

This method uses the `install.sh` script to set up everything automatically: system packages, Python virtual environment, VOSK models, and desktop integration.

#### Step 1: Download and Extract
Download the repository as a ZIP file and extract it, or clone it:
```bash
git clone https://github.com/butter-fly04/livecaption_translator.git
cd livecaption_translator
```

#### Step 2: (Optional) Add Local VOSK Models
If you already have VOSK model `.zip` files downloaded, place them in this folder. The installer will detect and extract them automatically.

#### Step 3: Run the Installer
Execute the installation script:
```bash
chmod +x install.sh
./install.sh
```
The installer will walk you through:
1. Installing required system packages (via `apt`, `dnf`, or `pacman`).
2. Creating a virtual environment in `~/livecaption/venv` and installing Python dependencies.
3. Downloading VOSK models (or extracting local ones).
4. Prompting you to select your default and right-click language pairs.
5. Creating a Desktop Entry in your app menu.

Once finished, you can launch "LiveCaption Translator" directly from your application menu!

---

### Method 2: Manual Setup 

If you prefer not to use the automated installer, you can set everything up manually.

#### Step 1: Install System Dependencies
You will need `python3`, `pip`, `venv`, `tkinter`, and `pulseaudio-utils`. 
On Debian/Ubuntu:
```bash
sudo apt update
sudo apt install python3 python3-pip python3-venv python3-tk pulseaudio-utils
```
On Fedora:
```bash
sudo dnf install python3 python3-pip python3-tkinter pulseaudio-utils
```

#### Step 2: Create a Virtual Environment
```bash
python3 -m venv venv
source venv/bin/activate
```
You should see `(venv)` at the beginning of your terminal prompt.

#### Step 3: Install Python Dependencies
```bash
pip install -r requirements.txt
```

#### Step 4: Download VOSK Models
1. Visit **[https://alphacephei.com/vosk/models](https://alphacephei.com/vosk/models)**
2. Download the model(s) for your language (e.g., English, Spanish, Chinese, Russian)
3. Extract each `.zip` file to a location on your system (e.g., `~/vosk_models/`)

#### Step 5: Configure the Script Manually
Edit `live_caption.py` and update the **VOSK_MODELS** section with the full paths to your extracted models:

```python
VOSK_MODELS = {
    "en": "/home/username/vosk_models/vosk-model-en-us-0.22",
    "es": "/home/username/vosk_models/vosk-model-es-0.42",
    "ru": "/home/username/vosk_models/vosk-model-ru-0.10",
}
```

#### Step 6: Run the Script
```bash
python3 live_caption.py --mode es-en
```

---

## Configuration & Desktop Entry Management

If you used the **Automated Installer**, your installation is managed via configuration files in `~/livecaption/`.

### 1. Changing the Default Language Pair (`config.conf`)
The default language pair used when you left-click the app menu icon is stored in:
`~/livecaption/config.conf`

To change your default source or target language, simply open this file in a text editor:
```ini
[defaults]
source = en-us
target = es

[actions]
# Format: source->target,source->target
pairs = ru->en, es->en-us
```
Save the file. The next time you launch the app, it will use your new default languages.

### 2. Updating the Right-Click Menu (`update-desktop.sh`)
If you want to add, remove, or change the language pairs available in the right-click menu of your app icon:

1. Open `~/livecaption/config.conf` in a text editor.
2. Find the `[actions]` section and edit the `pairs` line. 
   *Example: `pairs = ru->en, es->en-us, fr->en`*
3. Save the file.
4. Run the desktop updater script from your terminal:
```bash
~/livecaption/update-desktop.sh
```
This regenerates the `.desktop` entry, and your right-click menu will instantly reflect the new language pairs!

### 3. The Desktop Entry (`.desktop`)
The installer places a file named `livecaption.desktop` in `~/.local/share/applications/`. 
* **Left-click:** Executes `~/livecaption/launcher.sh` with no arguments, causing it to read `config.conf` and launch your default pair.
* **Right-click (Actions):** Executes `~/livecaption/launcher.sh <source> <target>`, overriding the config file for that specific session.

---

## Command-Line Options

If you are running the script manually (Method 2), or want to create your own custom shortcuts, here are the available arguments:

- `--mode`: Language code from your VOSK_MODELS (e.g., `en`, `es`, `ru`, or `es-en` to enable translation)
- `-d / --device`: PulseAudio source name (default: monitor of default sink)
- `--list-devices`: List audio sources and exit

---

## Customization Options (UI)

All visual customization is done by editing the **"CUSTOMIZE THESE"** section in `live_caption.py`.

| Option | Default | Purpose | Example |
|---|---|---|---|
| `BASE_WIDTH` | `1400` | Window width in pixels | `1400` |
| `BASE_HEIGHT` | `150` | Window height in pixels | `150` |
| `WINDOW_SIZE_PERCENT` | `70` | Scale factor (100 = base, 150 = 1.5x, 50 = 0.5x) | `100` for full size |
| `WINDOW_OPACITY` | `0.93` | Transparency (0.1 = very transparent, 1.0 = opaque) | `0.7` for more transparent |
| `MAX_LINES` | `3` | Maximum number of caption lines displayed at once | `5` |
| `SHOW_ONLY_TRANSLATION` | `True` | Hides the source-text pane and halves window width | `False` to show original text |

**Note:** Opacity may not work on all window managers. Language codes follow ISO 639-1 (2-letter codes: `en` = English, `es` = Spanish, `zh` = Chinese, `ru` = Russian).
