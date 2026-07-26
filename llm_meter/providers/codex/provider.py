"""Codex provider: plan usage windows, credit balance and reset credits."""

import logging
from datetime import datetime
from typing import Optional

import requests

from ... import format as fmt
from ...i18n import tr
from ..base import ErrorKind, MenuEntry, Metric, Provider, ProviderError, Section, Snapshot
from . import api, auth

logger = logging.getLogger(__name__)

_WINDOW_LABELS = {
    5 * 3600: ("5시간", "5h"),
    24 * 3600: ("일간", "Daily"),
    7 * 24 * 3600: ("주간", "Weekly"),
    30 * 24 * 3600: ("월간", "Monthly"),
}


def _window_label(window_seconds: int) -> str:
    labels = _WINDOW_LABELS.get(window_seconds)
    if labels:
        return tr(*labels)
    return fmt.duration(window_seconds)


def _reset_status(status: str) -> str:
    labels = {
        "available": tr("사용 가능", "Available"),
        "used": tr("사용됨", "Used"),
        "redeemed": tr("사용됨", "Redeemed"),
        "expired": tr("만료됨", "Expired"),
    }
    return labels.get(status.lower(), status.replace("_", " ").title())


def _reset_credit_detail(credit: api.ResetCredit, now: Optional[datetime] = None) -> str:
    parts = [_reset_status(credit.status)]
    if credit.expires_at:
        current = now or datetime.now(credit.expires_at.tzinfo)
        remaining = (credit.expires_at - current).total_seconds()
        when = fmt.timestamp(credit.expires_at)
        if remaining > 0:
            parts.append(
                tr(
                    f"{when} 만료 ({fmt.duration(remaining)} 후)",
                    f"expires {when} (in {fmt.duration(remaining)})",
                )
            )
        else:
            parts.append(tr(f"{when} 만료됨", f"expired {when}"))
    return " · ".join(parts)


