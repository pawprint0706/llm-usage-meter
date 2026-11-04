"""Ollama Cloud provider: documented balances and rolling 30-day model usage."""

import logging
from datetime import datetime, timezone
from typing import Optional

import requests

from ... import format as fmt
from ...i18n import tr
from ..base import ErrorKind, MenuEntry, Metric, Provider, ProviderError, Section, Snapshot
from . import api, auth

logger = logging.getLogger(__name__)


class OllamaProvider(Provider):
    id = "ollama"
    name = "Ollama"
    accent = "#4F46E5"

    def __init__(self, cfg, ui):
        super().__init__(cfg, ui)
        self._api_key: Optional[str] = None
        self._api_key_loaded = False

    @property
    def api_key(self) -> Optional[str]:
        if not self._api_key_loaded:
            try:
                self._api_key = auth.load_key()
            except auth.AuthError as exc:
                logger.warning("Ollama API key unreadable: %s", exc)
                self._api_key = None
            self._api_key_loaded = True
        return self._api_key

    def is_authenticated(self) -> bool:
        return bool(self.api_key)

    def signed_out_hint(self) -> str:
        return tr("API 키를 입력하세요", "Add your API key")

    def load(self) -> api.CloudData:
        key = self.api_key
        if not key:
            raise ProviderError(tr("API 키가 없습니다", "No API key"), ErrorKind.AUTH)
        try:
            return api.fetch_cloud(key)
        except api.AuthExpiredError as exc:
            raise ProviderError(
                tr("키가 거부되었습니다 · API 키를 다시 입력하세요",
                   "Key rejected · add your API key again"),
                ErrorKind.AUTH,
            ) from exc
        except api.ScopeError as exc:
            raise ProviderError(
                tr("Ollama 계정 접근이 제한되었습니다 (403) · 계정 상태 확인",
                   "Ollama account access denied (403) · check account status"),
                ErrorKind.DATA,
            ) from exc
        except api.RateLimitError as exc:
            if exc.retry_after is not None:
                message = tr(f"요청 제한 · {exc.retry_after}초 후 다시 시도하세요",
                             f"Rate limited · try again after {exc.retry_after}s")
            else:
                message = tr("요청 제한 · 잠시 후 다시 시도하세요",
                             "Rate limited · try again later")
            raise ProviderError(message, ErrorKind.DATA) from exc
        except requests.RequestException as exc:
            raise ProviderError(
                tr("네트워크 오류 · 재시도 예정", "Network error · will retry"),
                ErrorKind.NETWORK,
            ) from exc
        except (api.FetchError, api.ParseError) as exc:
            raise ProviderError(
                tr("Ollama API 응답을 읽지 못했습니다 · 로그 확인",
                   "Could not read the Ollama API response · see log"),
                ErrorKind.DATA,
            ) from exc

    def render(self, data: api.CloudData) -> Snapshot:
        windows = data.balance.windows
        return Snapshot(
            sections=[
                self._usage_section(data.balance),
                Section(
                    title=tr("구매 크레딧", "Purchased credits"),
                    metrics=[Metric(
                        label=tr("잔액", "Balance"),
                        value=fmt.money(data.balance.purchased),
                        muted=data.balance.purchased <= 0,
                    )],
                    url=api.SETTINGS_PAGE,
                ),
                self._history_section(data),
            ],
            gauge_percent=windows[0].percent if windows else None,
        )

    def _usage_section(self, balance: api.BalanceData) -> Section:
        metrics = []
        now = datetime.now(timezone.utc)
        labels = {"monthly": tr("월간", "Monthly"),
                  "session": tr("세션", "Session"), "weekly": tr("주간", "Weekly")}
        for window in balance.windows:
            span = fmt.duration(max(0.0, (window.reset_at - now).total_seconds()))
            detail = tr(f"{span} 후 초기화", f"resets in {span}")
            if window.balance is not None and window.total is not None:
                value = tr(f"{fmt.money(window.balance)} / {fmt.money_compact(window.total)}",
                           f"{fmt.money(window.balance)} of {fmt.money_compact(window.total)}")
            else:
                left = fmt.percent(100.0 - window.percent, 1)
                value = tr(f"{left} 남음", f"{left} remaining")
            metrics.append(Metric(label=labels[window.label], value=value,
                                  percent=window.percent, detail=detail))
        note = None
        if balance.included_error:
            note = tr("기본 사용량 응답을 읽지 못했습니다 · 로그 확인",
                      "Could not read the included usage response · see log")
        return Section(title=tr("기본 사용량", "Included usage"), metrics=metrics,
                       url=api.SETTINGS_PAGE, note=note)

    def _history_section(self, data: api.CloudData) -> Section:
        title = tr("최근 30일 요청", "Requests in the last 30 days")
        if data.history is None:
            return Section(title=title, note=tr("요청 통계 가져오기 실패", "Could not fetch request statistics"),
                           url=api.SETTINGS_PAGE)
        history = data.history
        metrics = [Metric(label=tr("전체", "Total"),
                          value=tr(f"{history.requests}회", f"{history.requests} req"))]
        metrics.extend(Metric(label=model.name,
                              value=tr(f"{model.requests}회", f"{model.requests} req"), muted=True)
                       for model in history.models)
        if history.other_requests:
            metrics.append(Metric(label=tr("기타 모델", "Other models"),
                                  value=tr(f"{history.other_requests}회", f"{history.other_requests} req"),
                                  muted=True))
        return Section(title=title, metrics=metrics, url=api.SETTINGS_PAGE)

    def menu(self) -> list[MenuEntry]:
        entries = [
            MenuEntry(label=tr("사용량 페이지 열기", "Open usage page"),
                      run=lambda: self.ui.open_url(api.SETTINGS_PAGE), background=False),
            MenuEntry.sep(),
        ]
        if self.is_authenticated():
            entries.append(MenuEntry(label=tr("로그아웃", "Sign out"),
                                     run=self._sign_out, background=False))
        else:
            entries.append(MenuEntry(label=tr("클립보드에서 API 키 붙여넣기", "Paste API key from clipboard"),
                                     run=self._paste_key, background=False))
        entries.append(self.primary_action())
        return entries

    def primary_action(self) -> MenuEntry:
        return MenuEntry(label=tr("API 키 입력...", "Enter API key..."),
                         run=self._enter_key, background=False)

    def _paste_key(self) -> None:
        key = auth.clean_key(self.ui.clipboard_text())
        if not key:
            self.set_message(tr("클립보드가 API 키 형식이 아닙니다",
                                "The clipboard does not look like an API key"))
            return
        self._adopt_key(key)

    def _enter_key(self) -> None:
        value = self.ui.ask_text(
            tr("Ollama API 키", "Ollama API key"),
            tr("API 키를 붙여넣으세요:", "Paste your API key:") + "\n\n" + auth.instructions(),
            secret=True,
        )
        if value is None:
            return
        key = auth.clean_key(value)
        if not key:
            self.set_message(tr("잘못된 API 키입니다", "That is not a valid API key"))
            return
        self._adopt_key(key)

    def _adopt_key(self, key: str) -> None:
        try:
            auth.save_key(key)
        except auth.AuthError as exc:
            self.set_message(str(exc))
            return
        self._api_key = key
        self._api_key_loaded = True
        self.reset(tr("API 키를 저장했습니다", "API key saved"))
        self.ui.request_refresh(self)

    def _sign_out(self) -> None:
        try:
            auth.delete_key()
        except auth.AuthError as exc:
            self.set_message(str(exc))
            return
        self._api_key = None
        self._api_key_loaded = True
        self.reset(tr("로그아웃됨", "Signed out"))

    def on_auth_error(self) -> None:
        try:
            auth.delete_key()
        except auth.AuthError:
            logger.warning("Could not remove the rejected API key", exc_info=True)
        self._api_key = None
        self._api_key_loaded = True
        self.ui.notify(tr("Ollama 키가 거부되었습니다. API 키를 다시 입력하세요.",
                          "The Ollama key was rejected. Add your API key again."))
