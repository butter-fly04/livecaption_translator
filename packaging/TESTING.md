# AppImage test checklist

Two artifacts are built and kept in `dist/`:

- `LiveCaption-<version>-fedora-x86_64.AppImage` — Fedora-native build, using
  Fedora's Python and Tk. Runs only on systems whose glibc is at least as new
  as the build Fedora release's.
- `LiveCaption-<version>-ubuntu24.04-x86_64.AppImage` — Ubuntu 24.04 container
  build (glibc 2.39). This is the portable/release artifact and the recommended
  download for most users.

Run the automated smoke test on each in an environment that can execute it:

```bash
# Portable build: any current Linux host
packaging/smoke-test.sh dist/LiveCaption-*-ubuntu24.04-x86_64.AppImage

# Fedora build: on Fedora, or inside the Fedora build container
packaging/smoke-test.sh dist/LiveCaption-*-fedora-x86_64.AppImage
```

It uses temporary `LIVE_CAPTION_CONFIG_DIR` / `LIVE_CAPTION_DATA_DIR` folders,
so it cannot disturb a real installation. It checks:

- `--version` starts the frozen bundle
- `--self-test` imports Python, Tk, numpy and sherpa-onnx and loads onnxruntime
- the config and model folders are writable
- `--appimage-extract-and-run` works when FUSE is unavailable
- fresh XDG folders are created

## Manual matrix

Test each release on the systems below. Record the AppImage SHA-256 and the
result in the release notes.

| System | Session | Audio stack | Result |
|---|---|---|---|
| Fedora 44 | KDE Wayland | PipeWire | |
| Fedora 44 | KDE X11 | PipeWire | |
| Ubuntu 24.04 | GNOME Wayland | PipeWire | |
| Ubuntu 24.04 | X11 | PulseAudio | |
| Ubuntu 22.04 | X11 / Wayland | PipeWire / PulseAudio | |
| Linux Mint 22.3 | X11 / Wayland | PipeWire / PulseAudio | |
| Debian 12 | X11 / Wayland | PipeWire / PulseAudio | |
| Arch Linux | Wayland | PipeWire | |

For every row, check:

- [ ] `./LiveCaption-*.AppImage --version`
- [ ] `./LiveCaption-*.AppImage --self-test` (Result: OK)
- [ ] `./LiveCaption-*.AppImage --list-devices`
- [ ] first run with no config and no models opens Settings → Speech models
- [ ] download one model with the built-in catalogue
- [ ] restart and confirm captions start without reopening Settings
- [ ] import an existing extracted model folder
- [ ] change a setting, close, restart, confirm it persisted
- [ ] tray icon and menu on Plasma; graceful fallback on GNOME
- [ ] `--appimage-extract-and-run` on a machine without FUSE 2
- [ ] 100 %, 125 % and 150 % display scaling
- [ ] pause/resume and language-pair switching while running

## Useful commands

```bash
# Start with isolated settings (safe for testing)
LIVE_CAPTION_CONFIG_DIR="$(mktemp -d)" \
LIVE_CAPTION_DATA_DIR="$(mktemp -d)" \
./LiveCaption-*.AppImage

# No FUSE
./LiveCaption-*.AppImage --appimage-extract-and-run --self-test

# Inspect the payload
./LiveCaption-*.AppImage --appimage-extract
find squashfs-root/usr/bin/live-caption/_internal -maxdepth 2 -type f | head

# Verify the bundled sherpa libraries
BIN=squashfs-root/usr/bin/live-caption/_internal/sherpa_onnx/lib
ldd "$BIN"/_sherpa_onnx*.so | grep -E 'not found|onnxruntime|sherpa'

# Verify the minimum glibc the payload needs
find squashfs-root/usr/bin/live-caption -type f -exec sh -c '
  for f; do
    file -b "$f" | grep -q ELF || continue
    v=$(objdump -T "$f" 2>/dev/null | grep -oE "GLIBC_[0-9.]+" | sort -V | tail -1)
    [ -n "$v" ] && echo "$v $f"
  done' sh {} + | sort -V | tail
```

The last check is the important one for “any distro”: the highest `GLIBC_*`
symbol version in the payload must be less than or equal to the oldest target
system. Build release artifacts in the container, not on a rolling host.

## Release artifact checklist

- [ ] `__version__` in `src/livecaption/__init__.py` matches the tag
- [ ] tag is `vX.Y.Z`
- [ ] both variants are attached and clearly named:
      `LiveCaption-<version>-ubuntu24.04-x86_64.AppImage` and
      `LiveCaption-<version>-fedora-x86_64.AppImage`
- [ ] `.sha256` is attached for both variants
- [ ] the Ubuntu `.zsync` is attached and `APPIMAGE_UPDATE_INFO` points at it
- [ ] the Ubuntu build was produced on `ubuntu-24.04`
- [ ] the Fedora build was smoke-tested inside the Fedora container
- [ ] release notes say which variant most users should download
- [ ] release notes include the minimum glibc / tested systems
- [ ] no `-dirty` in any published filename
