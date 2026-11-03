"""OpenCode provider: Go usage percentages and absolute reset timestamps."""

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

import requests

from ... import format as fmt
from ...i18n import tr
from ..base import ErrorKind, MenuEntry, Metric, Provider, ProviderError, Section, Snapshot
from . import api, auth

logger = logging.getLogger(__name__)

PERIODS = (
    ("5시간", "5h", "rolling"),
    ("주간", "Week", "weekly"),
    ("월간", "Month", "monthly"),
)


@dataclass
class Loaded:
    go: api.GoUsage


class OpenCodeProvider(Provider):
    id = "opencode"
    name = "OpenCode"
    accent = "#5B9DFF"

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
                logger.warning("OpenCode API key unreadable: %s", exc)
                self._api_key = None
            self._api_key_loaded = True
        return self._api_key

    def is_authenticated(self) -> bool:
        return bool(self.api_key)

    def signed_out_hint(self) -> str:
        return tr("Go 개인 API 키를 입력하세요 (기존 세션 쿠키 사용 불가)",
                  "Add your Go personal API key (not the old session cookie)")

    def load(self) -> Loaded:
        key = self.api_key
        if not key:
            raise ProviderError(tr("API 키가 없습니다", "No API key"), ErrorKind.AUTH)
        try:
            go = api.fetch_usage(key)
        except api.AuthExpiredError as exc:
            raise ProviderError(
                tr("키가 거부되었습니다 · API 키를 다시 입력하세요",
                   "Key rejected · add your API key again"),
                ErrorKind.AUTH,
            ) from exc
        except api.ScopeError as exc:
            # A valid key without Go access must not be erased.
            raise ProviderError(
                tr("Go 사용량 접근 불가 · 개인 API 키 권한과 Go 구독을 확인하세요",
                   "Go usage unavailable · check personal API key permissions and Go subscription"),
                ErrorKind.DATA,
            ) from exc
        except requests.RequestException as exc:
            raise ProviderError(
                tr("네트워크 오류 · 재시도 예정", "Network error · will retry"),
                ErrorKind.NETWORK,
            ) from exc
        except (api.FetchError, api.ParseError) as exc:
            raise ProviderError(
                tr("Go 사용량 응답을 읽지 못했습니다 · 로그 확인",
                   "Could not read the Go usage response · see log"),
                ErrorKind.DATA,
            ) from exc
        return Loaded(go=go)

    def render(self, data: Loaded) -> Snapshot:
        now = datetime.now(timezone.utc)
        metrics = []
        for korean, english, key in PERIODS:
            window = getattr(data.go, key)
            remaining = max(0.0, (window.resets_at - now).total_seconds())
            span = fmt.duration(remaining)
            detail = tr(f"{span} 후 초기화", f"resets in {span}")
            if window.status == "rate-limited":
                detail += tr(" · 사용 제한됨", " · rate-limited")
            metrics.append(Metric(
                label=tr(korean, english),
                # The fuel gauge reads what is left: the text matches the bar.
                value=f"{fmt.percent(100.0 - window.percent)} / {fmt.percent(100.0)}",
                percent=window.percent,
                detail=detail,
            ))
        return Snapshot(
            sections=[Section(
                title=tr("Go 플랜 사용량", "Go plan usage"),
                metrics=metrics,
                url=api.CONSOLE_PAGE,
            )],
            gauge_percent=data.go.rolling.percent,
        )

    def menu(self) -> list[MenuEntry]:
        entries = [
            MenuEntry(
                label=tr("콘솔 열기", "Open console"),
                run=lambda: self.ui.open_url(api.CONSOLE_PAGE),
                background=False,
            ),
            MenuEntry.sep(),
        ]
        if self.is_authenticated():
            entries.append(MenuEntry(
                label=tr("로그아웃", "Sign out"), run=self._sign_out, background=False,
            ))
            # 403 does not sign out: allow replacing a key without deleting it first.
            entries.append(self.primary_action())
        else:
            entries.append(MenuEntry(
                label=tr("클립보드에서 API 키 붙여넣기", "Paste API key from clipboard"),
                run=self._paste_key,
                background=False,
            ))
            entries.append(self.primary_action())
        return entries

    def primary_action(self) -> MenuEntry:
        return MenuEntry(
            label=tr("API 키 입력...", "Enter API key..."),
            run=self._enter_key,
            background=False,
        )

    def _paste_key(self) -> None:
        key = auth.clean_key(self.ui.clipboard_text())
        if not key:
            self.set_message(tr("클립보드가 API 키 형식이 아닙니다",
                                "The clipboard does not look like an API key"))
            return
        self._adopt_key(key)

    def _enter_key(self) -> None:
        value = self.ui.ask_text(
            tr("OpenCode API 키", "OpenCode API key"),
            tr("Go 개인 API 키를 붙여넣으세요:", "Paste your Go personal API key:")
            + "\n\n" + auth.instructions(),
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
        self.ui.notify(tr("OpenCode 키가 거부되었습니다. API 키를 다시 입력하세요.",
                          "The OpenCode key was rejected. Add your API key again."))
