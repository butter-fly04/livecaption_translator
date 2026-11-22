#!/usr/bin/env python3
"""Draw the Live Caption app icon into an AppDir (Pillow is already a runtime dep).

    python3 packaging/make-icon.py build/AppDir
"""

import os
import sys

from PIL import Image, ImageDraw, ImageFont

SIZES = (16, 22, 24, 32, 48, 64, 128, 256)
FONT_CANDIDATES = (
    "/usr/share/fonts/dejavu-sans-fonts/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/liberation-sans-fonts/LiberationSans-Bold.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
)


def _font(size):
    for path in FONT_CANDIDATES:
        if os.path.exists(path):
            return ImageFont.truetype(path, size)
    try:
        return ImageFont.load_default(size=size)
    except TypeError:                       # Pillow < 10
        return ImageFont.load_default()


def render(size):
    scale = 4
    s = size * scale
    img = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)

    margin = int(s * 0.07)
    box = (margin, margin, s - margin, int(s * 0.72))
    radius = int(s * 0.16)
    width = max(2, int(s * 0.035))
    d.rounded_rectangle(box, radius=radius, fill=(47, 52, 57, 255),
                        outline=(255, 255, 255, 255), width=width)

    # Speech-bubble tail.
    tail = [(int(s * 0.26), int(s * 0.70)),
            (int(s * 0.44), int(s * 0.70)),
            (int(s * 0.25), int(s * 0.88))]
    d.polygon(tail, fill=(47, 52, 57, 255))
    d.line([tail[0], tail[2], tail[1]], fill=(255, 255, 255, 255), width=width,
           joint="curve")

    d.text((s // 2, int(s * 0.40)), "CC", font=_font(int(s * 0.30)),
           fill=(255, 255, 255, 255), anchor="mm")
    return img.resize((size, size), Image.Resampling.LANCZOS)


def main(appdir):
    icons = os.path.join(appdir, "usr", "share", "icons", "hicolor")
    for size in SIZES:
        folder = os.path.join(icons, f"{size}x{size}", "apps")
        os.makedirs(folder, exist_ok=True)
        render(size).save(os.path.join(folder, "live-caption.png"))
    os.makedirs(os.path.join(icons, "scalable", "apps"), exist_ok=True)
    src = os.path.join(os.path.dirname(os.path.abspath(__file__)), "live-caption.svg")
    if os.path.exists(src):
        with open(src, "rb") as f:
            svg = f.read()
        with open(os.path.join(icons, "scalable", "apps", "live-caption.svg"), "wb") as f:
            f.write(svg)
    render(256).save(os.path.join(appdir, "live-caption.png"))
    # .DirIcon is the icon AppImageLauncher / file managers show.  It has no
    # filename extension, so the format must be explicit.
    render(256).save(os.path.join(appdir, ".DirIcon"), format="PNG")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "AppDir")
