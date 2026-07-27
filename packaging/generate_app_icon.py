#!/usr/bin/env python3
"""Render the LLM Usage Meter app icon and write platform icon packs.

The mark reuses the tray gauge glyph (`glyphs.gauge_pixmap`) with the needle at
2 o'clock, composited on a black high-gloss rounded square.

Usage (from the repo root, inside the app venv):

    .venv/bin/python packaging/generate_app_icon.py
"""

from __future__ import annotations

import shutil
import struct
import subprocess
import sys
from pathlib import Path

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import (
    QColor,
    QImage,
    QLinearGradient,
    QPainter,
    QPainterPath,
    QRadialGradient,
)
from PySide6.QtWidgets import QApplication

ROOT = Path(__file__).resolve().parent.parent
ASSETS = ROOT / "assets"
sys.path.insert(0, str(ROOT))

from llm_meter.ui import glyphs  # noqa: E402

# Tray dial: start 225°, sweep −270°. 2 o'clock is 30° (ccw from 3 o'clock).
_TWO_OCLOCK_PERCENT = (225.0 - 30.0) / 270.0 * 100.0  # ≈ 72.22

# macOS continuous-corner feel for a full-bleed app tile.
_CORNER_RATIO = 0.2237
_MASTER = 1024
_PNG_SIZES = (16, 32, 64, 128, 256, 512, 1024)
_ICNS_SIZES = (
    (16, "icon_16x16.png"),
    (32, "diana.l@example.org"),
    (32, "icon_32x32.png"),
    (64, "ivan.p@example.net"),
    (128, "icon_128x128.png"),
    (256, "wendy.h@example.net"),
    (256, "icon_256x256.png"),
    (512, "wendy.h@example.net"),
    (512, "icon_512x512.png"),
    (1024, "walt.e@example.net"),
)
_ICO_SIZES = (16, 32, 48, 64, 128, 256)


def render_app_icon(size: int) -> QImage:
    """Black high-gloss tile with the tray gauge centred, needle at 2 o'clock."""
    image = QImage(size, size, QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(Qt.GlobalColor.transparent)
    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)

    # Leave a 1px optical margin so downscales keep a clean alpha edge.
    inset = max(1.0, size * 0.02)
    tile = size - 2 * inset
    radius = tile * _CORNER_RATIO
    tile_rect = QRectF(inset, inset, tile, tile)
    shape = QPainterPath()
    shape.addRoundedRect(tile_rect, radius, radius)

    painter.setClipPath(shape)

    base = QLinearGradient(tile_rect.topLeft(), tile_rect.bottomLeft())
    base.setColorAt(0.0, QColor("#2a2a2c"))
    base.setColorAt(0.35, QColor("#141416"))
    base.setColorAt(1.0, QColor("#050506"))
    painter.fillPath(shape, base)

    # Soft specular sheen across the upper face.
    sheen = QLinearGradient(tile_rect.topLeft(), QPointF(tile_rect.center().x(), tile_rect.bottom()))
    sheen.setColorAt(0.0, QColor(255, 255, 255, 58))
    sheen.setColorAt(0.28, QColor(255, 255, 255, 18))
    sheen.setColorAt(0.55, QColor(255, 255, 255, 0))
    sheen.setColorAt(1.0, QColor(0, 0, 0, 0))
    painter.fillPath(shape, sheen)

    # Focused highlight near the top edge (high-gloss).
    gloss = QRadialGradient(
        QPointF(tile_rect.center().x(), tile_rect.top() + tile * 0.18),
        tile * 0.55,
    )
    gloss.setColorAt(0.0, QColor(255, 255, 255, 72))
    gloss.setColorAt(0.35, QColor(255, 255, 255, 22))
    gloss.setColorAt(1.0, QColor(255, 255, 255, 0))
    painter.fillPath(shape, gloss)

    # Deepen the lower half so the tile reads as lacquered metal/glass.
    shade = QLinearGradient(QPointF(tile_rect.center().x(), tile_rect.center().y()), tile_rect.bottomLeft())
    shade.setColorAt(0.0, QColor(0, 0, 0, 0))
    shade.setColorAt(1.0, QColor(0, 0, 0, 90))
    painter.fillPath(shape, shade)

    painter.setClipping(False)

    # Thin rim catch-light.
    painter.setPen(Qt.PenStyle.NoPen)
    rim = QPainterPath(shape)
    inner = QPainterPath()
    rim_inset = max(1.5, size * 0.012)
    inner.addRoundedRect(tile_rect.adjusted(rim_inset, rim_inset, -rim_inset, -rim_inset), radius * 0.92, radius * 0.92)
    rim = rim.subtracted(inner)
    rim_grad = QLinearGradient(tile_rect.topLeft(), tile_rect.bottomRight())
    rim_grad.setColorAt(0.0, QColor(255, 255, 255, 70))
    rim_grad.setColorAt(0.45, QColor(255, 255, 255, 18))
    rim_grad.setColorAt(1.0, QColor(255, 255, 255, 8))
    painter.fillPath(rim, rim_grad)

    # Tray gauge — same geometry, white ink on the dark tile.
    gauge_size = max(1, round(tile * 0.70))
    gauge = glyphs.gauge_pixmap(gauge_size, _TWO_OCLOCK_PERCENT, QColor("#f4f4f5"))
    x = round((size - gauge_size) / 2)
    y = round((size - gauge_size) / 2)
    painter.drawPixmap(x, y, gauge)
    painter.end()
    return image


