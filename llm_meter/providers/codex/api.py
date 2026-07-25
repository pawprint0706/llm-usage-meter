"""ChatGPT Codex usage, credit balance and rate-limit reset-credit client.

Two undocumented ChatGPT endpoints back this module:

    GET /backend-api/wham/usage                      rate-limit windows + credits
    GET /backend-api/wham/rate-limit-reset-credits    banked reset credits

The ``credits`` object inside the usage payload carries the purchased Codex
credit balance, so no separate balance endpoint is needed.
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional

import requests

from . import auth

BASE_URL = "https://chatgpt.com/backend-api"
USAGE_URL = f"{BASE_URL}/wham/usage"
RESET_CREDITS_URL = f"{BASE_URL}/wham/rate-limit-reset-credits"

USAGE_PAGE = "https://chatgpt.com/#settings/Usage"
ANALYTICS_PAGE = "https://chatgpt.com/codex/cloud/settings/analytics"

# The reset-credit endpoint is served to the Codex desktop client only.
_CODEX_CLIENT_HEADERS = {"OpenAI-Beta": "codex-1", "originator": "Codex Desktop"}

SESSION_WINDOW_MAX_SECONDS = 6 * 60 * 60
DAILY_WINDOW_MAX_SECONDS = 24 * 60 * 60


class ApiError(Exception):
    """Base API error."""


class UnauthorizedError(ApiError):
    """The access token was rejected."""


class ResponseError(ApiError):
    """The server response was not usable."""


@dataclass
class UsageWindow:
    used_percent: float
    reset_at: datetime
    window_seconds: int

    @property
    def remaining_percent(self) -> float:
        return max(0.0, min(100.0, 100.0 - self.used_percent))


@dataclass
class ResetCredit:
    id: str
    reset_type: str
    status: str
    title: Optional[str]
    description: Optional[str]
    expires_at: Optional[datetime]


@dataclass
class CreditBalance:
    """Purchased Codex credits (usage beyond the plan's included quota)."""

    balance: float = 0.0
    has_credits: bool = False
    unlimited: bool = False
    overage_limit_reached: bool = False
    approx_local_messages: Optional[tuple[int, int]] = None
    approx_cloud_messages: Optional[tuple[int, int]] = None


@dataclass
class UsageData:
    windows: list[UsageWindow] = field(default_factory=list)
    plan_type: Optional[str] = None
    credits: CreditBalance = field(default_factory=CreditBalance)
    reset_credits: list[ResetCredit] = field(default_factory=list)
    available_reset_count: int = 0
    reset_credits_error: Optional[str] = None

    @property
    def primary_window(self) -> Optional[UsageWindow]:
        """The longest window, i.e. the one that usually gates a plan."""
        return max(self.windows, key=lambda w: w.window_seconds) if self.windows else None


def _parse_timestamp(value: Any) -> Optional[datetime]:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(value, tz=timezone.utc)
    if isinstance(value, str):
        text = value.strip().replace("Z", "+00:00")
        try:
            parsed = datetime.fromisoformat(text)
        except ValueError:
            return None
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    return None


def _number(value: Any) -> Optional[float]:
    """Accept both numbers and the decimal strings the credits object uses."""
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value.strip().lstrip("$").replace(",", ""))
        except ValueError:
            return None
    return None


def _parse_window(value: Any) -> Optional[UsageWindow]:
    if not isinstance(value, dict):
        return None
    used = _number(value.get("used_percent"))
    reset_at = _parse_timestamp(value.get("reset_at"))
    seconds = _number(value.get("limit_window_seconds"))
    if used is None or reset_at is None or seconds is None:
        return None
    return UsageWindow(used, reset_at, int(seconds))


def _parse_message_range(value: Any) -> Optional[tuple[int, int]]:
    if not isinstance(value, list) or len(value) != 2:
        return None
    low, high = _number(value[0]), _number(value[1])
    if low is None or high is None or (low <= 0 and high <= 0):
        return None
    return int(low), int(high)


