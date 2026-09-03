#!/usr/bin/env python3
"""Render AppIcon.icns for the Mariposa Studio .app bundle.

Draws the "Club Paper" brand mark: a bottle-green squircle (the brand's
"Court" green) with the Mariposa butterfly in warm paper-white. Colors come
straight from design.py so the icon can never drift from the brand tokens.

Drawn with QPainter (no extra deps once PySide6 is installed). Outputs an
iconset directory and converts it to .icns with macOS's `iconutil`.
"""
import os
import shutil
import subprocess
import sys
from functools import lru_cache
from pathlib import Path

from PySide6.QtCore import Qt, QRectF
from PySide6.QtGui import (QGuiApplication, QImage, QPainter, QColor,
                            QLinearGradient, QPainterPath)
from PySide6.QtSvg import QSvgRenderer

import design as d

APP_DIR = Path(__file__).resolve().parent.parent
BRAND_DIR = APP_DIR / "brand"
RESOURCES = APP_DIR / "Mariposa Studio.app" / "Contents" / "Resources"

# The squircle is miavola's wine, not the retired Court green — the Dock icon
# is the first thing you see of the app and it should belong to the same house.
BG_TOP    = QColor(d.WINE_HI)       # subtle top sheen
BG_BOTTOM = QColor(d.WINE_PRESSED)
MARK      = QColor(d.CANVAS)        # warm cream butterfly

# The icon IS the logomark — we rasterise brand/logomark.svg so the two can
# never drift. On the green squircle the butterfly is paper-white (its
# currentColor recoloured to MARK; green-on-green would vanish).
_LOGOMARK = BRAND_DIR / "logomark.svg"

# Fraction of the (already-inset) box the butterfly spans horizontally.
_GLYPH = 0.66


@lru_cache(maxsize=1)
def _mark_svg() -> bytes:
    svg = _LOGOMARK.read_text(encoding="utf-8").replace("currentColor", MARK.name())
    return bytearray(svg, encoding="utf-8")


def _draw_mark(p: QPainter, box: float) -> None:
    """Render the butterfly, centred, into a box-sized region at the origin."""
    r = QSvgRenderer(_mark_svg())
    sz = r.defaultSize()
    ratio = (sz.height() / sz.width()) if sz.width() else 1.0
    gw = box * _GLYPH
    gh = gw * ratio
    r.render(p, QRectF((box - gw) / 2, (box - gh) / 2, gw, gh))


# macOS app icons don't fill their canvas: the rounded square sits inside a
# transparent margin (~10% each side, the Big Sur "824 in 1024" grid). Without
# it our squircle reads noticeably bigger than every other Dock icon.
_MARGIN = 0.10


def draw_icon(size: int, margin: float = _MARGIN) -> QImage:
    img = QImage(size, size, QImage.Format_ARGB32_Premultiplied)
    img.fill(Qt.transparent)
    p = QPainter(img)
    p.setRenderHint(QPainter.Antialiasing)

    # The artwork lives in an inset box, leaving a transparent margin so the app
    # matches the size of neighbouring Dock / Launchpad icons.
    inset = round(size * margin)
    box = size - 2 * inset
    radius = box * 0.225
    path = QPainterPath()
    path.addRoundedRect(QRectF(inset, inset, box, box), radius, radius)
    p.setClipPath(path)

    # Bottle-green squircle with a faint vertical sheen — flat, no glow.
    bg = QLinearGradient(0, inset, 0, inset + box)
    bg.setColorAt(0, BG_TOP)
    bg.setColorAt(1, BG_BOTTOM)
    p.fillRect(QRectF(inset, inset, box, box), bg)

    # The paper-white butterfly, centred in the inset box.
    p.translate(inset, inset)
    _draw_mark(p, box)

    p.end()
    return img


# Windows icon margin — tighter than macOS (Windows taskbar/Explorer icons sit
# fuller in their box than Dock icons), and small sizes need the glyph bold.
_WIN_MARGIN = 0.04

# Sizes Windows actually pulls from the .ico (taskbar 16–32, large views up to
# 256). A single-256 .ico renders BLANK at taskbar sizes — hence the full set.
_WIN_ICO_SIZES = [16, 24, 32, 48, 64, 128, 256]


def _png_bytes(img: QImage) -> bytes:
    """Encode a QImage as PNG bytes (for embedding as an ICO frame)."""
    from PySide6.QtCore import QBuffer, QByteArray
    ba = QByteArray()
    buf = QBuffer(ba)
    buf.open(QBuffer.WriteOnly)
    img.save(buf, "PNG")
    buf.close()
    return bytes(ba)


def write_multi_ico(path, sizes, margin: float = _WIN_MARGIN) -> None:
    """Write a multi-resolution .ico (PNG-compressed frames, Win Vista+).

    Qt's ICO writer only emits one frame; Windows then can't downscale a lone
    256px icon to taskbar sizes and shows a blank page. Packing 16–256px frames
    fixes the blank taskbar / pinned icon."""
    import struct
    frames = [(s, _png_bytes(draw_icon(s, margin))) for s in sorted(sizes)]
    header = struct.pack("<HHH", 0, 1, len(frames))  # reserved, type=icon, count
    offset = 6 + 16 * len(frames)
    entries, blobs = b"", b""
    for s, png in frames:
        dim = 0 if s >= 256 else s  # 0 means 256 in the ICO directory
        entries += struct.pack("<BBBBHHII", dim, dim, 0, 0, 1, 32, len(png), offset)
        offset += len(png)
        blobs += png
    with open(path, "wb") as f:
        f.write(header + entries + blobs)


def main():
    app = QGuiApplication.instance() or QGuiApplication(sys.argv)

    # Windows icon — a multi-size .ico committed to brand/ so the installer and
    # the app's shortcut can point the Start Menu / taskbar icon at it without
    # rendering anything at install time.
    BRAND_DIR.mkdir(parents=True, exist_ok=True)
    ico_out = BRAND_DIR / "AppIcon.ico"
    write_multi_ico(ico_out, _WIN_ICO_SIZES)
    print(f"✓ Wrote {ico_out}  ({len(_WIN_ICO_SIZES)} sizes)")

    # macOS .icns via iconutil (mac-only; nothing else uses it).
    if sys.platform != "darwin":
        return

    iconset = APP_DIR / "AppIcon.iconset"
    if iconset.exists():
        shutil.rmtree(iconset)
    iconset.mkdir(parents=True)

    sizes = [
        (16, "16x16"), (32, "16x16@2x"), (32, "32x32"), (64, "32x32@2x"),
        (128, "128x128"), (256, "128x128@2x"), (256, "256x256"),
        (512, "256x256@2x"), (512, "512x512"), (1024, "512x512@2x"),
    ]
    for px, label in sizes:
        draw_icon(px).save(str(iconset / f"icon_{label}.png"), "PNG")

    RESOURCES.mkdir(parents=True, exist_ok=True)
    out = RESOURCES / "AppIcon.icns"
    subprocess.run(["iconutil", "-c", "icns", str(iconset), "-o", str(out)], check=True)
    shutil.rmtree(iconset)
    app_path = APP_DIR / "Mariposa Studio.app"
    try:
        os.utime(app_path, None)
    except Exception:
        pass
    print(f"✓ Wrote {out}")


if __name__ == "__main__":
    main()
