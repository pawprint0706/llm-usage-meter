"""OpenRouter provider: prepaid credit balance as a single meter."""

import logging
from dataclasses import dataclass
from typing import Optional

import requests

from ... import format as fmt
from ...i18n import tr
from ..base import ErrorKind, MenuEntry, Metric, Provider, ProviderError, Section, Snapshot
from . import api, auth

logger = logging.getLogger(__name__)


@dataclass
class Loaded:
    credits: api.CreditsData


class OpenRouterProvider(Provider):
    id = "openrouter"
    name = "OpenRouter"
    accent = "#7624F4"

    def __init__(self, cfg, ui):
        super().__init__(cfg, ui)
        self._api_key: Optional[str] = None
        self._api_key_loaded = False

    # ------------------------------------------------------------------ auth

    @property
    def api_key(self) -> Optional[str]:
        if not self._api_key_loaded:
            try:
                self._api_key = auth.load_key()
            except auth.AuthError as exc:
                logger.warning("OpenRouter API key unreadable: %s", exc)
                self._api_key = None
            self._api_key_loaded = True
        return self._api_key

    def is_authenticated(self) -> bool:
        return bool(self.api_key)

    def signed_out_hint(self) -> str:
        return tr("API 키를 입력하세요", "Add your API key")

    # ------------------------------------------------------------------ data

    def load(self) -> Loaded:
        key = self.api_key
        if not key:
            raise ProviderError(tr("API 키가 없습니다", "No API key"), ErrorKind.AUTH)
        try:
            credits = api.fetch_credits(key)
        except api.AuthExpiredError as exc:
            raise ProviderError(
                tr(
                    "키가 거부되었습니다 · API 키를 다시 입력하세요",
                    "Key rejected · add your API key again",
                ),
                ErrorKind.AUTH,
            ) from exc
        except api.ScopeError as exc:
            # 403 means the key is valid but not allowed to read credits here;
            # keeping it lets the user swap in another key without re-pasting.
            raise ProviderError(
                tr(
                    "이 키로는 크레딧을 읽을 수 없습니다 · 다른 API 키를 사용하세요",
                    "This key cannot read credits · use a different API key",
                ),
                ErrorKind.DATA,
            ) from exc
        except requests.RequestException as exc:
            raise ProviderError(
                tr("네트워크 오류 · 재시도 예정", "Network error · will retry"),
                ErrorKind.NETWORK,
            ) from exc
        except (api.FetchError, api.ParseError) as exc:
            raise ProviderError(
                tr(
                    "크레딧 응답을 읽지 못했습니다 · 로그 확인",
                    "Could not read the credit response · see log",
                ),
                ErrorKind.DATA,
            ) from exc
        return Loaded(credits=credits)

    def render(self, data: Loaded) -> Snapshot:
        return Snapshot(
            sections=[self._credit_section(data.credits)],
            gauge_percent=data.credits.percent,
        )

    def _credit_section(self, credits: api.CreditsData) -> Section:
        left = max(0.0, credits.balance)
        total = fmt.money_compact(credits.total_credits)
        percent = credits.percent
        # The metric row already names itself "Credits", so the section carries
        # no title of its own.
        if percent is None:
            # No credit ever purchased: nothing to compare the meter against.
            metrics = [
                Metric(
                    label=tr("크레딧", "Credits"),
                    value=f"{fmt.money(left)} / {total}",
                    muted=True,
                )
            ]
        else:
            metrics = [
                Metric(
                    label=tr("크레딧", "Credits"),
                    value=f"{fmt.money_compact(left)} / {total}",
                    percent=percent,
                    muted=left <= 0,
                )
            ]
        return Section(title="", metrics=metrics, url=api.CREDITS_PAGE)

    # ------------------------------------------------------------------ menu

    def menu(self) -> list[MenuEntry]:
        entries = [
            MenuEntry(
                label=tr("크레딧 페이지 열기", "Open credits page"),
                run=lambda: self.ui.open_url(api.CREDITS_PAGE),
                background=False,
            ),
            MenuEntry(
                label=tr("활동 페이지 열기", "Open activity page"),
                run=lambda: self.ui.open_url(api.ACTIVITY_PAGE),
                background=False,
            ),
            MenuEntry(
                label=tr("로그 페이지 열기", "Open logs page"),
                run=lambda: self.ui.open_url(api.LOGS_PAGE),
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
                    label=tr(
                        "클립보드에서 API 키 붙여넣기",
                        "Paste API key from clipboard",
                    ),
                    run=self._paste_key,
                    background=False,
                )
            )
            entries.append(self.primary_action())
        return entries

    def primary_action(self) -> MenuEntry:
        return MenuEntry(
            label=tr("API 키 입력...", "Enter API key..."),
            run=self._enter_key,
            background=False,
        )

    # --------------------------------------------------------------- session

    def _paste_key(self) -> None:
        """GUI thread: read the clipboard and adopt it as the API key."""
        key = auth.clean_key(self.ui.clipboard_text())
        if not key:
            self.set_message(
                tr(
                    "클립보드가 API 키 형식이 아닙니다",
                    "The clipboard does not look like an API key",
                )
            )
            return
        self._adopt_key(key)

    def _enter_key(self) -> None:
        """GUI thread: ask for the API key in a modal prompt."""
        value = self.ui.ask_text(
            tr("OpenRouter API 키", "OpenRouter API key"),
            tr(
                "API 키(sk-or-...)를 붙여넣으세요:",
                "Paste your API key (sk-or-...):",
            )
            + "\n\n"
            + auth.instructions(),
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
        self.set_message(tr("API 키를 저장했습니다", "API key saved"))
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
        self.ui.notify(
            tr(
                "OpenRouter 키가 거부되었습니다. API 키를 다시 입력하세요.",
                "The OpenRouter key was rejected. Add your API key again.",
            )
        )
