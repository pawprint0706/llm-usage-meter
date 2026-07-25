"""The panel that opens next to the tray icon."""

import logging
from contextlib import contextmanager
from typing import Optional

from PySide6.QtCore import QPoint, QRect, QSize, Qt
from PySide6.QtGui import (
    QColor,
    QCursor,
    QFont,
    QFontMetrics,
    QGuiApplication,
    QIcon,
    QPainter,
    QPixmap,
)
from PySide6.QtWidgets import (
    QApplication,
    QFrame,
    QGraphicsDropShadowEffect,
    QHBoxLayout,
    QLabel,
    QMenu,
    QScrollArea,
    QSizePolicy,
    QTabBar,
    QTabWidget,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from .. import format as fmt
from ..i18n import tr
from ..providers import State
from . import glyphs, theme
from .widgets import CardHooks, ProviderCard, _font, _label

logger = logging.getLogger(__name__)

PANEL_WIDTH = 348
SHADOW_MARGIN = 10
GAP_FROM_TRAY = 2
MAX_HEIGHT_FRACTION = 0.82
TAB_ICON_SIZE = 14
TAB_TO_CARD_GAP = 12
TAB_ICON_TEXT_GAP = 6
# No pad past the composed label so the underline ends on the last glyph.
TAB_LABEL_RIGHT_PAD = 0
# Must match QTabBar::tab margin-right; Qt takes margin out of the painted
# tab (and its underline), so sizeHint has to include it or the bar clips
# the title.
TAB_BAR_MARGIN_RIGHT = 10
HEADER_ACTION_SIZE = 16
HEADER_ACTION_GAP = 12
PANEL_MARGIN = 12


class _ProviderTabBar(QTabBar):
    """Size each tab to its composed label so the underline doesn't overshoot."""

    def tabSizeHint(self, index: int) -> QSize:
        base = super().tabSizeHint(index)
        button = self.tabButton(index, QTabBar.ButtonPosition.LeftSide)
        if button is None:
            return base
        return QSize(
            button.sizeHint().width() + TAB_LABEL_RIGHT_PAD + TAB_BAR_MARGIN_RIGHT,
            base.height(),
        )


class _ActionButton(QToolButton):
    """Fixed-size header action whose glyph fills the box so layout spacing is visual."""

    def __init__(self, glyph: str, tip: str, palette: theme.Palette, parent: QWidget):
        super().__init__(parent)
        self._glyph = glyph
        self._palette = palette
        self.setToolTip(tip)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setAutoRaise(True)
        self.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonIconOnly)
        self.setFixedSize(HEADER_ACTION_SIZE, HEADER_ACTION_SIZE)
        self.setIconSize(QSize(HEADER_ACTION_SIZE, HEADER_ACTION_SIZE))
        self.setStyleSheet(
            "QToolButton { border: none; background: transparent; padding: 0; margin: 0; }"
        )
        self._paint(hover=False)

    def enterEvent(self, event) -> None:  # noqa: N802
        self._paint(hover=True)
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:  # noqa: N802
        self._paint(hover=False)
        super().leaveEvent(event)

    def _paint(self, *, hover: bool) -> None:
        color = QColor(self._palette.text if hover else self._palette.subtle)
        self.setIcon(_action_icon(self._glyph, color, HEADER_ACTION_SIZE))


