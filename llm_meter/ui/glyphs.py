"""Monochrome provider marks and the tray gauge, drawn at request time.

Everything here is a single-ink silhouette plus an alpha channel: the popup
tints marks with the current text colour, and on macOS the tray icon is handed
to the system as a template image so the menu bar recolours it itself.
"""

import functools
import logging
import math

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QImage, QImageReader, QPainter, QPen, QPixmap
from PySide6.QtSvg import QSvgRenderer

from .. import paths

logger = logging.getLogger(__name__)

_CODEX_ICON = "codex-blossom.ico"
_CURSOR_ICON = "cursor-cube.svg"
_OLLAMA_ICON = "ollama-icon.svg"
_OPENROUTER_ICON = "openrouter-icon.svg"

# OpenCode logomark geometry in the official 512x512 viewBox: a frame with an
# interior hole whose lower two thirds hold a dimmer inset panel.
_OC_VIEWBOX = 512.0
_OC_OUTER = (128, 96, 384, 416)
_OC_HOLE = (192, 160, 320, 352)
_OC_INSET = (192, 224, 320, 352)
_OC_INSET_ALPHA = 0x5A / 0xFF  # brand gray-to-white ratio


def _rect(box: tuple[int, int, int, int], scale: float) -> QRectF:
    left, top, right, bottom = (value * scale for value in box)
    return QRectF(left, top, right - left, bottom - top)


def _tint(mask: QImage, color: QColor) -> QPixmap:
    """Recolour an alpha-carrying image, keeping its alpha channel."""
    pixmap = QPixmap(mask.size())
    pixmap.fill(Qt.transparent)
    painter = QPainter(pixmap)
    painter.drawImage(0, 0, mask)
    painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceIn)
    painter.fillRect(pixmap.rect(), color)
    painter.end()
    return pixmap


def _centered(pixmap: QPixmap, size: int) -> QPixmap:
    if pixmap.width() == size and pixmap.height() == size:
        return pixmap
    canvas = QPixmap(size, size)
    canvas.fill(Qt.transparent)
    painter = QPainter(canvas)
    painter.drawPixmap(
        (size - pixmap.width()) // 2, (size - pixmap.height()) // 2, pixmap
    )
    painter.end()
    return canvas


@functools.lru_cache(maxsize=1)
def _blossom_mask() -> QImage | None:
    """Alpha mask of the ChatGPT blossom strokes, cropped to its bounding box.

    The favicon is a white disc carrying a black blossom, so inverted luminance
    multiplied by the source alpha isolates the strokes and drops the disc.
    """
    path = paths.asset(_CODEX_ICON)
    if not path:
        logger.warning("Bundled asset %s is missing", _CODEX_ICON)
        return None
    reader = QImageReader(path)
    source = QImage()
    for index in range(max(1, reader.imageCount())):
        reader.jumpToImage(index)
        frame = reader.read()
        if frame.isNull():
            continue
        if frame.width() > source.width():
            source = frame
    if source.isNull():
        logger.warning("Could not decode %s", path)
        return None

    source = source.convertToFormat(QImage.Format.Format_ARGB32)
    width, height = source.width(), source.height()
    mask = QImage(width, height, QImage.Format.Format_ARGB32)
    mask.fill(Qt.transparent)
    left, top, right, bottom = width, height, -1, -1
    for y in range(height):
        for x in range(width):
            pixel = source.pixelColor(x, y)
            luminance = (
                pixel.red() * 299 + pixel.green() * 587 + pixel.blue() * 114
            ) // 1000
            alpha = pixel.alpha() * (255 - luminance) // 255
            if alpha <= 8:
                continue
            mask.setPixelColor(x, y, QColor(0, 0, 0, alpha))
            left, top = min(left, x), min(top, y)
            right, bottom = max(right, x), max(bottom, y)
    if right < 0:
        return None
    return mask.copy(left, top, right - left + 1, bottom - top + 1)


def _codex_pixmap(size: int, color: QColor) -> QPixmap:
    mask = _blossom_mask()
    if mask is None:
        return _fallback_pixmap(size, color)
    scaled = mask.scaled(
        size, size, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation
    )
    return _centered(_tint(scaled, color), size)


