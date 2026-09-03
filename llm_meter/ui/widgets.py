"""Building blocks for the popup: usage bars, metric rows and provider cards."""

from contextlib import nullcontext
from dataclasses import dataclass, field
from typing import Callable, ContextManager, Optional

from PySide6.QtCore import QRectF, Qt, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPainterPath
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QMenu,
    QPushButton,
    QSizePolicy,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from ..i18n import tr
from ..providers import MenuEntry, Metric, Provider, Section, State
from . import glyphs
from .theme import Palette

MARK_SIZE = 18
BAR_HEIGHT = 6
BAR_PAD = 3  # vertical padding so a bar alone sits as far from its label as a bar sharing a row with a note


@dataclass
class CardHooks:
    """Host callbacks a card needs.

    ``menu_guard`` is held open while a context menu is up so a background
    refresh cannot delete the widgets the menu is anchored to.
    """

    run_entry: Callable[[MenuEntry], None]
    menu_guard: Callable[[], ContextManager] = field(default=lambda: nullcontext())


def _font(widget: QWidget, delta: int = 0, weight: QFont.Weight = QFont.Weight.Normal) -> QFont:
    font = QFont(widget.font())
    base = font.pointSizeF()
    if base > 0:
        font.setPointSizeF(max(8.0, base + delta))
    font.setWeight(weight)
    return font


def _label(
    parent: QWidget,
    text: str,
    color: str,
    delta: int = 0,
    weight: QFont.Weight = QFont.Weight.Normal,
    wrap: bool = False,
) -> QLabel:
    label = QLabel(text, parent)
    label.setFont(_font(parent, delta, weight))
    label.setStyleSheet(f"color: {color}; background: transparent;")
    label.setWordWrap(wrap)
    label.setTextInteractionFlags(Qt.TextInteractionFlag.NoTextInteraction)
    return label


class UsageBar(QWidget):
    """Fuel-gauge style bar: the fill is what remains of the allowance.

    Metrics report usage as a percent, but the card draws what is left, so
    every service reads the same way — full at the start, draining as the
    allowance is spent. The ink still follows the usage level: what remains
    turns amber, then red, as the allowance runs out.
    """

    def __init__(self, palette: Palette, percent: float, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self._palette = palette
        self._used = max(0.0, min(100.0, percent))
        self.setFixedHeight(BAR_HEIGHT + 2 * BAR_PAD)
        self.setContentsMargins(0, BAR_PAD, 0, BAR_PAD)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

    def paintEvent(self, event) -> None:  # noqa: N802 — Qt naming
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.NoPen)
        full = self.contentsRect()
        radius = full.height() / 2
        track = QPainterPath()
        track.addRoundedRect(full, radius, radius)
        painter.fillPath(track, QColor(self._palette.track))
        remaining = 100.0 - self._used
        if remaining <= 0:
            return
        width = max(full.height(), full.width() * remaining / 100.0)
        fill = QPainterPath()
        fill.addRoundedRect(QRectF(full.left(), full.top(), width, full.height()), radius, radius)
        painter.fillPath(fill, QColor(self._palette.usage_color(self._used)))


class LinkLabel(QLabel):
    """Text that behaves like a link: pointer cursor, underline on hover."""

    clicked = Signal()

    def __init__(self, text: str, color: str, parent: Optional[QWidget] = None):
        super().__init__(text, parent)
        self._color = color
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setStyleSheet(f"color: {color}; background: transparent;")

    def enterEvent(self, event) -> None:  # noqa: N802
        font = self.font()
        font.setUnderline(True)
        self.setFont(font)
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:  # noqa: N802
        font = self.font()
        font.setUnderline(False)
        self.setFont(font)
        super().leaveEvent(event)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton and self.rect().contains(event.pos()):
            self.clicked.emit()
        super().mouseReleaseEvent(event)