def _png_bytes(image: QImage) -> bytes:
    from PySide6.QtCore import QBuffer, QIODevice

    buffer = QBuffer()
    buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    image.save(buffer, "PNG")
    return bytes(buffer.data())


def _write_ico(path: Path, images: dict[int, QImage]) -> None:
    """Write a multi-resolution ICO from ARGB images (PNG-compressed entries)."""
    entries: list[tuple[int, bytes]] = []
    for size in _ICO_SIZES:
        png = _png_bytes(images[size])
        entries.append((size, png))

    count = len(entries)
    offset = 6 + 16 * count
    header = struct.pack("<HHH", 0, 1, count)
    directory = bytearray()
    blobs = bytearray()
    for size, png in entries:
        w = 0 if size >= 256 else size
        h = 0 if size >= 256 else size
        directory += struct.pack("<BBBBHHII", w, h, 0, 0, 1, 32, len(png), offset)
        blobs += png
        offset += len(png)
    path.write_bytes(header + directory + blobs)


def _write_icns(path: Path, master: QImage) -> None:
    iconset = path.with_suffix(".iconset")
    if iconset.exists():
        shutil.rmtree(iconset)
    iconset.mkdir()
    for size, name in _ICNS_SIZES:
        scaled = master.scaled(
            size,
            size,
            Qt.AspectRatioMode.IgnoreAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        scaled.save(str(iconset / name), "PNG")
    subprocess.check_call(["iconutil", "-c", "icns", "-o", str(path), str(iconset)])
    shutil.rmtree(iconset)


def main() -> int:
    app = QApplication.instance() or QApplication(sys.argv)
    ASSETS.mkdir(parents=True, exist_ok=True)

    master = render_app_icon(_MASTER)
    png_path = ASSETS / "app-icon.png"
    master.save(str(png_path), "PNG")
    print(f"Wrote {png_path}")

    sized = {
        size: master.scaled(
            size,
            size,
            Qt.AspectRatioMode.IgnoreAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        for size in set(_PNG_SIZES) | set(_ICO_SIZES)
    }

    ico_path = ASSETS / "app-icon.ico"
    _write_ico(ico_path, sized)
    print(f"Wrote {ico_path}")

    if sys.platform == "darwin":
        icns_path = ASSETS / "app-icon.icns"
        _write_icns(icns_path, master)
        print(f"Wrote {icns_path}")
    else:
        print("Skipping .icns (iconutil is macOS-only)")

    # Keep the process from hanging on a Qt event loop if one was created.
    app.quit()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