def _opencode_pixmap(size: int, color: QColor) -> QPixmap:
    scale = size / _OC_VIEWBOX
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setPen(Qt.NoPen)

    frame = QColor(color)
    painter.setBrush(frame)
    painter.drawRect(_rect(_OC_OUTER, scale))
    painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_Clear)
    painter.drawRect(_rect(_OC_HOLE, scale))
    painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceOver)
    inset = QColor(color)
    inset.setAlphaF(color.alphaF() * _OC_INSET_ALPHA)
    painter.setBrush(inset)
    painter.drawRect(_rect(_OC_INSET, scale))
    painter.end()

    # The logomark is not square; trim the transparent margin so cards can align
    # every provider mark on the same optical box.
    trimmed = pixmap.toImage()
    box = _OC_OUTER
    crop = QRectF(
        box[0] * scale, box[1] * scale, (box[2] - box[0]) * scale, (box[3] - box[1]) * scale
    ).toRect()
    cropped = QPixmap.fromImage(trimmed.copy(crop))
    scaled = cropped.scaled(
        size,
        size,
        Qt.AspectRatioMode.KeepAspectRatio,
        Qt.TransformationMode.SmoothTransformation,
    )
    return _centered(scaled, size)


@functools.lru_cache(maxsize=1)
def _cursor_mask() -> QImage | None:
    """Alpha mask of the official Cursor 2D cube mark."""
    path = paths.asset(_CURSOR_ICON)
    if not path:
        logger.warning("Bundled asset %s is missing", _CURSOR_ICON)
        return None
    renderer = QSvgRenderer(path)
    if not renderer.isValid():
        logger.warning("Could not parse %s", path)
        return None
    view = renderer.viewBoxF()
    if view.isEmpty():
        view = QRectF(0, 0, 466.73, 532.09)
    # Render large enough that downscales stay sharp in the popup and tray.
    height = 512
    width = max(1, round(height * view.width() / view.height()))
    image = QImage(width, height, QImage.Format.Format_ARGB32)
    image.fill(Qt.transparent)
    painter = QPainter(image)
    renderer.render(painter, QRectF(0, 0, width, height))
    painter.end()

    mask = QImage(width, height, QImage.Format.Format_ARGB32)
    mask.fill(Qt.transparent)
    left, top, right, bottom = width, height, -1, -1
    for y in range(height):
        for x in range(width):
            pixel = image.pixelColor(x, y)
            if pixel.alpha() <= 8:
                continue
            # Dark official fill (#26251e): keep alpha so tinting stays crisp.
            mask.setPixelColor(x, y, QColor(0, 0, 0, pixel.alpha()))
            left, top = min(left, x), min(top, y)
            right, bottom = max(right, x), max(bottom, y)
    if right < 0:
        return None
    return mask.copy(left, top, right - left + 1, bottom - top + 1)


def _cursor_pixmap(size: int, color: QColor) -> QPixmap:
    mask = _cursor_mask()
    if mask is None:
        return _fallback_pixmap(size, color)
    scaled = mask.scaled(
        size, size, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation
    )
    return _centered(_tint(scaled, color), size)


@functools.lru_cache(maxsize=1)
def _ollama_mask() -> QImage | None:
    """Alpha mask of the official Ollama llama mark (SVG)."""
    path = paths.asset(_OLLAMA_ICON)
    if not path:
        logger.warning("Bundled asset %s is missing", _OLLAMA_ICON)
        return None
    renderer = QSvgRenderer(path)
    if not renderer.isValid():
        logger.warning("Could not parse %s", path)
        return None
    view = renderer.viewBoxF()
    if view.isEmpty():
        view = QRectF(0, 0, 512, 512)
    # Render large enough that downscales stay sharp in the popup and tray.
    height = 512
    width = max(1, round(height * view.width() / view.height()))
    image = QImage(width, height, QImage.Format.Format_ARGB32)
    image.fill(Qt.transparent)
    painter = QPainter(image)
    renderer.render(painter, QRectF(0, 0, width, height))
    painter.end()

    mask = QImage(width, height, QImage.Format.Format_ARGB32)
    mask.fill(Qt.transparent)
    left, top, right, bottom = width, height, -1, -1
    for y in range(height):
        for x in range(width):
            pixel = image.pixelColor(x, y)
            if pixel.alpha() <= 8:
                continue
            # The mark is a single black silhouette; keep alpha so tinting stays crisp.
            mask.setPixelColor(x, y, QColor(0, 0, 0, pixel.alpha()))
            left, top = min(left, x), min(top, y)
            right, bottom = max(right, x), max(bottom, y)
    if right < 0:
        return None
    return mask.copy(left, top, right - left + 1, bottom - top + 1)


