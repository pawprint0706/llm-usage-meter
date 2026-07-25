"""The panel that opens next to the tray icon."""

import logging
from contextlib import contextmanager
from typing import Optional

from PySide6.QtCore import QPoint, QRect, Qt
from PySide6.QtGui import QColor, QCursor, QFont, QGuiApplication
from PySide6.QtWidgets import (
    QApplication,
    QFrame,
    QGraphicsDropShadowEffect,
    QHBoxLayout,
    QMenu,
    QScrollArea,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from .. import format as fmt
from ..i18n import tr
from ..providers import State
from . import theme
from .widgets import CardHooks, LinkLabel, ProviderCard, _font, _label

logger = logging.getLogger(__name__)

PANEL_WIDTH = 348
SHADOW_MARGIN = 10
GAP_FROM_TRAY = 2
MAX_HEIGHT_FRACTION = 0.82


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
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setFixedWidth(PANEL_WIDTH + 2 * SHADOW_MARGIN)
        self._hooks = CardHooks(
            open_url=self._app.open_url,
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
        panel_layout.setContentsMargins(12, 10, 12, 10)
        panel_layout.setSpacing(9)

        self._header = QHBoxLayout()
        self._header.setContentsMargins(0, 0, 0, 0)
        self._header.setSpacing(4)
        panel_layout.addLayout(self._header)

        self._scroll = QScrollArea(self._panel)
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self._content = QWidget(self._scroll)
        self._content_layout = QVBoxLayout(self._content)
        self._content_layout.setContentsMargins(0, 0, 0, 0)
        self._content_layout.setSpacing(8)
        self._scroll.setWidget(self._content)
        panel_layout.addWidget(self._scroll)

        self._footer = QHBoxLayout()
        self._footer.setContentsMargins(0, 0, 0, 0)
        self._footer.setSpacing(8)
        panel_layout.addLayout(self._footer)

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
        would collapse the panel to its header and footer.
        """
        layout.addWidget(widget)
        widget.show()

    def _tool_button(self, glyph: str, tip: str, palette: theme.Palette) -> QToolButton:
        button = QToolButton(self._panel)
        button.setText(glyph)
        button.setToolTip(tip)
        button.setCursor(Qt.CursorShape.PointingHandCursor)
        button.setFont(_font(self._panel, 2))
        button.setStyleSheet(
            f"QToolButton {{ color: {palette.subtle}; border: none; background: transparent;"
            f" padding: 0 5px; }}"
            f"QToolButton:hover {{ color: {palette.text}; }}"
        )
        return button

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
        )
        self._scroll.viewport().setStyleSheet("background: transparent;")
        self._content.setStyleSheet("background: transparent;")

        self._build_header(palette)
        self._build_cards(palette)
        self._build_footer(palette)
        self._resize_to_content()
        if self.isVisible():
            self._place()

    def _build_header(self, palette: theme.Palette) -> None:
        self._clear(self._header)
        title = _label(
            self._panel, tr("LLM 사용량", "LLM usage"), palette.text, 0, QFont.Weight.DemiBold
        )
        self._add(self._header, title)
        self._header.addStretch(1)
        refresh = self._tool_button("⟳", tr("모두 새로고침", "Refresh all"), palette)
        refresh.clicked.connect(self._app.refresh_all)
        self._add(self._header, refresh)
        settings = self._tool_button("⚙", tr("설정", "Settings"), palette)
        settings.clicked.connect(lambda: self._app.show_settings_menu(settings))
        self._add(self._header, settings)

    def _build_cards(self, palette: theme.Palette) -> None:
        self._clear(self._content_layout)
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
        for provider in visible:
            self._add(
                self._content_layout, ProviderCard(provider, palette, self._hooks, self._content)
            )
        self._content_layout.addStretch(1)

    def _build_footer(self, palette: theme.Palette) -> None:
        self._clear(self._footer)
        self._add(self._footer, _label(self._panel, self._updated_text(), palette.faint, -3))
        self._footer.addStretch(1)
        quit_link = LinkLabel(tr("종료", "Quit"), palette.faint, self._panel)
        quit_link.setFont(_font(self._panel, -3))
        quit_link.clicked.connect(self._app.quit)
        self._add(self._footer, quit_link)

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
        """Grow to fit the cards, then give the scroll area whatever is left."""
        self._content.adjustSize()
        wanted = self._content.sizeHint().height()
        self._scroll.setFixedHeight(wanted)
        self.adjustSize()
        cap = int(self._screen().availableGeometry().height() * MAX_HEIGHT_FRACTION)
        if self.height() > cap:
            self._scroll.setFixedHeight(max(120, wanted - (self.height() - cap)))
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
