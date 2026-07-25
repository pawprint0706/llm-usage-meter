"""Tray application: owns the providers, the refresh schedule and the popup."""

import logging
import os
import subprocess
import sys
import threading
import webbrowser
from typing import Optional

from PySide6.QtCore import QDateTime, QObject, QRect, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QGuiApplication, QIcon
from PySide6.QtWidgets import QApplication, QInputDialog, QLineEdit, QMenu, QSystemTrayIcon

from . import autostart, config, platform_mac
from .i18n import tr
from .providers import MenuEntry, Provider, State, build_providers, known_provider_ids
from .ui import glyphs, theme
from .ui.popup import PopupWindow
from .ui.widgets import style_menu

logger = logging.getLogger(__name__)

APP_TITLE = "LLM Usage Meter"
TRAY_ICON_SIZES = (16, 18, 20, 22, 24, 32, 44, 64)
UI_TICK_MS = 60_000
THEME_POLL_MS = 5_000
REPAINT_DEBOUNCE_MS = 60
# A click on the tray icon first dismisses the open popup; without this window
# the same click would immediately reopen it.
REOPEN_GUARD_MS = 250


class MeterApp(QObject):
    """Implements ``providers.ProviderUi`` on top of Qt's event loop."""

    _provider_changed = Signal(str)
    _copy_requested = Signal(str)
    _open_requested = Signal(str)
    _notify_requested = Signal(str)
    _refresh_requested = Signal(str)

    def __init__(self, qt_app: QApplication):
        super().__init__()
        self.qt_app = qt_app
        self.cfg = config.load_config()
        self.providers: list[Provider] = build_providers(self.cfg, self)
        self._providers_by_id = {provider.id: provider for provider in self.providers}
        self._tray_ink_light: Optional[bool] = None
        self._tray_percent: Optional[float] = None
        self._popup_hidden_at = 0

        self.popup = PopupWindow(self)
        self.tray = QSystemTrayIcon()
        self.tray.activated.connect(self._on_tray_activated)

        self._connect_signals()
        self._build_timers()
        self._update_tray_icon(force=True)

    # ------------------------------------------------------------------ wiring

    def _connect_signals(self) -> None:
        self._provider_changed.connect(self._on_provider_changed)
        self._copy_requested.connect(self._copy_to_clipboard)
        self._open_requested.connect(self._open_url)
        self._notify_requested.connect(self._show_notification)
        self._refresh_requested.connect(self._refresh_provider_by_id)

        hints = QGuiApplication.styleHints()
        if hints is not None:
            hints.colorSchemeChanged.connect(self._on_color_scheme_changed)

    def _build_timers(self) -> None:
        self._refresh_timer = QTimer(self)
        self._refresh_timer.timeout.connect(self.refresh_all)
        self._apply_refresh_interval()

        self._tick_timer = QTimer(self)
        self._tick_timer.setInterval(UI_TICK_MS)
        self._tick_timer.timeout.connect(self._on_ui_tick)
        self._tick_timer.start()

        self._repaint_timer = QTimer(self)
        self._repaint_timer.setSingleShot(True)
        self._repaint_timer.setInterval(REPAINT_DEBOUNCE_MS)
        self._repaint_timer.timeout.connect(self._repaint)

        self._theme_timer = QTimer(self)
        if sys.platform == "win32":
            self._theme_timer.setInterval(THEME_POLL_MS)
            self._theme_timer.timeout.connect(lambda: self._update_tray_icon())
            self._theme_timer.start()

    def _apply_refresh_interval(self) -> None:
        self._refresh_timer.setInterval(max(1, self.cfg.refresh_interval) * 60_000)
        self._refresh_timer.start()

    # ---------------------------------------------------------------- lifecycle

    def start(self) -> None:
        platform_mac.hide_dock_icon()
        self.tray.show()
        self._run_background(autostart.refresh_if_stale)
        self.refresh_all()

    def quit(self) -> None:
        self._refresh_timer.stop()
        self._tick_timer.stop()
        self._theme_timer.stop()
        for provider in self.providers:
            provider.shutdown()
        self.popup.hide()
        self.tray.hide()
        self.qt_app.quit()

    @staticmethod
    def _run_background(target, *args) -> None:
        threading.Thread(target=target, args=args, daemon=True).start()

    # ------------------------------------------------------- ProviderUi (any thread)

    def changed(self, provider: Provider) -> None:
        self._provider_changed.emit(provider.id)

    def copy_to_clipboard(self, text: str) -> None:
        self._copy_requested.emit(text)

    def open_url(self, url: str) -> None:
        self._open_requested.emit(url)

    def notify(self, message: str) -> None:
        self._notify_requested.emit(message)

    def request_refresh(self, provider: Provider) -> None:
        self._refresh_requested.emit(provider.id)

    # ------------------------------------------------- ProviderUi (GUI thread only)

    def clipboard_text(self) -> Optional[str]:
        clipboard = QGuiApplication.clipboard()
        return clipboard.text() if clipboard else None

    def ask_text(self, title: str, prompt: str, secret: bool = False) -> Optional[str]:
        # The popup is a grabbing window; it must go away before a modal dialog.
        self.popup.hide()
        platform_mac.activate_app()
        dialog = QInputDialog()
        dialog.setWindowTitle(f"{APP_TITLE} · {title}")
        dialog.setLabelText(prompt)
        dialog.setInputMode(QInputDialog.InputMode.TextInput)
        dialog.setTextEchoMode(
            QLineEdit.EchoMode.Password if secret else QLineEdit.EchoMode.Normal
        )
        # Qt's own translations are not shipped, so the buttons would stay English.
        dialog.setOkButtonText(tr("확인", "OK"))
        dialog.setCancelButtonText(tr("취소", "Cancel"))
        dialog.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, True)
        dialog.resize(460, dialog.height())
        if not dialog.exec():
            return None
        return dialog.textValue()

    # ------------------------------------------------------------------- slots

    def _on_provider_changed(self, _provider_id: str) -> None:
        self._repaint_timer.start()

    def _repaint(self) -> None:
        self._update_tray_icon()
        if self.popup.isVisible():
            self.popup.rebuild()

    def _copy_to_clipboard(self, text: str) -> None:
        clipboard = QGuiApplication.clipboard()
        if clipboard:
            clipboard.setText(text)

    def _open_url(self, url: str) -> None:
        self.popup.hide()
        try:
            webbrowser.open(url, new=2)
        except Exception:
            logger.exception("Could not open %s", url)

    def _show_notification(self, message: str) -> None:
        if platform_mac.notify(APP_TITLE, message):
            return
        if QSystemTrayIcon.supportsMessages():
            self.tray.showMessage(APP_TITLE, message, QSystemTrayIcon.MessageIcon.Information)

    def _refresh_provider_by_id(self, provider_id: str) -> None:
        provider = self._providers_by_id.get(provider_id)
        if provider:
            self._run_background(provider.refresh)

    def _on_color_scheme_changed(self, _scheme) -> None:
        self._update_tray_icon(force=True)
        if self.popup.isVisible():
            self.popup.rebuild()

    def _on_ui_tick(self) -> None:
        for provider in self.providers:
            if provider.enabled and provider.state is State.READY:
                provider.rebuild()
        self._repaint_timer.start()

    # ------------------------------------------------------------------- refresh

    def refresh_all(self) -> None:
        for provider in self.providers:
            if provider.enabled:
                self._run_background(provider.refresh)

    # ---------------------------------------------------------------------- tray

    def _on_tray_activated(self, reason: QSystemTrayIcon.ActivationReason) -> None:
        if reason not in (
            QSystemTrayIcon.ActivationReason.Trigger,
            QSystemTrayIcon.ActivationReason.Context,
            QSystemTrayIcon.ActivationReason.DoubleClick,
        ):
            return
        self.toggle_popup()

    def toggle_popup(self) -> None:
        if self.popup.isVisible():
            self.popup.hide()
            return
        if self._popup_hidden_recently():
            return
        self.popup.show_near(self._tray_rect())
        # Stale numbers are worse than a brief spinner, so top up on open.
        for provider in self.providers:
            if provider.enabled and provider.state is State.ERROR:
                self._run_background(provider.refresh)

    def note_popup_hidden(self) -> None:
        self._popup_hidden_at = self._now_ms()

    def _popup_hidden_recently(self) -> bool:
        return self._now_ms() - self._popup_hidden_at < REOPEN_GUARD_MS

    @staticmethod
    def _now_ms() -> int:
        return QDateTime.currentMSecsSinceEpoch()

    def _tray_rect(self) -> Optional[QRect]:
        rect = self.tray.geometry()
        if rect.isNull() or rect.width() <= 0 or rect.height() <= 0:
            return None
        return rect

    def _gauge_percent(self) -> Optional[float]:
        values = [
            provider.snapshot.gauge_percent
            for provider in self.providers
            if provider.enabled and provider.snapshot and provider.snapshot.gauge_percent is not None
        ]
        return max(values) if values else None

    def _update_tray_icon(self, force: bool = False) -> None:
        light_ink = theme.tray_needs_light_ink()
        percent = self._gauge_percent()
        if not force and light_ink == self._tray_ink_light and percent == self._tray_percent:
            return
        self._tray_ink_light = light_ink
        self._tray_percent = percent
        template = sys.platform == "darwin"
        color = QColor("#000000") if template or not light_ink else QColor("#FFFFFF")
        icon = QIcon()
        for size in TRAY_ICON_SIZES:
            icon.addPixmap(glyphs.gauge_pixmap(size, percent, color))
        icon.setIsMask(template)
        self.tray.setIcon(icon)

    # ------------------------------------------------------------------- actions

    def run_entry(self, entry: MenuEntry) -> None:
        if entry.run is None or not entry.enabled:
            return
        if entry.background:
            self._run_background(self._guarded, entry)
        else:
            self._guarded(entry)

    @staticmethod
    def _guarded(entry: MenuEntry) -> None:
        try:
            entry.run()
        except Exception:
            logger.exception("Menu action %r failed", entry.label)

    def show_settings_menu(self, anchor) -> None:
        palette = theme.current()
        menu = QMenu(self.popup)
        style_menu(menu, palette)

        interval_menu = menu.addMenu(tr("새로고침 주기", "Refresh interval"))
        style_menu(interval_menu, palette)
        for minutes in config.REFRESH_OPTIONS:
            action = interval_menu.addAction(tr(f"{minutes}분", f"{minutes} min"))
            action.setCheckable(True)
            action.setChecked(self.cfg.refresh_interval == minutes)
            action.triggered.connect(
                lambda _checked=False, value=minutes: self._set_refresh_interval(value)
            )

        services_menu = menu.addMenu(tr("표시할 서비스", "Services shown"))
        style_menu(services_menu, palette)
        for provider in self.providers:
            action = services_menu.addAction(provider.name)
            action.setCheckable(True)
            action.setChecked(provider.enabled)
            action.triggered.connect(
                lambda checked, pid=provider.id: self._set_provider_enabled(pid, checked)
            )

        order_menu = menu.addMenu(tr("탭 순서", "Tab order"))
        style_menu(order_menu, palette)
        for index, provider in enumerate(self.providers):
            item = order_menu.addMenu(f"{index + 1}. {provider.name}")
            style_menu(item, palette)
            up = item.addAction(tr("앞으로 이동", "Move up"))
            up.setEnabled(index > 0)
            up.triggered.connect(
                lambda _checked=False, pid=provider.id: self._move_provider(pid, -1)
            )
            down = item.addAction(tr("뒤로 이동", "Move down"))
            down.setEnabled(index < len(self.providers) - 1)
            down.triggered.connect(
                lambda _checked=False, pid=provider.id: self._move_provider(pid, 1)
            )

        autostart_action = menu.addAction(tr("로그인 시 자동 시작", "Start at login"))
        autostart_action.setCheckable(True)
        autostart_action.setChecked(autostart.is_enabled())
        autostart_action.triggered.connect(self._toggle_autostart)

        menu.addSeparator()
        data_action = menu.addAction(tr("데이터 폴더 열기", "Open data folder"))
        data_action.triggered.connect(lambda: self._reveal(config.config_dir()))
        menu.addSeparator()
        quit_action = menu.addAction(tr("종료", "Quit"))
        quit_action.triggered.connect(self.quit)

        with self.popup.menu_guard():
            menu.exec(anchor.mapToGlobal(anchor.rect().bottomLeft()))

    def _set_refresh_interval(self, minutes: int) -> None:
        self.cfg.refresh_interval = minutes
        config.save_config(self.cfg)
        self._apply_refresh_interval()

    def _set_provider_enabled(self, provider_id: str, enabled: bool) -> None:
        self.cfg.set_provider_enabled(provider_id, enabled)
        config.save_config(self.cfg)
        provider = self._providers_by_id.get(provider_id)
        if provider and enabled:
            self._run_background(provider.refresh)
        self._repaint_timer.start()
        if self.popup.isVisible():
            self.popup.rebuild()

    def _move_provider(self, provider_id: str, delta: int) -> None:
        if not self.cfg.move_provider(provider_id, delta, known_provider_ids()):
            return
        config.save_config(self.cfg)
        self.providers = [
            self._providers_by_id[provider_id]
            for provider_id in self.cfg.ordered_provider_ids(known_provider_ids())
            if provider_id in self._providers_by_id
        ]
        if self.popup.isVisible():
            self.popup.rebuild()
        self._repaint_timer.start()

    def _toggle_autostart(self) -> None:
        try:
            autostart.toggle()
        except Exception as exc:
            logger.exception("Could not change start-at-login")
            self._show_notification(
                tr(f"자동 시작을 변경하지 못했습니다: {exc}", f"Could not change start-at-login: {exc}")
            )

    @staticmethod
    def _reveal(path: str) -> None:
        try:
            if sys.platform == "darwin":
                subprocess.run(["open", path], check=False, timeout=10)
            elif sys.platform == "win32":
                os.startfile(path)  # noqa: S606 — opening a folder we own
            else:
                subprocess.run(["xdg-open", path], check=False, timeout=10)
        except Exception:
            logger.exception("Could not open %s", path)