def parse_credits(value: Any) -> CreditBalance:
    if not isinstance(value, dict):
        return CreditBalance()
    return CreditBalance(
        balance=_number(value.get("balance")) or 0.0,
        has_credits=bool(value.get("has_credits")),
        unlimited=bool(value.get("unlimited")),
        overage_limit_reached=bool(value.get("overage_limit_reached")),
        approx_local_messages=_parse_message_range(value.get("approx_local_messages")),
        approx_cloud_messages=_parse_message_range(value.get("approx_cloud_messages")),
    )


def parse_usage(data: Any) -> UsageData:
    if not isinstance(data, dict):
        raise ResponseError("Usage response is not an object.")
    rate_limit = data.get("rate_limit")
    if not isinstance(rate_limit, dict):
        raise ResponseError("Usage response has no rate_limit object.")
    windows = [
        window
        for window in (
            _parse_window(rate_limit.get("primary_window")),
            _parse_window(rate_limit.get("secondary_window")),
        )
        if window
    ]
    if not windows:
        raise ResponseError("Usage response has no valid rate-limit window.")
    windows.sort(key=lambda window: window.window_seconds)
    plan_type = data.get("plan_type")
    return UsageData(
        windows=windows,
        plan_type=plan_type if isinstance(plan_type, str) else None,
        credits=parse_credits(data.get("credits")),
    )


def parse_reset_credits(data: Any) -> tuple[list[ResetCredit], int]:
    if not isinstance(data, dict):
        raise ResponseError("Reset-credit response is not an object.")
    raw_credits = data.get("credits") or []
    if not isinstance(raw_credits, list):
        raise ResponseError("Reset-credit list is invalid.")
    credits = []
    for value in raw_credits:
        if not isinstance(value, dict):
            continue
        credit_id = value.get("id")
        status = value.get("status")
        if not isinstance(credit_id, str) or not isinstance(status, str):
            continue
        credits.append(
            ResetCredit(
                id=credit_id,
                reset_type=str(value.get("reset_type") or "reset"),
                status=status,
                title=value.get("title") if isinstance(value.get("title"), str) else None,
                description=(
                    value.get("description")
                    if isinstance(value.get("description"), str)
                    else None
                ),
                expires_at=_parse_timestamp(value.get("expires_at")),
            )
        )
    default_count = sum(credit.status.lower() == "available" for credit in credits)
    count = _number(data.get("available_count"))
    return credits, max(0, int(count if count is not None else default_count))


def _get(
    url: str,
    credentials: auth.Credentials,
    extra_headers: Optional[dict[str, str]] = None,
    session: Optional[requests.Session] = None,
) -> Any:
    headers = {
        "Authorization": f"Bearer {credentials.access_token}",
        "Accept": "application/json",
        "User-Agent": "LlmUsageMeter/1.0",
    }
    if credentials.account_id:
        headers["ChatGPT-Account-Id"] = credentials.account_id
    if extra_headers:
        headers.update(extra_headers)
    client = session or requests
    response = client.get(url, headers=headers, timeout=30)
    if response.status_code in (401, 403):
        raise UnauthorizedError(f"HTTP {response.status_code}")
    if not 200 <= response.status_code < 300:
        raise ResponseError(f"HTTP {response.status_code}")
    try:
        return response.json()
    except ValueError as exc:
        raise ResponseError("The API returned invalid JSON.") from exc


def fetch_usage(
    credentials: auth.Credentials, session: Optional[requests.Session] = None
) -> UsageData:
    result = parse_usage(_get(USAGE_URL, credentials, session=session))
    try:
        payload = _get(
            RESET_CREDITS_URL,
            credentials,
            extra_headers=_CODEX_CLIENT_HEADERS,
            session=session,
        )
        result.reset_credits, result.available_reset_count = parse_reset_credits(payload)
    except UnauthorizedError:
        raise
    except (ResponseError, requests.RequestException) as exc:
        result.reset_credits_error = str(exc)
    return result


def fetch_with_refresh(session: Optional[requests.Session] = None) -> UsageData:
    """Fetch usage, retrying once with a force-refreshed access token on 401/403."""
    credentials = auth.valid_credentials()
    if credentials is None:
        raise UnauthorizedError("Not signed in.")
    try:
        return fetch_usage(credentials, session=session)
    except UnauthorizedError:
        credentials = auth.valid_credentials(force_refresh=True)
        if credentials is None:
            raise
        return fetch_usage(credentials, session=session)