class CodexProvider(Provider):
    id = "codex"
    name = "Codex"
    accent = "#19C37D"

    def __init__(self, cfg, ui):
        super().__init__(cfg, ui)
        self._login_in_progress = False
        self.device_code: Optional[str] = None
        self._credentials: Optional[auth.Credentials] = None
        self._credentials_loaded = False

    # ------------------------------------------------------------------ auth

    def _load_credentials(self) -> Optional[auth.Credentials]:
        if not self._credentials_loaded:
            try:
                self._credentials = auth.load_credentials()
            except auth.AuthError as exc:
                logger.warning("Codex credential check failed: %s", exc)
                self._credentials = None
            self._credentials_loaded = True
        return self._credentials

    def _invalidate_credentials_cache(self) -> None:
        self._credentials = None
        self._credentials_loaded = False

    def is_authenticated(self) -> bool:
        return self._load_credentials() is not None

    def signed_out_hint(self) -> str:
        return tr("OpenAI 계정으로 로그인하세요", "Sign in with your OpenAI account")

    # ------------------------------------------------------------------ data

    def load(self) -> api.UsageData:
        try:
            return api.fetch_with_refresh()
        except (auth.AuthError, api.UnauthorizedError) as exc:
            raise ProviderError(
                tr("로그인이 만료되었습니다", "Sign-in expired"), ErrorKind.AUTH
            ) from exc
        except requests.RequestException as exc:
            raise ProviderError(
                tr("네트워크 오류 · 재시도 예정", "Network error · will retry"), ErrorKind.NETWORK
            ) from exc
        except api.ResponseError as exc:
            raise ProviderError(
                tr("사용량 응답을 읽지 못했습니다", "Could not read the usage response"),
                ErrorKind.DATA,
            ) from exc

    def render(self, data: api.UsageData) -> Snapshot:
        sections = [
            self._usage_section(data),
            self._credits_section(data.credits),
            self._reset_section(data),
        ]
        return Snapshot(
            sections=sections,
            badge=data.plan_type,
            gauge_percent=(
                data.primary_window.used_percent if data.primary_window else None
            ),
        )

    def _usage_section(self, data: api.UsageData) -> Section:
        metrics = []
        for window in data.windows:
            remaining = (window.reset_at - datetime.now(window.reset_at.tzinfo)).total_seconds()
            metrics.append(
                Metric(
                    label=_window_label(window.window_seconds),
                    value=fmt.percent(window.used_percent),
                    percent=window.used_percent,
                    detail=tr(
                        f"{fmt.duration(remaining)} 후 초기화",
                        f"resets in {fmt.duration(remaining)}",
                    ),
                )
            )
        return Section(
            title=tr("플랜 사용량", "Plan usage"),
            metrics=metrics,
            url=api.USAGE_PAGE,
        )

    def _credits_section(self, credits: api.CreditBalance) -> Section:
        if credits.unlimited:
            value = tr("무제한", "Unlimited")
        else:
            value = fmt.money(credits.balance)
        detail = None
        if credits.has_credits:
            spans = []
            if credits.approx_local_messages:
                low, high = credits.approx_local_messages
                spans.append(tr(f"로컬 약 {low}~{high}회", f"~{low}-{high} local"))
            if credits.approx_cloud_messages:
                low, high = credits.approx_cloud_messages
                spans.append(tr(f"클라우드 약 {low}~{high}회", f"~{low}-{high} cloud"))
            detail = " · ".join(spans) or None
        metrics = [Metric(label=tr("잔액", "Balance"), value=value, detail=detail)]
        if not credits.has_credits and not credits.unlimited:
            metrics[0].muted = True
        note = None
        if credits.overage_limit_reached:
            note = tr("초과 사용 한도에 도달했습니다", "Overage limit reached")
        return Section(
            title=tr("크레딧", "Credits"),
            metrics=metrics,
            url=api.USAGE_PAGE,
            note=note,
        )

    def _reset_section(self, data: api.UsageData) -> Section:
        if data.reset_credits_error:
            return Section(
                title=tr("사용량 한도 재설정", "Usage limit resets"),
                note=tr("조회하지 못했습니다", "Could not load"),
            )
        available = [
            credit for credit in data.reset_credits if credit.status.lower() == "available"
        ]
        metrics = [
            Metric(label=credit.title or _reset_status(credit.status),
                   value="",
                   detail=_reset_credit_detail(credit))
            for credit in (available or data.reset_credits)
        ]
        count = data.available_reset_count
        return Section(
            title=tr(f"사용량 한도 재설정 {count}개", f"Usage limit resets: {count}"),
            metrics=metrics,
            empty_text=tr("보유한 재설정 없음", "No reset credits"),
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
                label=tr("통계 페이지 열기", "Open analytics page"),
                run=lambda: self.ui.open_url(api.ANALYTICS_PAGE),
                background=False,
            ),
            MenuEntry.sep(),
        ]
        if self.is_authenticated():
            entries.append(
                MenuEntry(label=tr("로그아웃", "Sign out"), run=self._sign_out, background=False)
            )
        else:
            entries.append(self.primary_action())
        return entries

    def primary_action(self) -> MenuEntry:
        return MenuEntry(
            label=tr("OpenAI로 로그인...", "Sign in with OpenAI..."),
            run=self._login,
            enabled=not self._login_in_progress,
        )

    def _sign_out(self) -> None:
        try:
            auth.delete_credentials()
        except auth.AuthError as exc:
            self.set_message(str(exc))
            return
        self._invalidate_credentials_cache()
        self.device_code = None
        self.reset(tr("로그아웃됨", "Signed out"))

    def _login(self) -> None:
        """Device-code login. Runs on a worker thread."""
        if self._login_in_progress:
            return
        self._login_in_progress = True
        self.set_message(tr("로그인 준비 중...", "Preparing sign-in..."))
        try:
            auth.login(self._on_device_code, should_continue=lambda: self.alive)
        except auth.LoginCancelled:
            return
        except (auth.AuthError, requests.RequestException) as exc:
            logger.warning("Codex sign-in failed: %s", exc)
            self.set_message(tr(f"로그인 실패: {exc}", f"Sign-in failed: {exc}"))
            self.ui.notify(self.message)
            return
        finally:
            self._login_in_progress = False
            self.device_code = None
        self._invalidate_credentials_cache()
        self.set_message(tr("로그인됨", "Signed in"))
        self.ui.request_refresh(self)

    def _on_device_code(self, code: auth.DeviceCode) -> None:
        self.device_code = code.user_code
        self.ui.copy_to_clipboard(code.user_code)
        self.set_message(
            tr(
                f"브라우저에 코드 {code.user_code} 입력 (클립보드에 복사됨)",
                f"Enter code {code.user_code} in the browser (copied)",
            )
        )
        self.ui.notify(self.message)