def _action_icon(glyph: str, color: QColor, size: int) -> QIcon:
    """Render ``glyph`` ink-cropped so it fills ``size``×``size`` with no side bearings."""
    font = QFont()
    font.setPixelSize(size * 2)
    probe = QPixmap(size * 4, size * 4)
    probe.fill(Qt.GlobalColor.transparent)
    painter = QPainter(probe)
    painter.setRenderHint(QPainter.RenderHint.TextAntialiasing)
    painter.setFont(font)
    painter.setPen(color)
    painter.drawText(probe.rect(), int(Qt.AlignmentFlag.AlignCenter), glyph)
    painter.end()

    image = probe.toImage()
    left, top, right, bottom = size * 4, size * 4, -1, -1
    for y in range(image.height()):
        for x in range(image.width()):
            if image.pixelColor(x, y).alpha() < 16:
                continue
            left = min(left, x)
            top = min(top, y)
            right = max(right, x)
            bottom = max(bottom, y)
    if right < left:
        return QIcon()

    cropped = probe.copy(QRect(left, top, right - left + 1, bottom - top + 1))
    scaled = cropped.scaled(
        size,
        size,
        Qt.AspectRatioMode.KeepAspectRatio,
        Qt.TransformationMode.SmoothTransformation,
    )
    icon = QPixmap(size, size)
    icon.fill(Qt.GlobalColor.transparent)
    painter = QPainter(icon)
    painter.drawPixmap((size - scaled.width()) // 2, (size - scaled.height()) // 2, scaled)
    painter.end()
    return QIcon(icon)


class PopupWindow(QWidget):
    """Frameless popup: closes as soon as the user clicks outside it."""

    def __init__(self, app):
        super().__init__(
            None,
            Qt.WindowType.Popup
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.NoDropShadowWindowHint,
        )
        self._app = app
        self._anchor: Optional[QRect] = None
        self._dirty = False
        self._menu_depth = 0
        self._selected_provider_id: Optional[str] = None
        self._tab_ids: list[str] = []
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setFixedWidth(PANEL_WIDTH + 2 * SHADOW_MARGIN)
        self._hooks = CardHooks(
            run_entry=self._app.run_entry,
            menu_guard=self.menu_guard,
        )
        self._build()

    # ----------------------------------------------------------------- layout

    def _build(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(
            SHADOW_MARGIN, SHADOW_MARGIN, SHADOW_MARGIN, SHADOW_MARGIN
        )

        self._panel = QFrame(self)
        self._panel.setObjectName("panel")
        shadow = QGraphicsDropShadowEffect(self._panel)
        shadow.setBlurRadius(24)
        shadow.setOffset(0, 4)
        shadow.setColor(QColor(0, 0, 0, 90))
        self._panel.setGraphicsEffect(shadow)
        outer.addWidget(self._panel)

        panel_layout = QVBoxLayout(self._panel)
        # Side and bottom margins match so the active card sits with PANEL_MARGIN
        # clearance on three sides; the top stays slightly tighter for the header.
        panel_layout.setContentsMargins(PANEL_MARGIN, 10, PANEL_MARGIN, PANEL_MARGIN)
        panel_layout.setSpacing(0)

        self._header = QHBoxLayout()
        self._header.setContentsMargins(0, 0, 0, 0)
        self._header.setSpacing(8)
        panel_layout.addLayout(self._header)
        panel_layout.addSpacing(4)

        self._scroll = QScrollArea(self._panel)
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self._content = QWidget(self._scroll)
        self._content_layout = QVBoxLayout(self._content)
        self._content_layout.setContentsMargins(0, 0, 0, 0)
        self._content_layout.setSpacing(0)
        self._scroll.setWidget(self._content)
        panel_layout.addWidget(self._scroll)

    def _clear(self, layout) -> None:
        while layout.count():
            item = layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.setParent(None)
                widget.deleteLater()
            elif item.layout() is not None:
                self._clear(item.layout())

    @staticmethod
    def _add(layout, widget: QWidget) -> None:
        """Add `widget` and show it right away.

        Qt only shows a widget added to an already-visible parent once the queued
        layout request arrives. Until then the layout treats it as hidden and
        leaves it out of the height :meth:`_resize_to_content` measures, which
        would collapse the panel to its header.
        """
        layout.addWidget(widget)
        widget.show()

    def _tool_button(self, glyph: str, tip: str, palette: theme.Palette) -> QToolButton:
        return _ActionButton(glyph, tip, palette, self._panel)

    def rebuild(self) -> None:
        """Re-render everything from current provider state."""
        if self._menu_depth:
            self._dirty = True
            return
        palette = theme.current()
        self._panel.setStyleSheet(
            f"#panel {{ background: {palette.window};"
            f" border: 1px solid {palette.border}; border-radius: 14px; }}"
            f"QScrollBar:vertical {{ background: transparent; width: 6px; margin: 0; }}"
            f"QScrollBar::handle:vertical {{ background: {palette.border};"
            f" border-radius: 3px; min-height: 24px; }}"
            f"QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}"
            f"QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical"
            f" {{ background: transparent; }}"
            f"QTabWidget::pane {{ border: none; background: transparent; top: 0; margin: 0; padding: 0; }}"
            f"QTabBar::tab {{ background: transparent; color: {palette.faint};"
            f" border: none; border-bottom: 2px solid transparent;"
            f" padding: 6px 0 8px 0; margin: 0 {TAB_BAR_MARGIN_RIGHT}px 0 0; min-height: 24px; }}"
            f"QTabBar::tab:hover {{ color: {palette.subtle}; }}"
            f"QTabBar::tab:selected {{ color: {palette.text};"
            f" border-bottom: 2px solid {palette.text}; }}"
            f"QTabBar::tab:disabled {{ color: {palette.faint}; }}"
        )
        self._scroll.viewport().setStyleSheet("background: transparent;")
        self._content.setStyleSheet("background: transparent;")

        self._build_header(palette)
        self._build_tabs(palette)
        self._resize_to_content()
        if self.isVisible():
            self._place()

    def _build_header(self, palette: theme.Palette) -> None:
        self._clear(self._header)
        title = _label(
            self._panel, tr("LLM 사용량", "LLM usage"), palette.text, 0, QFont.Weight.DemiBold
        )
        self._add(self._header, title)
        updated = self._updated_text()
        if updated:
            self._add(self._header, _label(self._panel, updated, palette.faint, -3))
        self._header.addStretch(1)
        refresh = self._tool_button("⟳", tr("모두 새로고침", "Refresh all"), palette)
        refresh.clicked.connect(self._app.refresh_all)
        settings = self._tool_button("⚙", tr("설정", "Settings"), palette)
        settings.clicked.connect(lambda: self._app.show_settings_menu(settings))
        actions = QHBoxLayout()
        actions.setContentsMargins(0, 0, 0, 0)
        actions.setSpacing(HEADER_ACTION_GAP)
        self._add(actions, refresh)
        self._add(actions, settings)
        self._header.addLayout(actions)

    def _build_tabs(self, palette: theme.Palette) -> None:
        self._clear(self._content_layout)
        self._tab_ids = []
        visible = [provider for provider in self._app.providers if provider.enabled]
        if not visible:
            self._add(
                self._content_layout,
                _label(
                    self._content,
                    tr(
                        "설정에서 서비스를 하나 이상 켜세요",
                        "Enable at least one service in settings",
                    ),
                    palette.faint,
                    -1,
                    wrap=True,
                ),
            )
            return

        tabs = QTabWidget(self._content)
        tabs.setTabBar(_ProviderTabBar(tabs))
        tabs.setDocumentMode(True)
        tabs.setElideMode(Qt.TextElideMode.ElideNone)
        tabs.setUsesScrollButtons(True)
        tabs.setIconSize(QSize(TAB_ICON_SIZE, TAB_ICON_SIZE))
        tabs.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        tabs.tabBar().setExpanding(False)
        tabs.tabBar().setDrawBase(False)

        selected = self._selected_provider_id
        if selected not in {provider.id for provider in visible}:
            selected = visible[0].id
            self._selected_provider_id = selected

        for provider in visible:
            page = QWidget(tabs)
            page_layout = QVBoxLayout(page)
            page_layout.setContentsMargins(0, 0, 0, 0)
            page_layout.setSpacing(0)
            spacer = QWidget(page)
            spacer.setFixedHeight(TAB_TO_CARD_GAP)
            spacer.setStyleSheet("background: transparent;")
            page_layout.addWidget(spacer)
            card = ProviderCard(provider, palette, self._hooks, page)
            page_layout.addWidget(card)
            index = tabs.addTab(page, "")
            tabs.tabBar().setTabButton(
                index,
                QTabBar.ButtonPosition.LeftSide,
                self._tab_label(provider, palette, selected=provider.id == selected),
            )
            tabs.setTabToolTip(index, provider.name)
            self._tab_ids.append(provider.id)

        tabs.tabBar().updateGeometry()
        tabs.currentChanged.connect(self._on_tab_changed)
        self._add(self._content_layout, tabs)
        tabs.setCurrentIndex(self._tab_ids.index(selected))

    def _tab_label(self, provider, palette: theme.Palette, *, selected: bool) -> QWidget:
        """Pre-composited icon + name so the two stay vertically aligned on macOS."""
        color = QColor(palette.text if selected else palette.faint)
        font = _font(self._panel, -1, QFont.Weight.DemiBold if selected else QFont.Weight.Normal)
        metrics = QFontMetrics(font)
        text_width = metrics.horizontalAdvance(provider.name)
        height = max(TAB_ICON_SIZE, metrics.height())
        width = TAB_ICON_SIZE + TAB_ICON_TEXT_GAP + text_width

        canvas = QPixmap(width, height)
        canvas.fill(Qt.GlobalColor.transparent)
        painter = QPainter(canvas)
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        icon = glyphs.provider_pixmap(provider.id, TAB_ICON_SIZE, color)
        # Marks read slightly high next to Latin capitals; shift the glyph down 1px.
        painter.drawPixmap(0, (height - TAB_ICON_SIZE) // 2 + 1, icon)
        painter.setPen(color)
        painter.setFont(font)
        text_y = (height + metrics.ascent() - metrics.descent()) // 2
        painter.drawText(TAB_ICON_SIZE + TAB_ICON_TEXT_GAP, text_y, provider.name)
        painter.end()

        label = QLabel()
        label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        label.setPixmap(canvas)
        label.setFixedSize(width, height)
        label.setStyleSheet("background: transparent; border: none;")
        return label

    def _refresh_tab_labels(self, tabs: QTabWidget, palette: theme.Palette) -> None:
        by_id = {provider.id: provider for provider in self._app.providers}
        for index, provider_id in enumerate(self._tab_ids):
            provider = by_id.get(provider_id)
            if provider is None:
                continue
            tabs.tabBar().setTabButton(
                index,
                QTabBar.ButtonPosition.LeftSide,
                self._tab_label(provider, palette, selected=index == tabs.currentIndex()),
            )
        tabs.tabBar().updateGeometry()

    def _on_tab_changed(self, index: int) -> None:
        if 0 <= index < len(self._tab_ids):
            self._selected_provider_id = self._tab_ids[index]
        tabs = self._content.findChild(QTabWidget)
        if tabs is not None:
            self._refresh_tab_labels(tabs, theme.current())
        if self.isVisible() and not self._menu_depth:
            self._resize_to_content()
            self._place()

    def _updated_text(self) -> str:
        stamps = [
            provider.fetched_at
            for provider in self._app.providers
            if provider.enabled and provider.fetched_at
        ]
        if any(
            provider.state is State.LOADING
            for provider in self._app.providers
            if provider.enabled
        ):
            return tr("가져오는 중...", "Fetching...")
        if not stamps:
            return ""
        when = fmt.clock(min(stamps))
        return tr(f"{when} 업데이트", f"Updated {when}")

    def _resize_to_content(self) -> None:
        """Grow to fit the active tab only, then clamp to the screen."""
        self._content.adjustSize()
        tabs = self._content.findChild(QTabWidget)
        if tabs is not None:
            page = tabs.currentWidget()
            card = page.findChild(ProviderCard) if page is not None else None
            if page is not None and card is not None:
                card.adjustSize()
                page_height = TAB_TO_CARD_GAP + card.sizeHint().height()
                # Every page gets the active one's height: a stacked layout reports the
                # tallest page, so a taller tab visited earlier would otherwise keep the
                # panel stretched below the card.
                for index in range(tabs.count()):
                    tabs.widget(index).setFixedHeight(page_height)
                # Custom tab buttons make sizeHint underestimate the bar; measure after layout.
                tabs.setMinimumHeight(0)
                tabs.setMaximumHeight(16777215)
                tabs.adjustSize()
                bar_height = max(
                    tabs.tabBar().sizeHint().height(),
                    tabs.tabBar().minimumSizeHint().height(),
                    tabs.tabBar().geometry().height(),
                    30,
                )
                wanted = bar_height + page_height
                tabs.setFixedHeight(wanted)
                self._scroll.setFixedHeight(wanted)
                self._settle()
                cap = int(self._screen().availableGeometry().height() * MAX_HEIGHT_FRACTION)
                if self.height() > cap:
                    self._scroll.setFixedHeight(max(120, wanted - (self.height() - cap)))
                    self._settle()
                return

        wanted = self._content.sizeHint().height()
        self._scroll.setFixedHeight(wanted)
        self._settle()
        cap = int(self._screen().availableGeometry().height() * MAX_HEIGHT_FRACTION)
        if self.height() > cap:
            self._scroll.setFixedHeight(max(120, wanted - (self.height() - cap)))
            self._settle()

    def _settle(self) -> None:
        """Shrinking needs the nested layouts recomputed first, otherwise
        ``adjustSize`` resizes against the previous tab's stale size hint."""
        self._panel.layout().activate()
        self.layout().activate()
        self.adjustSize()

    # --------------------------------------------------------------- placement

    def _screen(self):
        point = self._anchor.center() if self._anchor else QCursor.pos()
        return QGuiApplication.screenAt(point) or QGuiApplication.primaryScreen()

    def _place(self) -> None:
        anchor = self._anchor or QRect(QCursor.pos(), QCursor.pos())
        area = self._screen().availableGeometry()
        width, height = self.width(), self.height()
        # The visible panel is inset by the shadow margin, so undo it here to keep
        # the panel edge a couple of pixels from the tray icon.
        x = anchor.center().x() - width // 2
        y = anchor.bottom() + GAP_FROM_TRAY - SHADOW_MARGIN
        if y + height > area.bottom():
            y = anchor.top() - height - GAP_FROM_TRAY + SHADOW_MARGIN
        x = max(area.left() - SHADOW_MARGIN, min(x, area.right() - width + SHADOW_MARGIN))
        y = max(area.top() - SHADOW_MARGIN, min(y, area.bottom() - height + SHADOW_MARGIN))
        self.move(QPoint(x, y))

    def show_near(self, anchor: Optional[QRect]) -> None:
        if anchor is not None and not anchor.isNull() and anchor.width() > 0:
            self._anchor = anchor
        else:
            position = QCursor.pos()
            self._anchor = QRect(position.x(), position.y(), 1, 1)
        self.rebuild()
        self._place()
        self.show()
        self.raise_()
        self.activateWindow()

    # ------------------------------------------------------------------ guards

    @contextmanager
    def menu_guard(self):
        """Suppress rebuilds while a context menu is anchored to our widgets."""
        self._menu_depth += 1
        try:
            yield
        finally:
            self._menu_depth -= 1
            if not self._menu_depth and self._dirty:
                self._dirty = False
                self.rebuild()

    def keyPressEvent(self, event) -> None:  # noqa: N802
        if event.key() == Qt.Key.Key_Escape:
            self.hide()
            return
        super().keyPressEvent(event)

    def hideEvent(self, event) -> None:  # noqa: N802
        self._app.note_popup_hidden()
        super().hideEvent(event)

    def is_menu_open(self) -> bool:
        return isinstance(QApplication.activePopupWidget(), QMenu)
