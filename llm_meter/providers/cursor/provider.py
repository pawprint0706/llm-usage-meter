"""Cursor provider: plan usage percentages and cycle spend."""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

import requests

from ... import format as fmt
from ...i18n import tr
from ..base import ErrorKind, MenuEntry, Metric, Provider, ProviderError, Section, Snapshot
from . import api, auth

logger = logging.getLogger(__name__)


@dataclass
class Loaded:
    usage: api.UsageData
    monotonic: float


class CursorProvider(Provider):
    id = "cursor"
    name = "Cursor"
    accent = "#F54E00"

    def __init__(self, cfg, ui):
        super().__init__(cfg, ui)
        self._session: Optional[auth.Session] = None
        self._session_loaded = False

    # ------------------------------------------------------------------ auth

    @property
    def session(self) -> Optional[auth.Session]:
        if not self._session_loaded:
            try:
                self._session = auth.resolve_session()
            except auth.AuthError as exc:
                logger.warning("Cursor session unreadable: %s", exc)
                self._session = None
            self._session_loaded = True
        return self._session

    def _invalidate_session_cache(self) -> None:
        self._session = None
        self._session_loaded = False

    def is_authenticated(self) -> bool:
        return self.session is not None

    def signed_out_hint(self) -> str:
        return tr(
            "Cursor에 로그인하거나 세션 토큰을 입력하세요",
            "Sign in to Cursor or add a session token",
        )

    # ------------------------------------------------------------------ data

    def load(self) -> Loaded:
        session = self.session
        if session is None:
            raise ProviderError(tr("세션이 없습니다", "No session"), ErrorKind.AUTH)
        try:
            usage = api.fetch_usage(session)
        except api.AuthExpiredError as exc:
            raise ProviderError(
                tr(
                    "세션이 만료되었습니다 · Cursor에 다시 로그인하거나 토큰을 붙여넣으세요",
                    "Session expired · sign in to Cursor again or paste a new token",
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
                tr("사용량 응답을 읽지 못했습니다", "Could not read the usage response"),
                ErrorKind.DATA,
            ) from exc
        return Loaded(usage=usage, monotonic=time.monotonic())

    def render(self, data: Loaded) -> Snapshot:
        usage = data.usage
        badge = usage.plan_name or (
            usage.membership_type.upper() if usage.membership_type else None
        )
        return Snapshot(
            sections=[
                self._usage_section(data),
                self._spend_section(usage),
            ],
            badge=badge,
            gauge_percent=usage.gauge_percent,
        )

    def _cycle_remaining(self, data: Loaded) -> Optional[float]:
        end = data.usage.billing_cycle_end
        if end is None:
            return None
        return max(0.0, (end - datetime.now(timezone.utc)).total_seconds())

    def _usage_section(self, data: Loaded) -> Section:
        usage = data.usage
        plan = usage.plan
        metrics: list[Metric] = []
        cycle_detail = None
        remaining = self._cycle_remaining(data)
        if remaining is not None:
            cycle_detail = tr(
                f"{fmt.duration(remaining)} 후 초기화",
                f"resets in {fmt.duration(remaining)}",
            )

        if usage.is_unlimited:
            metrics.append(
                Metric(
                    label=tr("전체", "Total"),
                    value=tr("무제한", "Unlimited"),
                    detail=cycle_detail,
                )
            )
        else:
            total = plan.total_percent
            metrics.append(
                Metric(
                    label=tr("전체", "Total"),
                    value=fmt.percent(total) if total is not None else tr("없음", "n/a"),
                    percent=total,
                    detail=cycle_detail,
                    muted=total is None,
                )
            )
            if plan.auto_percent is not None:
                metrics.append(
                    Metric(
                        label=tr("자동", "Auto"),
                        value=fmt.percent(plan.auto_percent),
                        percent=plan.auto_percent,
                    )
                )
            if plan.api_percent is not None:
                metrics.append(
                    Metric(
                        label=tr("API", "API"),
                        value=fmt.percent(plan.api_percent),
                        percent=plan.api_percent,
                    )
                )

        return Section(
            title=tr("플랜 사용량", "Plan usage"),
            metrics=metrics,
            url=api.USAGE_PAGE,
        )

    def _money_pair(self, used_cents: Optional[float], limit_cents: Optional[float]) -> str:
        used = api.cents_to_dollars(used_cents)
        limit = api.cents_to_dollars(limit_cents)
        if used is None and limit is None:
            return tr("없음", "n/a")
        if limit is None:
            return fmt.money(used or 0.0)
        return f"{fmt.money(used or 0.0)} / {fmt.money_compact(limit)}"

    def _spend_section(self, usage: api.UsageData) -> Section:
        plan = usage.plan
        metrics: list[Metric] = []

        included = plan.included_cents
        if included is None:
            included = plan.used_cents
        limit = plan.limit_cents
        if included is not None or limit is not None:
            percent = None
            if included is not None and limit and limit > 0:
                percent = min(100.0, included / limit * 100.0)
            metrics.append(
                Metric(
                    label=tr("포함", "Included"),
                    value=self._money_pair(included, limit),
                    percent=percent,
                )
            )

        if plan.bonus_cents and plan.bonus_cents > 0:
            metrics.append(
                Metric(
                    label=tr("보너스", "Bonus"),
                    value=fmt.money(api.cents_to_dollars(plan.bonus_cents) or 0.0),
                )
            )

        on_demand = usage.on_demand
        overage_off = usage.no_usage_based_allowed is True or not on_demand.enabled
        if overage_off:
            metrics.append(
                Metric(
                    label=tr("온디맨드", "On-demand"),
                    value=tr("꺼짐", "Off"),
                    muted=True,
                )
            )
        else:
            used = on_demand.used_cents
            limit = on_demand.limit_cents
            percent = None
            if used is not None and limit and limit > 0:
                percent = min(100.0, used / limit * 100.0)
            metrics.append(
                Metric(
                    label=tr("온디맨드", "On-demand"),
                    value=self._money_pair(used, limit),
                    percent=percent,
                )
            )

        if usage.plan_price:
            metrics.append(
                Metric(
                    label=tr("요금제", "Plan"),
                    value=usage.plan_price,
                    muted=True,
                )
            )

        return Section(
            title=tr("지출", "Spending"),
            metrics=metrics or [
                Metric(label=tr("지출", "Spend"), value=tr("없음", "n/a"), muted=True)
            ],
            url=api.SPENDING_PAGE,
        )

    # ------------------------------------------------------------------ menu

    def menu(self) -> list[MenuEntry]:
        entries = [
            MenuEntry(
                label=tr("사용량 페이지 열기", "Open usage page"),
                run=lambda: self.ui.open_url(api.USAGE_PAGE),
                background=False,
            ),
            MenuEntry(
                label=tr("지출 페이지 열기", "Open spending page"),
                run=lambda: self.ui.open_url(api.SPENDING_PAGE),
                background=False,
            ),
            MenuEntry.sep(),
        ]
        session = self.session
        if session is not None and session.source == "keystore":
            entries.append(
                MenuEntry(label=tr("로그아웃", "Sign out"), run=self._sign_out, background=False)
            )
        elif session is None:
            entries.append(
                MenuEntry(
                    label=tr(
                        "클립보드에서 세션 토큰 붙여넣기",
                        "Paste session token from clipboard",
                    ),
                    run=self._paste_session_token,
                    background=False,
                )
            )
            entries.append(self.primary_action())
        return entries

    def primary_action(self) -> MenuEntry:
        return MenuEntry(
            label=tr("세션 토큰 입력...", "Enter session token..."),
            run=self._enter_session_token,
            background=False,
        )

    # --------------------------------------------------------------- session

    def _paste_session_token(self) -> None:
        token = auth.clean_session_token(self.ui.clipboard_text())
        if not token:
            self.set_message(
                tr(
                    "클립보드가 세션 토큰 형식이 아닙니다",
                    "The clipboard does not look like a session token",
                )
            )
            return
        self._adopt_session_token(token)

    def _enter_session_token(self) -> None:
        value = self.ui.ask_text(
            tr("Cursor 세션 토큰", "Cursor session token"),
            tr(
                "'WorkosCursorSessionToken' 쿠키 값을 붙여넣으세요:",
                "Paste your 'WorkosCursorSessionToken' cookie value:",
            )
            + "\n\n"
            + auth.instructions(),
            secret=True,
        )
        if value is None:
            return
        token = auth.clean_session_token(value)
        if not token:
            self.set_message(tr("잘못된 세션 토큰입니다", "That is not a valid session token"))
            return
        self._adopt_session_token(token)

    def _adopt_session_token(self, token: str) -> None:
        try:
            auth.save_pasted_session_token(token)
        except auth.AuthError as exc:
            self.set_message(str(exc))
            return
        self._invalidate_session_cache()
        self.set_message(tr("세션 토큰을 저장했습니다", "Session token saved"))
        self.ui.request_refresh(self)

    def _sign_out(self) -> None:
        try:
            auth.delete_pasted_session_token()
        except auth.AuthError as exc:
            self.set_message(str(exc))
            return
        self._invalidate_session_cache()
        if self.is_authenticated():
            self.set_message(
                tr(
                    "붙여넣은 토큰을 제거했습니다 · 로컬 세션을 사용합니다",
                    "Removed the pasted token · using the local session",
                )
            )
            self.ui.request_refresh(self)
            return
        self.reset(tr("로그아웃됨", "Signed out"))

    def on_auth_error(self) -> None:
        session = self.session
        if session is not None and session.source == "keystore":
            try:
                auth.delete_pasted_session_token()
            except auth.AuthError:
                logger.warning("Could not remove the rejected session token", exc_info=True)
        self._invalidate_session_cache()
        self.ui.notify(
            tr(
                "Cursor 세션이 만료되었습니다. Cursor에 다시 로그인하거나 토큰을 붙여넣으세요.",
                "The Cursor session expired. Sign in to Cursor again or paste a new token.",
            )
        )