class MetricView(QWidget):
    """One metric: label and value, plus an optional usage bar and note."""

    def __init__(self, metric: Metric, palette: Palette, parent: Optional[QWidget] = None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(3)

        top = QHBoxLayout()
        top.setContentsMargins(0, 0, 0, 0)
        top.setSpacing(8)
        top.addWidget(_label(self, metric.label, palette.subtle, -1))
        top.addStretch(1)
        if metric.value:
            value_color = palette.faint if metric.muted else palette.text
            top.addWidget(
                _label(self, metric.value, value_color, -1, QFont.Weight.DemiBold)
            )
        layout.addLayout(top)

        if metric.percent is not None:
            bottom = QHBoxLayout()
            bottom.setContentsMargins(0, 0, 0, 0)
            bottom.setSpacing(8)
            bottom.addWidget(UsageBar(palette, metric.percent, self), 1)
            if metric.detail:
                bottom.addWidget(_label(self, metric.detail, palette.faint, -3))
            layout.addLayout(bottom)
        elif metric.detail:
            layout.addWidget(_label(self, metric.detail, palette.faint, -3, wrap=True))


class SectionView(QWidget):
    """A titled metric group. Page links live in the card's ⋯ menu, not here."""

    def __init__(
        self,
        section: Section,
        palette: Palette,
        parent: Optional[QWidget] = None,
    ):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(5)

        header = QHBoxLayout()
        header.setContentsMargins(0, 0, 0, 0)
        header.setSpacing(4)
        if section.title:
            header.addWidget(
                _label(self, section.title, palette.subtle, -2, QFont.Weight.DemiBold)
            )
            header.addStretch(1)
            layout.addLayout(header)

        for metric in section.metrics:
            layout.addWidget(MetricView(metric, palette, self))
        if not section.metrics and section.empty_text:
            layout.addWidget(_label(self, section.empty_text, palette.faint, -2))
        if section.note:
            layout.addWidget(_label(self, section.note, palette.faint, -2, wrap=True))


class ProviderCard(QFrame):
    """One service: mark, name, status and its sections."""

    def __init__(
        self,
        provider: Provider,
        palette: Palette,
        hooks: CardHooks,
        parent: Optional[QWidget] = None,
    ):
        super().__init__(parent)
        self._hooks = hooks
        self.setObjectName("card")
        self.setStyleSheet(
            f"#card {{ background: {palette.card};"
            f" border: 1px solid {palette.border}; border-radius: 10px; }}"
        )
        layout = QVBoxLayout(self)
        layout.setContentsMargins(13, 11, 13, 12)
        layout.setSpacing(10)
        layout.addLayout(self._header(provider, palette))

        if provider.state is State.SIGNED_OUT:
            layout.addWidget(self._sign_in_block(provider, palette))
        else:
            for section in provider.sections:
                layout.addWidget(SectionView(section, palette, self))
            if provider.message:
                color = palette.danger if provider.state is State.ERROR else palette.faint
                layout.addWidget(_label(self, provider.message, color, -2, wrap=True))

    def _header(self, provider: Provider, palette: Palette) -> QHBoxLayout:
        header = QHBoxLayout()
        header.setContentsMargins(0, 0, 0, 0)
        header.setSpacing(8)

        mark = QLabel(self)
        mark.setPixmap(
            glyphs.provider_pixmap(provider.id, MARK_SIZE, QColor(palette.text))
        )
        mark.setFixedSize(MARK_SIZE, MARK_SIZE)
        mark.setStyleSheet("background: transparent;")
        header.addWidget(mark)
        header.addWidget(_label(self, provider.name, palette.text, 0, QFont.Weight.DemiBold))

        badge = provider.snapshot.badge if provider.snapshot else None
        if badge:
            chip = _label(self, badge.upper(), palette.subtle, -4)
            chip.setStyleSheet(
                f"color: {palette.subtle}; border: 1px solid {palette.border};"
                f" border-radius: 6px; padding: 1px 5px; background: transparent;"
            )
            header.addWidget(chip)
        header.addStretch(1)

        if provider.state is State.LOADING:
            header.addWidget(_label(self, tr("불러오는 중...", "Loading..."), palette.faint, -3))

        menu_button = QToolButton(self)
        menu_button.setText("⋯")
        menu_button.setCursor(Qt.CursorShape.PointingHandCursor)
        menu_button.setFont(_font(self, 1, QFont.Weight.DemiBold))
        menu_button.setToolTip(tr("이 서비스 설정", "Service actions"))
        menu_button.setStyleSheet(
            f"QToolButton {{ color: {palette.subtle}; border: none; background: transparent;"
            f" padding: 0 4px; }}"
            f"QToolButton:hover {{ color: {palette.text}; }}"
        )
        menu_button.clicked.connect(lambda: self._show_menu(provider, palette, menu_button))
        header.addWidget(menu_button)
        return header

    def _sign_in_block(self, provider: Provider, palette: Palette) -> QWidget:
        block = QWidget(self)
        layout = QVBoxLayout(block)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(9)
        message = provider.message or provider.signed_out_hint()
        layout.addWidget(_label(block, message, palette.subtle, -1, wrap=True))
        entry = provider.primary_action()
        if entry:
            button = QPushButton(entry.label, block)
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.setEnabled(entry.enabled)
            button.setFont(_font(block, -1, QFont.Weight.DemiBold))
            button.setStyleSheet(
                f"QPushButton {{ color: {palette.text}; background: {palette.hover};"
                f" border: 1px solid {palette.border}; border-radius: 7px; padding: 6px 12px; }}"
                f"QPushButton:hover {{ border-color: {provider.accent}; }}"
                f"QPushButton:disabled {{ color: {palette.faint}; }}"
            )
            button.clicked.connect(lambda: self._hooks.run_entry(entry))
            row = QHBoxLayout()
            row.setContentsMargins(0, 0, 0, 0)
            row.addWidget(button)
            row.addStretch(1)
            layout.addLayout(row)
        return block

    def _show_menu(self, provider: Provider, palette: Palette, anchor: QWidget) -> None:
        menu = QMenu(self)
        style_menu(menu, palette)
        entries = [
            MenuEntry(
                label=tr("지금 새로고침", "Refresh now"),
                run=lambda: provider.ui.request_refresh(provider),
                background=False,
                enabled=provider.is_authenticated(),
            ),
            MenuEntry.sep(),
            *provider.menu(),
        ]
        for entry in entries:
            if entry.separator:
                menu.addSeparator()
                continue
            action = menu.addAction(entry.label)
            action.setEnabled(entry.enabled)
            if entry.checked is not None:
                action.setCheckable(True)
                action.setChecked(entry.checked)
            action.triggered.connect(
                lambda _checked=False, item=entry: self._hooks.run_entry(item)
            )
        with self._hooks.menu_guard():
            menu.exec(anchor.mapToGlobal(anchor.rect().bottomLeft()))


def style_menu(menu: QMenu, palette: Palette) -> None:
    menu.setStyleSheet(
        f"QMenu {{ background: {palette.card}; color: {palette.text};"
        f" border: 1px solid {palette.border}; border-radius: 8px; padding: 5px; }}"
        f"QMenu::item {{ padding: 5px 22px 5px 12px; border-radius: 5px; }}"
        f"QMenu::item:selected {{ background: {palette.hover}; }}"
        f"QMenu::item:disabled {{ color: {palette.faint}; }}"
        f"QMenu::separator {{ height: 1px; background: {palette.border}; margin: 5px 8px; }}"
    )
