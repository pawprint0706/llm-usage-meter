"""OpenCode provider: Go plan usage and Zen credit balance as separate sections."""

import logging
import time
from dataclasses import dataclass
from typing import Optional

import requests

from ... import config as config_module
from ... import format as fmt
from ...i18n import tr
from ..base import ErrorKind, MenuEntry, Metric, Provider, ProviderError, Section, Snapshot
from . import api, auth

logger = logging.getLogger(__name__)

DEFAULT_LIMITS = {
    "rolling": 12.0,   # $ per 5-hour window
    "weekly": 30.0,    # $ per week
    "monthly": 60.0,   # $ per month
}

# (Korean label, English label, GoUsage attribute == limits key)
PERIODS = (
    ("5시간", "5h", "rolling"),
    ("주간", "Week", "weekly"),
    ("월간", "Month", "monthly"),
)


@dataclass
class Loaded:
    console: api.ConsoleData
    monotonic: float


class OpenCodeProvider(Provider):
    id = "opencode"
    name = "OpenCode"
    accent = "#5B9DFF"

    def __init__(self, cfg, ui):
        super().__init__(cfg, ui)
        self._session_key: Optional[str] = None
        self._session_key_loaded = False

    # ------------------------------------------------------------------ auth

    @property
    def session_key(self) -> Optional[str]:
        if not self._session_key_loaded:
            try:
                self._session_key = auth.load_session_key()
            except auth.AuthError as exc:
                logger.warning("OpenCode session key unreadable: %s", exc)
                self._session_key = None
            self._session_key_loaded = True
        return self._session_key

    def is_authenticated(self) -> bool:
        return bool(self.session_key)

    def signed_out_hint(self) -> str:
        return tr("세션 키를 입력하세요", "Add your session key")

    # --------------------------------------------------------------- settings

    @property
    def workspace_id(self) -> Optional[str]:
        value = self.settings.get("workspace_id")
        return value if isinstance(value, str) and value else None

    @workspace_id.setter
    def workspace_id(self, value: Optional[str]) -> None:
        self.settings["workspace_id"] = value
        config_module.save_config(self.cfg)

    @property
    def limits(self) -> dict[str, float]:
        raw = self.settings.get("limits")
        limits = dict(DEFAULT_LIMITS)
        if isinstance(raw, dict):
            for key, default in DEFAULT_LIMITS.items():
                try:
                    limits[key] = float(raw.get(key, default))
                except (TypeError, ValueError):
                    limits[key] = default
        return limits

    # ------------------------------------------------------------------ data

    def load(self) -> Loaded:
        key = self.session_key
        if not key:
            raise ProviderError(tr("세션 키가 없습니다", "No session key"), ErrorKind.AUTH)
        try:
            if not self.workspace_id:
                discovered = api.find_workspace_id(key)
                if not discovered:
                    raise ProviderError(
                        tr(
                            "이 계정에 워크스페이스가 없습니다",
                            "No workspace found for this account",
                        ),
                        ErrorKind.DATA,
                    )
                self.workspace_id = discovered
            console = api.fetch_console(key, self.workspace_id)
        except api.AuthExpiredError as exc:
            raise ProviderError(
                tr(
                    "세션이 만료되었습니다 · 세션 키를 다시 입력하세요",
                    "Session expired · add your session key again",
                ),
                ErrorKind.AUTH,
            ) from exc
        except requests.RequestException as exc:
            raise ProviderError(
                tr("네트워크 오류 · 재시도 예정", "Network error · will retry"),
                ErrorKind.NETWORK,
            ) from exc
        except api.ParseError as exc:
            raise ProviderError(
                tr(
                    "콘솔에서 사용량을 읽지 못했습니다 · 로그 확인",
                    "Could not read usage from the console · see log",
                ),
                ErrorKind.DATA,
            ) from exc
        except api.FetchError as exc:
            if "404" in str(exc):
                # The workspace was removed or renamed: rediscover next cycle.
                self.workspace_id = None
            raise ProviderError(str(exc), ErrorKind.DATA) from exc
        return Loaded(console=console, monotonic=time.monotonic())

    def render(self, data: Loaded) -> Snapshot:
        sections = [self._go_section(data), self._zen_section(data.console.zen)]
        return Snapshot(
            sections=sections,
            gauge_percent=self._gauge_percent(data.console),
        )

    def _remaining_seconds(self, window: Optional[dict], loaded_at: float) -> Optional[float]:
        seconds = api.reset_in_seconds(window)
        if seconds is None:
            return None
        return max(0.0, seconds - (time.monotonic() - loaded_at))

    def _go_section(self, data: Loaded) -> Section:
        go = data.console.go
        url = api.go_page(self.workspace_id) if self.workspace_id else api.CONSOLE_BASE
        if go is None:
            return Section(
                title=tr("Go 플랜 사용량", "Go plan usage"),
                url=url,
                note=tr(
                    "Go 플랜 사용량 가져오기 실패. 구독 상태를 확인하세요",
                    "Could not fetch the Go plan usage. Check your subscription",
                ),
            )
        limits = self.limits
        metrics: list[Metric] = []
        for korean, english, key in PERIODS:
            window = getattr(go, key, None)
            label = tr(korean, english)
            percent = api.usage_percent(window)
            if percent is None:
                status = (window or {}).get("status") if isinstance(window, dict) else None
                metrics.append(
                    Metric(label=label, value=str(status or tr("없음", "n/a")), muted=True)
                )
                continue
            limit = limits.get(key, 0.0)
            used = percent / 100.0 * limit
            detail = None
            remaining = self._remaining_seconds(window, data.monotonic)
            if remaining is not None:
                span = fmt.duration(remaining)
                detail = tr(f"{span} 후 초기화", f"resets in {span}")
            status = window.get("status") if isinstance(window, dict) else None
            if status not in (None, "ok"):
                detail = f"{detail} · {status}" if detail else str(status)
            metrics.append(
                Metric(
                    label=label,
                    value=f"{fmt.money(used)} / {fmt.money_compact(limit)}",
                    percent=percent,
                    detail=detail,
                )
            )
        note = None
        if go.use_balance:
            note = tr(
                "이 워크스페이스는 Zen 크레딧으로 결제합니다",
                "This workspace bills against Zen credits",
            )
        return Section(
            title=tr("Go 플랜 사용량", "Go plan usage"),
            metrics=metrics,
            url=url,
            note=note,
        )

    def _zen_section(self, zen: Optional[api.ZenBilling]) -> Section:
        url = api.zen_page(self.workspace_id) if self.workspace_id else api.CONSOLE_BASE
        title = tr("Zen 크레딧", "Zen credits")
        if zen is None:
            return Section(
                title=title,
                url=url,
                note=tr("크레딧 정보 가져오기 실패", "Could not fetch the credit info"),
            )
        metrics = [
            Metric(
                label=tr("잔액", "Balance"),
                value=fmt.money(zen.balance) if zen.balance is not None else tr("없음", "n/a"),
                muted=zen.balance is None,
            )
        ]
        if zen.monthly_usage is not None:
            limit = zen.monthly_limit
            percent = None
            value = fmt.money(zen.monthly_usage)
            if limit:
                percent = min(100.0, zen.monthly_usage / limit * 100.0)
                value = f"{value} / {fmt.money_compact(limit)}"
            metrics.append(
                Metric(label=tr("이번 달 사용", "This month"), value=value, percent=percent)
            )
        if zen.reload_enabled and zen.reload_amount and zen.reload_trigger is not None:
            metrics.append(
                Metric(
                    label=tr("자동 충전", "Auto-reload"),
                    value=fmt.money_compact(zen.reload_amount),
                    detail=tr(
                        f"{fmt.money_compact(zen.reload_trigger)} 미만일 때",
                        f"when below {fmt.money_compact(zen.reload_trigger)}",
                    ),
                )
            )
        elif zen.balance is not None:
            metrics.append(
                Metric(
                    label=tr("자동 충전", "Auto-reload"),
                    value=tr("꺼짐", "Off"),
                    muted=True,
                )
            )
        return Section(title=title, metrics=metrics, url=url)

    def _gauge_percent(self, console: api.ConsoleData) -> Optional[float]:
        percents = [
            api.usage_percent(getattr(console.go, key, None))
            for _ko, _en, key in PERIODS
            if console.go
        ]
        values = [value for value in percents if value is not None]
        return max(values) if values else None

    # ------------------------------------------------------------------ menu

    def menu(self) -> list[MenuEntry]:
        workspace = self.workspace_id
        entries = [
            MenuEntry(
                label=tr("Go 사용량 페이지 열기", "Open Go usage page"),
                run=lambda: self.ui.open_url(
                    api.go_page(workspace) if workspace else api.CONSOLE_BASE
                ),
                background=False,
            ),
            MenuEntry(
                label=tr("Zen 크레딧 페이지 열기", "Open Zen credits page"),
                run=lambda: self.ui.open_url(
                    api.zen_page(workspace) if workspace else api.CONSOLE_BASE
                ),
                background=False,
            ),
            MenuEntry(
                label=tr("통계 페이지 열기", "Open stats page"),
                run=lambda: self.ui.open_url(
                    api.stats_page(workspace) if workspace else api.CONSOLE_BASE
                ),
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
                    label=tr("클립보드에서 세션 키 붙여넣기", "Paste session key from clipboard"),
                    run=self._paste_session_key,
                    background=False,
                )
            )
            entries.append(self.primary_action())
        return entries

    def primary_action(self) -> MenuEntry:
        return MenuEntry(
            label=tr("세션 키 입력...", "Enter session key..."),
            run=self._enter_session_key,
            background=False,
        )

    # --------------------------------------------------------------- session

    def _paste_session_key(self) -> None:
        """GUI thread: read the clipboard and adopt it as the session key."""
        key = auth.clean_session_key(self.ui.clipboard_text())
        if not key:
            self.set_message(
                tr(
                    "클립보드가 세션 키 형식이 아닙니다",
                    "The clipboard does not look like a session key",
                )
            )
            return
        self._adopt_session_key(key)

    def _enter_session_key(self) -> None:
        """GUI thread: ask for the session key in a modal prompt."""
        value = self.ui.ask_text(
            tr("OpenCode 세션 키", "OpenCode session key"),
            tr("'auth' 쿠키 값을 붙여넣으세요:", "Paste your 'auth' cookie value:")
            + "\n\n"
            + auth.instructions(),
            secret=True,
        )
        if value is None:
            return
        key = auth.clean_session_key(value)
        if not key:
            self.set_message(tr("잘못된 세션 키입니다", "That is not a valid session key"))
            return
        self._adopt_session_key(key)

    def _adopt_session_key(self, key: str) -> None:
        try:
            auth.save_session_key(key)
        except auth.AuthError as exc:
            self.set_message(str(exc))
            return
        self._session_key = key
        self._session_key_loaded = True
        self.set_message(tr("세션 키를 저장했습니다", "Session key saved"))
        self.ui.request_refresh(self)

    def _sign_out(self) -> None:
        try:
            auth.delete_session_key()
        except auth.AuthError as exc:
            self.set_message(str(exc))
            return
        self._session_key = None
        self._session_key_loaded = True
        self.workspace_id = None
        self.reset(tr("로그아웃됨", "Signed out"))

    def on_auth_error(self) -> None:
        try:
            auth.delete_session_key()
        except auth.AuthError:
            logger.warning("Could not remove the rejected session key", exc_info=True)
        self._session_key = None
        self._session_key_loaded = True
        self.ui.notify(
            tr(
                "OpenCode 세션이 만료되었습니다. 세션 키를 다시 입력하세요.",
                "The OpenCode session expired. Add your session key again.",
            )
        )