def _ollama_pixmap(size: int, color: QColor) -> QPixmap:
    mask = _ollama_mask()
    if mask is None:
        return _fallback_pixmap(size, color)
    scaled = mask.scaled(
        size, size, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation
    )
    return _centered(_tint(scaled, color), size)


@functools.lru_cache(maxsize=1)
def _openrouter_mask() -> QImage | None:
    """Alpha mask of the official OpenRouter grape glyph (SVG)."""
    path = paths.asset(_OPENROUTER_ICON)
    if not path:
        logger.warning("Bundled asset %s is missing", _OPENROUTER_ICON)
        return None
    renderer = QSvgRenderer(path)
    if not renderer.isValid():
        logger.warning("Could not parse %s", path)
        return None
    view = renderer.viewBoxF()
    if view.isEmpty():
        view = QRectF(0, 0, 1024, 730)
    # Render large enough that downscales stay sharp in the popup and tray.
    height = 512
    width = max(1, round(height * view.width() / view.height()))
    image = QImage(width, height, QImage.Format.Format_ARGB32)
    image.fill(Qt.transparent)
    painter = QPainter(image)
    renderer.render(painter, QRectF(0, 0, width, height))
    painter.end()

    mask = QImage(width, height, QImage.Format.Format_ARGB32)
    mask.fill(Qt.transparent)
    left, top, right, bottom = width, height, -1, -1
    for y in range(height):
        for x in range(width):
            pixel = image.pixelColor(x, y)
            if pixel.alpha() <= 8:
                continue
            # The mark is a single violet silhouette; keep alpha so tinting stays crisp.
            mask.setPixelColor(x, y, QColor(0, 0, 0, pixel.alpha()))
            left, top = min(left, x), min(top, y)
            right, bottom = max(right, x), max(bottom, y)
    if right < 0:
        return None
    return mask.copy(left, top, right - left + 1, bottom - top + 1)


def _openrouter_pixmap(size: int, color: QColor) -> QPixmap:
    mask = _openrouter_mask()
    if mask is None:
        return _fallback_pixmap(size, color)
    scaled = mask.scaled(
        size, size, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation
    )
    return _centered(_tint(scaled, color), size)


def _fallback_pixmap(size: int, color: QColor) -> QPixmap:
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setPen(QPen(color, max(1.0, size * 0.12)))
    inset = size * 0.16
    painter.drawEllipse(QRectF(inset, inset, size - 2 * inset, size - 2 * inset))
    painter.end()
    return pixmap


_BUILDERS = {
    "codex": _codex_pixmap,
    "opencode": _opencode_pixmap,
    "cursor": _cursor_pixmap,
    "ollama": _ollama_pixmap,
    "openrouter": _openrouter_pixmap,
}


@functools.lru_cache(maxsize=64)
def _cached(provider_id: str, size: int, rgba: int) -> QPixmap:
    builder = _BUILDERS.get(provider_id, _fallback_pixmap)
    return builder(size, QColor.fromRgba(rgba))


def provider_pixmap(provider_id: str, size: int, color: QColor) -> QPixmap:
    return _cached(provider_id, int(size), color.rgba())


def gauge_pixmap(size: int, percent: float | None, color: QColor) -> QPixmap:
    """A 270-degree fuel-gauge dial: the needle points at what remains.

    ``percent`` is the used share, so a full tank rests at the sweep's start
    and drains clockwise as the allowance is spent — matching the cards. With
    no reading yet the needle rests at full.
    """
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)

    # Tight inset so the glyph fills most of the menu-bar slot.
    stroke = max(1.25, size * 0.12)
    margin = stroke / 2 + max(0.5, size * 0.02)
    box = QRectF(margin, margin, size - 2 * margin, size - 2 * margin)
    start_angle, sweep = 225.0, -270.0

    painter.setPen(QPen(color, stroke, Qt.SolidLine, Qt.PenCapStyle.RoundCap))
    painter.drawArc(box, round(start_angle * 16), round(sweep * 16))

    used = max(0.0, min(100.0, percent)) if percent is not None else 0.0
    ratio = 1.0 - used / 100.0
    center = box.center()
    angle = math.radians(start_angle + sweep * ratio)
    radius = box.width() / 2 * 0.78
    painter.drawLine(
        center,
        QPointF(center.x() + radius * math.cos(angle), center.y() - radius * math.sin(angle)),
    )
    # A hub anchors the needle, so the glyph reads as a dial and not as a dash.
    hub = stroke * 0.85
    painter.setPen(Qt.NoPen)
    painter.setBrush(color)
    painter.drawEllipse(center, hub, hub)
    painter.end()
    return pixmap
