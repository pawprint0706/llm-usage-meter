"""Ollama Cloud provider: session/weekly usage, model stats and extra-usage balance."""

import logging
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

import requests

from ... import format as fmt
from ...i18n import tr
from ..base import ErrorKind, MenuEntry, Metric, Provider, ProviderError, Section, Snapshot
from . import api, auth

logger = logging.getLogger(__name__)


@dataclass
class Loaded:
    data: api.SettingsData
    monotonic: float


class OllamaProvider(Provider):
    id = "ollama"
    name = "Ollama"
    accent = "#4F46E5"

    def __init__(self, cfg, ui):
        super().__init__(cfg, ui)
        self._aid: Optional[str] = None
        self._session: Optional[str] = None
        self._credentials_loaded = False

    # ------------------------------------------------------------------ auth

    def _load_credentials(self) -> None:
        if self._credentials_loaded:
            return
        try:
            self._aid = auth.load_aid()
        except auth.AuthError as exc:
            logger.warning("Ollama aid unreadable: %s", exc)
            self._aid = None
        try:
            self._session = auth.load_session()
        except auth.AuthError as exc:
            logger.warning("Ollama session unreadable: %s", exc)
            self._session = None
        self._credentials_loaded = True

    def _invalidate_credentials_cache(self) -> None:
        self._aid = None
        self._session = None
        self._credentials_loaded = False

    def is_authenticated(self) -> bool:
        self._load_credentials()
        return bool(self._aid and self._session)

    def signed_out_hint(self) -> str:
        return tr("세션 쿠키를 입력하세요", "Add your session cookies")

    # ------------------------------------------------------------------ data

    def load(self) -> Loaded:
        self._load_credentials()
        if not self._aid or not self._session:
            raise ProviderError(tr("세션 쿠키가 없습니다", "No session cookies"), ErrorKind.AUTH)
        try:
            data = api.fetch_settings(self._aid, self._session)
        except api.AuthExpiredError as exc:
            raise ProviderError(
                tr(
                    "세션이 만료되었습니다 · 세션 쿠키를 다시 입력하세요",
                    "Session expired · add your session cookies again",
                ),
                ErrorKind.AUTH,
            ) from exc
        except requests.RequestException as exc:
            raise ProviderError(
                tr("네트워크 오류 · 재시도 예정", "Network error · will retry"),
                ErrorKind.NETWORK,
            ) from exc
        except api.FetchError as exc:
            raise ProviderError(
                tr(
                    "설정 페이지에서 사용량을 읽지 못했습니다 · 로그 확인",
                    "Could not read usage from the settings page · see log",
                ),
                ErrorKind.DATA,
            ) from exc
        return Loaded(data=data, monotonic=time.monotonic())

    def render(self, data: Loaded) -> Snapshot:
        sections = [self._usage_section(data), self._balance_section(data.data)]
        return Snapshot(
            sections=sections,
            badge=data.data.plan,
            gauge_percent=self._gauge_percent(data.data),
        )

    def _usage_section(self, data: Loaded) -> Section:
        windows = [window for window in (data.data.session, data.data.weekly) if window]
        if not windows:
            return Section(
                title=tr("클라우드 사용량", "Cloud usage"),
                url=api.SETTINGS_PAGE,
                note=tr(
                    "사용량 정보 가져오기 실패. 구독 상태를 확인하세요",
                    "Could not fetch the usage. Check your subscription",
                ),
            )
        metrics: list[Metric] = []
        for window in windows:
            label = tr(
                "세션 사용량" if window.label == "Session usage" else "주간 사용량",
                "Session usage" if window.label == "Session usage" else "Weekly usage",
            )
            detail = None
            if window.reset_at is not None:
                remaining = (window.reset_at - datetime.now(window.reset_at.tzinfo)).total_seconds()
                span = fmt.duration(max(0.0, remaining))
                detail = tr(f"{span} 후 초기화", f"resets in {span}")
            metrics.append(
                Metric(
                    label=label,
                    value=fmt.percent(window.percent, decimals=1),
                    percent=window.percent,
                    detail=detail,
                )
            )
        # The webpage lists "Models used this week" — the weekly meter's
        # per-model request counts, which can differ from the session's.
        models = data.data.weekly.models if data.data.weekly else []
        if not models and data.data.session:
            models = data.data.session.models
        if models:
            for model in models:
                metrics.append(
                    Metric(
                        label=model.name,
                        value=tr(f"{model.requests}회", f"{model.requests} req"),
                        muted=True,
                    )
                )
        return Section(
            title=tr("클라우드 사용량", "Cloud usage"),
            metrics=metrics,
            url=api.SETTINGS_PAGE,
        )

    def _balance_section(self, data: api.SettingsData) -> Section:
        title = tr("추가 사용량", "Extra usage")
        if data.balance is None:
            return Section(
                title=title,
                url=api.SETTINGS_PAGE,
                note=tr("잔액 정보 가져오기 실패", "Could not fetch the balance"),
            )
        metrics = [
            Metric(
                label=tr("잔액", "Balance"),
                value=data.balance_text or fmt.money(data.balance),
                muted=data.balance <= 0,
            )
        ]
        return Section(title=title, metrics=metrics, url=api.SETTINGS_PAGE)

    def _gauge_percent(self, data: api.SettingsData) -> Optional[float]:
        percents = [
            window.percent for window in (data.session, data.weekly) if window
        ]
        return max(percents) if percents else None

    # ------------------------------------------------------------------ menu

    def menu(self) -> list[MenuEntry]:
        entries = [
            MenuEntry(
                label=tr("사용량 페이지 열기", "Open usage page"),
                run=lambda: self.ui.open_url(api.SETTINGS_PAGE),
                background=False,
            ),
            MenuEntry.sep(),
        ]
        if self.is_authenticated():
            entries.append(
                MenuEntry(label=tr("로그아웃", "Sign out"), run=self._sign_out, background=False)
            )
        else:
            entries.append(
                MenuEntry(
                    label=tr("클립보드에서 aid 붙여넣기", "Paste aid from clipboard"),
                    run=self._paste_aid,
                    background=False,
                )
            )
            entries.append(
                MenuEntry(
                    label=tr(
                        "클립보드에서 __Secure-session 붙여넣기",
                        "Paste __Secure-session from clipboard",
                    ),
                    run=self._paste_session,
                    background=False,
                )
            )
            entries.append(self.primary_action())
        return entries

    def primary_action(self) -> MenuEntry:
        return MenuEntry(
            label=tr("세션 쿠키 입력...", "Enter session cookies..."),
            run=self._enter_credentials,
            background=False,
        )

    # --------------------------------------------------------------- session

    def _paste_aid(self) -> None:
        """GUI thread: read the clipboard and adopt it as the aid cookie."""
        value = auth.clean_aid(self.ui.clipboard_text())
        if not value:
            self.set_message(
                tr(
                    "클립보드가 aid 쿠키 형식이 아닙니다",
                    "The clipboard does not look like an aid cookie",
                )
            )
            return
        self._adopt_aid(value)

    def _paste_session(self) -> None:
        """GUI thread: read the clipboard and adopt it as the session cookie."""
        value = auth.clean_session(self.ui.clipboard_text())
        if not value:
            self.set_message(
                tr(
                    "클립보드가 __Secure-session 쿠키 형식이 아닙니다",
                    "The clipboard does not look like a __Secure-session cookie",
                )
            )
            return
        self._adopt_session(value)

    def _enter_credentials(self) -> None:
        """GUI thread: ask for both cookies in two modal prompts."""
        aid = self.ui.ask_text(
            tr("Ollama aid 쿠키", "Ollama aid cookie"),
            tr("'aid' 쿠키 값을 붙여넣으세요:", "Paste your 'aid' cookie value:")
            + "\n\n"
            + auth.instructions(),
            secret=True,
        )
        if aid is None:
            return
        cleaned_aid = auth.clean_aid(aid)
        if not cleaned_aid:
            self.set_message(tr("잘못된 aid 쿠키입니다", "That is not a valid aid cookie"))
            return
        self._adopt_aid(cleaned_aid)
        session = self.ui.ask_text(
            tr("Ollama __Secure-session 쿠키", "Ollama __Secure-session cookie"),
            tr(
                "'__Secure-session' 쿠키 값을 붙여넣으세요:",
                "Paste your '__Secure-session' cookie value:",
            ),
            secret=True,
        )
        if session is None:
            return
        cleaned_session = auth.clean_session(session)
        if not cleaned_session:
            self.set_message(
                tr("잘못된 __Secure-session 쿠키입니다", "That is not a valid __Secure-session cookie")
            )
            return
        self._adopt_session(cleaned_session)

    def _adopt_aid(self, value: str) -> None:
        try:
            auth.save_aid(value)
        except auth.AuthError as exc:
            self.set_message(str(exc))
            return
        self._aid = value
        self._credentials_loaded = True
        self.set_message(tr("aid 쿠키를 저장했습니다", "aid cookie saved"))
        self.ui.request_refresh(self)

    def _adopt_session(self, value: str) -> None:
        try:
            auth.save_session(value)
        except auth.AuthError as exc:
            self.set_message(str(exc))
            return
        self._session = value
        self._credentials_loaded = True
        self.set_message(tr("__Secure-session 쿠키를 저장했습니다", "__Secure-session cookie saved"))
        self.ui.request_refresh(self)

    def _sign_out(self) -> None:
        try:
            auth.delete_aid()
        except auth.AuthError as exc:
            self.set_message(str(exc))
            return
        try:
            auth.delete_session()
        except auth.AuthError as exc:
            self.set_message(str(exc))
            return
        self._invalidate_credentials_cache()
        self.reset(tr("로그아웃됨", "Signed out"))

    def on_auth_error(self) -> None:
        try:
            auth.delete_aid()
        except auth.AuthError:
            logger.warning("Could not remove the rejected aid cookie", exc_info=True)
        try:
            auth.delete_session()
        except auth.AuthError:
            logger.warning("Could not remove the rejected session cookie", exc_info=True)
        self._invalidate_credentials_cache()
        self.ui.notify(
            tr(
                "Ollama 세션이 만료되었습니다. 세션 쿠키를 다시 입력하세요.",
                "The Ollama session expired. Add your session cookies again.",
            )
        )
