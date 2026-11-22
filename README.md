# Live Caption

[![Build AppImage](https://github.com/butter-fly04/livecaption_translator/actions/workflows/build.yml/badge.svg)](https://github.com/butter-fly04/livecaption_translator/actions/workflows/build.yml)
[![License: GPL-3.0-only](https://img.shields.io/badge/license-GPL--3.0--only-blue.svg)](LICENSE)

Real-time captions for the audio your computer is playing, with optional live
translation. Speech recognition runs locally with
[sherpa-onnx](https://github.com/k2-fsa/sherpa-onnx); only translation uses the
network (and can be disabled).

The project is a Tkinter UI, a small PulseAudio/PipeWire capture layer, and a
sherpa-onnx backend. Application code lives in `src/livecaption/`; AppImage
packaging assets and build scripts live in `packaging/`.

---

## Repository layout

```text
.
├── .github/workflows/      CI build and release workflows
├── examples/               Example user configuration
├── packaging/              AppImage assets, PyInstaller spec, build scripts
├── src/livecaption/        Application source package
├── LICENSE                 GNU General Public License v3.0
├── pyproject.toml          Package metadata and console entry point
├── requirements.txt        Runtime dependencies
├── requirements-build.txt  Runtime + PyInstaller build dependencies
└── README.md
```

Run from a checkout with `python -m livecaption` after installing the package
(see [Development](#development)), or build an AppImage with the scripts in
`packaging/`.

---

## Download and run

Each release contains two AppImages. Pick the one that matches your system:

| Your system | Download | Notes |
|---|---|---|
| Ubuntu 24.04+, Linux Mint 22.x+, Debian 13+, Arch, or unsure | `LiveCaption-<version>-ubuntu24.04-x86_64.AppImage` | Portable build. **Recommended for most users.** |
| Fedora (current release) | `LiveCaption-<version>-fedora-x86_64.AppImage` | Native Fedora build (Fedora glibc and Tk). |

Both files contain the same application and features. The Ubuntu build is the
safe default; the Fedora build is for current Fedora systems and will not start
on distributions with an older glibc. Ubuntu 22.04 and older cannot run the
Ubuntu 24.04 build, so build from source there.

Download from the
[latest release](https://github.com/butter-fly04/livecaption_translator/releases/latest),
then:

```bash
chmod +x LiveCaption-*.AppImage
./LiveCaption-*.AppImage
```

If your distribution does not have FUSE 2 (common on Ubuntu 22.04+ and some
containers), use the built-in fallback:

```bash
./LiveCaption-*.AppImage --appimage-extract-and-run
```

The first launch opens **Settings → Speech models** because no speech model is
bundled. Download a model there (or point the Models table at an extracted
sherpa-onnx folder), press **Apply**, and captions start.

### First-run model download

The downloader offers streaming models from the k2-fsa/sherpa-onnx releases:

- language and model type
- archive size
- SHA-256 verification when the release asset provides a digest
- safe extraction (archive paths and links cannot escape the model folder)
- automatic Models and Language-pairs rows

The catalogue is fetched live from GitHub when available, with a built-in
fallback list. You can add your own entries in
`~/.local/share/live-caption/catalog.json`.

Models are third-party files with their own licences; check the licence of any
model before redistributing it.

### Desktop integration

The AppImage contains a `.desktop` file and icons, but a plain AppImage does not
install them by itself. Use [AppImageLauncher](https://github.com/TheAssassin/AppImageLauncher)
or `appimaged` if you want a menu entry and file-manager integration. Without
one of those, run the AppImage directly; the tray/menu still works.

A “start at login” option is not implemented yet. If you add one, the
`.desktop` file should launch `$APPIMAGE`, which the runtime sets to the real
path of the AppImage.

---

## Where things live

| What | Where | Notes |
|---|---|---|
| App, Python, Tk, onnxruntime | inside the AppImage | read-only, replaced on update |
| User config | `~/.config/live-caption/config.json` | honours `$XDG_CONFIG_HOME` |
| Speech models | `~/.local/share/live-caption/models/<name>/` | honours `$XDG_DATA_HOME` |
| Download cache / scratch | inside the models folder during install | removed after success/failure |
| Optional user catalogue | `~/.local/share/live-caption/catalog.json` | extra download entries |

Overrides for tests and portable setups:

```bash
LIVE_CAPTION_CONFIG_DIR=/some/place \
LIVE_CAPTION_DATA_DIR=/some/place \
./LiveCaption-*.AppImage
```

The app never writes next to the executable or inside `$APPDIR`. An existing
legacy `live_caption_config.json` placed next to `src/livecaption/paths.py` is
copied to the XDG config location on first run. A starting-point file is also
provided at `examples/config.example.json`; copy it to
`~/.config/live-caption/config.json` if you want those values.

---

## Command line

```bash
./LiveCaption-*.AppImage --help
./LiveCaption-*.AppImage --version
./LiveCaption-*.AppImage --self-test
./LiveCaption-*.AppImage --list-devices
./LiveCaption-*.AppImage --mode zh-en
```

`--self-test` is useful after a build or when troubleshooting: it checks Python
imports, Tk, the tray module and DBus session, config/model folder permissions,
`libpulse-simple`, `parec`, and the configured models. Audio and display issues
are warnings so it can run in headless CI.

---

## Build with your system Python (quick local build)

This is the fastest path and is what `packaging/build-appimage.sh` does. It uses
the Python you point it at; PyInstaller and the runtime packages must already be
installed.

### Fedora

```bash
sudo dnf install python3-tkinter python3-devel libpulse zsync file curl ca-certificates desktop-file-utils binutils
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements-build.txt
PYTHON="$PWD/.venv/bin/python" packaging/build-appimage.sh
```

### Debian / Ubuntu / Mint

```bash
sudo apt install python3-venv python3-tk libpython3-dev libpulse0 zsync file curl ca-certificates desktop-file-utils binutils
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements-build.txt
PYTHON="$PWD/.venv/bin/python" packaging/build-appimage.sh
```

The result is:

```text
dist/LiveCaption-<version>[-<suffix>]-x86_64.AppImage
dist/LiveCaption-<version>[-<suffix>]-x86_64.AppImage.sha256
dist/LiveCaption-<version>[-<suffix>]-x86_64.AppImage.zsync   # if zsyncmake is installed
```

Without `SUFFIX` the name has no variant label; the release scripts add
`fedora` or `ubuntu24.04`.

The version comes from `git describe --tags --always` (without a `-dirty`
suffix), falling back to `__version__` in `src/livecaption/__init__.py` when
building from an archive without `.git`. To give the artifact an explicit
variant label, use `SUFFIX`:

```bash
PYTHON="$PWD/.venv/bin/python" SUFFIX=fedora packaging/build-appimage.sh
```

On Fedora, build both release variants in one go:

```bash
PYTHON="$PWD/.venv/bin/python" packaging/build-both.sh
```

This produces:

```text
dist/LiveCaption-<version>-fedora-x86_64.AppImage
    Fedora-native build (Fedora glibc and Tk).  For current Fedora.
dist/LiveCaption-<version>-ubuntu24.04-x86_64.AppImage
    Ubuntu 24.04 container build (glibc 2.39).  Portable; recommended.
```

On a non-Fedora host, build the Fedora variant in a Fedora container:

```bash
DOCKERFILE=Dockerfile.fedora IMAGE=live-caption-build:fedora \
    SUFFIX=fedora SMOKE_TEST=1 packaging/build-in-container.sh
```

Smoke-test it without touching your real settings:

```bash
packaging/smoke-test.sh dist/LiveCaption-*.AppImage
```

### Important: glibc and Tk compatibility

An AppImage bundles Python and libraries, but **not glibc**. A build made on
Fedora 44 (glibc 2.43) will only run on systems with glibc 2.43 or newer. It is
fine for local use, but it will not run on Linux Mint 22.3 or Ubuntu 24.04
(glibc 2.39).

The bundled Tk also matters for HiDPI: current Fedora ships **Tk 9**, which
reads `Xft.dpi` and scales point-sized fonts automatically; the Ubuntu 24.04
build bundles **Tk 8.6**, which does not. That is why the two builds are kept
separate: the native build keeps the host Tk behaviour (Tk 9 on current
Fedora), and the Ubuntu build is the portable one.

For a distributable artifact, build on the oldest target you support. This repo
ships two container recipes: Ubuntu 24.04 for the portable build and Fedora for
the native build.

```bash
# Portable Ubuntu 24.04 build
packaging/build-in-container.sh          # docker
CONTAINER=podman packaging/build-in-container.sh

# Fedora-native build
DOCKERFILE=Dockerfile.fedora IMAGE=live-caption-build:fedora \
    SUFFIX=fedora SMOKE_TEST=1 packaging/build-in-container.sh
```

`SMOKE_TEST=1` runs `packaging/smoke-test.sh` inside the same container, which
is required for the Fedora build when the host is not Fedora: its glibc is too
new for an Ubuntu host to run directly.

To support older distros, change the `FROM` line in
`packaging/Dockerfile` to an older base (for example Ubuntu 22.04 or a
`manylinux_2_28` image) and rebuild. Do not build release artifacts on a rolling
distribution unless that is the only target.

The build script downloads `appimagetool` into `packaging/.cache/` and unpacks it
there. Unpacking never needs FUSE, so the container build works without
`--privileged` or `/dev/fuse`.

---

## AppImage layout

PyInstaller runs in **onedir** mode, not onefile. Onefile would unpack the whole
runtime to `/tmp` on every start; the AppImage already provides a single
read-only file.

```text
LiveCaption-<version>-<variant>-x86_64.AppImage
└── squashfs-root/
    ├── AppRun
    ├── live-caption.desktop
    ├── live-caption.png
    ├── .DirIcon
    └── usr/
        ├── bin/live-caption/
        │   ├── live-caption          # PyInstaller launcher
        │   └── _internal/            # Python, Tk, numpy, Pillow, sherpa libs
        └── share/
            ├── applications/live-caption.desktop
            ├── icons/hicolor/...
            └── metainfo/live-caption.appdata.xml
```

The spec collects `sherpa_onnx` with `collect_all`, because its native
`libonnxruntime.so` / `libsherpa-onnx-*.so` files must keep the package layout
their rpath expects. `caption_tray` and Pillow are explicit hidden imports:
`caption_tray` is imported lazily, and Pillow is imported inside
`caption_tray._render_icon()`.

`AppRun` prepends the bundle and its `_internal` directories to
`PATH`/`LD_LIBRARY_PATH`, then forwards all arguments to the launcher.

---

## What is not bundled

| Thing | Why | Behaviour |
|---|---|---|
| glibc | must match the host | build on an old enough base |
| PipeWire / PulseAudio server | system service | detect and explain at startup |
| `parec` | part of the audio stack | `libpulse-simple` is used first; `parec` is a fallback |
| XWayland | Tk is an X11 toolkit | KDE/GNOME provide it by default |
| `kdialog`, `zenity` | optional folder pickers | Tk file dialog fallback |
| `kscreen-doctor` | optional KDE scale detection | other scale detection fallback |
| Speech models | hundreds of MB, separate licences | download or import on first run |

`audio_capture.py` loads `libpulse-simple` through `ctypes` first. If that
library is missing it falls back to `parec`; if neither exists it reports a
clear error. This avoids making the AppImage depend on the `parec` executable
while still working on systems where only PulseAudio/PipeWire libraries are
installed.

---

## Test matrix

The release workflow runs the automated smoke test on Ubuntu. The rest of the
matrix is manual; record the result and the AppImage SHA-256 for each release.
See [`packaging/TESTING.md`](packaging/TESTING.md) for the full checklist and
the commands used to inspect the bundled libraries.

| System | Session | Audio | Status |
|---|---|---|---|
| Fedora 44 | KDE Wayland | PipeWire | local build tested |
| Ubuntu 24.04 | X11 / Wayland | PipeWire | CI build + smoke test |
| Ubuntu 22.04 | X11 / Wayland | PipeWire / Pulse | CI build + smoke test |
| Linux Mint 22.3 | X11 / Wayland | PipeWire / Pulse | manual |
| Debian 12 | X11 / Wayland | PipeWire / Pulse | manual |
| Arch Linux | Wayland | PipeWire | manual |

For each system check:

- `--version`, `--self-test`, `--list-devices`
- first run with no config and no models
- download one model, restart, captions still work
- import an existing model folder
- Settings changes survive restart
- tray icon/menu (Plasma) or graceful fallback (GNOME)
- `--appimage-extract-and-run` on a system without FUSE 2
- 125% / 150% display scaling

`packaging/smoke-test.sh` covers the headless, no-FUSE and fresh-XDG parts
automatically. It uses temporary config/model folders.

---

## CI and releases

The repository has two workflows:

- `.github/workflows/build.yml` — builds and smoke-tests Ubuntu 22.04, Ubuntu
  24.04, and Fedora (in a Fedora container) for pushes and pull requests.
- `.github/workflows/release.yml` — on a `v*` tag, builds both release variants,
  attaches both AppImages with their `.sha256` files (and the Ubuntu `.zsync`)
  to the GitHub release, and embeds AppImageUpdate information in the Ubuntu
  build.

Release assets are named explicitly:

```text
LiveCaption-<version>-ubuntu24.04-x86_64.AppImage   portable; recommended
LiveCaption-<version>-fedora-x86_64.AppImage        Fedora-native
```

### Delta updates

`build-appimage.sh` creates a `.zsync` file when `zsyncmake` is available. The
release workflow passes:

```text
gh-releases-zsync|<owner>|<repo>|latest|LiveCaption-*-ubuntu24.04-x86_64.AppImage.zsync
```

Only the portable Ubuntu build embeds update information, so AppImageUpdate
always follows that variant. Users with
[AppImageUpdate](https://github.com/AppImageCommunity/AppImageUpdate) can update
in place. Without an update tool, users download the new AppImage manually;
config and models are untouched because they live in XDG folders.

---

## Troubleshooting

### The AppImage does not start

Try:

```bash
./LiveCaption-*.AppImage --appimage-extract-and-run --self-test
```

If that works, install FUSE 2 (`libfuse2t64` on Ubuntu 24.04, `fuse-libs` on
Fedora) or keep using the extract-and-run flag.

### No audio / “Could not find a system audio monitor source”

- Check that PipeWire or PulseAudio is running.
- Run `--list-devices` and pass a source with `--device`.
- The default is the monitor of the default sink, which captures what you hear.
- If `libpulse-simple` is missing, install the PulseAudio client library; the
  app falls back to `parec` only if that exists.

### No tray icon

The tray uses StatusNotifierItem over DBus. It is native on Plasma. GNOME needs
an AppIndicator/StatusNotifier extension; the right-click menu on the caption
window still works.

### Model download fails

- The catalogue falls back to a built-in list if GitHub is unreachable.
- Paste a direct `.tar.bz2` / `.tar.gz` URL in the download dialog.
- Or download/extract a model yourself and add the folder in
  **Settings → Speech models**.

### Build fails with “On Linux, objdump is required”

PyInstaller needs `binutils`. Install it (`sudo dnf install binutils` or
`sudo apt install binutils`), or use the container build, which includes it.

### Build fails with “Python shared library … was not found”

Debian and Ubuntu ship the interpreter's shared library in a separate package.
Install `libpython3-dev` (Debian/Ubuntu) or `python3-devel` (Fedora), or use the
container build.

### Build fails with “fuse: device not found”

This comes from running `appimagetool` itself, which is an AppImage. The build
script unpacks it into `packaging/.cache/appimagetool-extracted/` and runs that
copy, so no FUSE is needed. If you still see it, delete `packaging/.cache/` and
rebuild, and make sure `packaging/.cache` is writable.

### Settings do not persist

Check that `~/.config/live-caption/` is writable. The AppImage itself is
read-only by design; nothing is written next to it. Use
`LIVE_CAPTION_CONFIG_DIR` to choose another location.

### Reset everything

```bash
rm -rf ~/.config/live-caption ~/.local/share/live-caption
```

This removes settings and downloaded models, not the AppImage.

---

## Development

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -e .

# Run from source
python -m livecaption

# Check the runtime without opening the window
python -m livecaption --self-test
```

If you prefer not to install the package, put `src/` on the import path:

```bash
PYTHONPATH=src python -m livecaption
```

The app is intentionally split into small modules, all under
`src/livecaption/`:

| File | Responsibility |
|---|---|
| `live_caption.py` | backend, CLI, recognizer lifecycle, settings schema |
| `caption_ui.py` | caption window, menus, config persistence |
| `settings_ui.py` | schema-driven settings form |
| `breeze_theme.py` | Breeze-like ttk theme and scaling |
| `caption_tray.py` | StatusNotifierItem / DBusMenu tray |
| `caption_translators.py` | translation engines |
| `audio_capture.py` | libpulse-simple capture with `parec` fallback |
| `paths.py` | XDG config/data locations and resource dir |
| `model_manager.py` | catalogue, download, checksum, safe extraction |
| `model_dialog.py` | download dialog |

The `packaging/` directory contains the PyInstaller spec, AppRun, icons,
desktop/AppStream metadata, Dockerfile, and build/test scripts.

---

## Licence

Live Caption is free software: you can redistribute it and/or modify it under
the terms of the GNU General Public License as published by the Free Software
Foundation, version 3 of the License.

This program is distributed in the hope that it will be useful, but **without
any warranty**; without even the implied warranty of merchantability or fitness
for a particular purpose. See the [GNU General Public License v3.0](LICENSE)
for the full text.

PyInstaller bundles Python, Tk, numpy, Pillow, requests, dbus-next, pulsectl,
sherpa-onnx and onnxruntime; each keeps its own licence. Speech models are
**not** bundled and are covered by the licence of the model you download.
