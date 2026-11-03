"""Ollama Cloud balances and rolling usage history via the documented APIs."""

import logging
import math
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

import requests

logger = logging.getLogger(__name__)

SETTINGS_PAGE = "https://ollama.com/settings"
BALANCE_URL = "https://ollama.com/api/balance"
USAGE_URL = "https://ollama.com/api/usage"


class ApiError(Exception):
    """Base class for account API errors."""


class AuthExpiredError(ApiError):
    """The API key is missing, invalid or revoked."""


class ScopeError(ApiError):
    """The account cannot access the endpoint (HTTP 403)."""


class FetchError(ApiError):
    """The endpoint returned an unexpected HTTP response."""


class RateLimitError(FetchError):
    def __init__(self, retry_after: Optional[int]):
        self.retry_after = retry_after
        detail = f"; retry after {retry_after}s" if retry_after is not None else ""
        super().__init__(f"Ollama API rate limit (HTTP 429){detail}")


class ParseError(ApiError):
    """The API response does not match the documented contract."""


@dataclass
class UsageWindow:
    label: str
    percent: Optional[float]  # Used percentage; None when no allowance exists.
    reset_at: datetime
    balance: Optional[float] = None
    total: Optional[float] = None


@dataclass
class BalanceData:
    windows: list[UsageWindow]
    purchased: float
    included_error: Optional[str] = None


@dataclass
class ModelUsage:
    name: str
    requests: int


@dataclass
class UsageHistory:
    requests: int
    models: list[ModelUsage]
    other_requests: int = 0


@dataclass
class CloudData:
    balance: BalanceData
    history: Optional[UsageHistory] = None
    history_error: Optional[str] = None


def _object(value: object, name: str) -> dict:
    if not isinstance(value, dict):
        raise ParseError(f"missing Ollama {name} object")
    return value


def _number(value: object, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ParseError(f"invalid Ollama {name}")
    try:
        number = float(value)
    except OverflowError as exc:
        raise ParseError(f"invalid Ollama {name}") from exc
    if not math.isfinite(number):
        raise ParseError(f"invalid Ollama {name}")
    return number


def _timestamp(value: object, name: str) -> datetime:
    if not isinstance(value, str):
        raise ParseError(f"missing Ollama {name} timestamp")
    try:
        instant = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ParseError(f"invalid Ollama {name} timestamp") from exc
    if instant.tzinfo is None or instant.utcoffset() is None:
        raise ParseError(f"Ollama {name} timestamp has no timezone")
    return instant


def parse_balance(payload: object) -> BalanceData:
    payload = _object(payload, "balance")
    purchased = _object(payload.get("purchased"), "purchased")
    data = BalanceData([], _number(purchased.get("balance_usd"), "purchased balance"))
    try:
        data.windows = _parse_included(payload.get("included"))
    except ParseError as exc:
        # Unknown/absent included allowances are not zero usage. Keep the
        # independently validated purchased balance and expose the failure.
        data.included_error = str(exc)
        logger.warning("Could not read Ollama included balance: %s", exc)
    return data


def _parse_included(value: object) -> list[UsageWindow]:
    included = _object(value, "included")
    windows = []
    if "session" in included or "weekly" in included:
        # The documented legacy contract has both windows; it reports remaining,
        # whereas the shared UI contract expects used percentages.
        for label in ("session", "weekly"):
            limit = _object(included.get(label), label)
            remaining = _number(limit.get("remaining_percent"), f"{label} remaining percent")
            if not 0 <= remaining <= 100:
                raise ParseError(f"invalid Ollama {label} remaining percent")
            windows.append(UsageWindow(
                label=label,
                percent=100.0 - remaining,
                reset_at=_timestamp(limit.get("resets_at"), label),
            ))
    else:
        balance = _number(included.get("balance_usd"), "included balance")
        allowance = _number(included.get("allowance_usd"), "included allowance")
        if allowance < 0:
            raise ParseError("negative Ollama included allowance")
        period = _object(included.get("period"), "included period")
        start = _timestamp(period.get("from"), "period start")
        reset = _timestamp(period.get("until"), "period end")
        if reset <= start:
            raise ParseError("invalid Ollama included period")
        percent = min(100.0, max(0.0, (1.0 - balance / allowance) * 100)) if allowance else None
        windows.append(UsageWindow("monthly", percent, reset, balance, allowance))
    return windows


def _request_count(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ParseError("invalid Ollama request count")
    return value


def parse_history(payload: object) -> UsageHistory:
    payload = _object(payload, "usage history")
    if payload.get("range") != "30d":
        raise ParseError("unexpected Ollama usage history range")
    totals = _object(payload.get("totals"), "usage totals")
    groups = totals.get("groups")
    if not isinstance(groups, list):
        raise ParseError("missing Ollama model groups")
    models = []
    for group in groups:
        group = _object(group, "model group")
        name = group.get("model")
        if not isinstance(name, str) or not name.strip():
            raise ParseError("missing Ollama model name")
        models.append(ModelUsage(name=name, requests=_request_count(group.get("request_count"))))
    other = 0
    if "remainder" in totals:
        other = _request_count(_object(totals["remainder"], "remainder").get("request_count"))
    return UsageHistory(_request_count(totals.get("request_count")), models, other)


def _get_json(session: requests.Session, url: str, params: Optional[dict] = None) -> object:
    response = session.get(url, params=params, timeout=15, allow_redirects=False)
    if response.status_code == 401:
        raise AuthExpiredError("Ollama API key rejected (HTTP 401)")
    if response.status_code == 403:
        raise ScopeError("Ollama account access denied (HTTP 403)")
    if response.status_code == 429:
        try:
            seconds = int(response.headers.get("Retry-After", ""))
        except ValueError:
            seconds = 0
        raise RateLimitError(seconds if seconds > 0 else None)
    if response.status_code != 200:
        raise FetchError(f"Ollama API returned HTTP {response.status_code}")
    try:
        return response.json()
    except ValueError as exc:
        raise ParseError("Ollama API returned invalid JSON") from exc


def fetch_cloud(api_key: str) -> CloudData:
    if not api_key:
        raise AuthExpiredError("no API key")
    with requests.Session() as session:
        session.headers.update({
            "Authorization": f"Bearer {api_key}",
            "Accept": "application/json",
            "User-Agent": "llm-usage-meter",
        })
        data = CloudData(balance=parse_balance(_get_json(session, BALANCE_URL)))
        try:
            data.history = parse_history(_get_json(session, USAGE_URL, {
                "range": "30d", "group_by": "model", "scope": "self",
            }))
        except AuthExpiredError:
            raise
        except (FetchError, ParseError, ScopeError, requests.RequestException) as exc:
            # A temporarily unavailable history must not hide current balances.
            data.history_error = str(exc)
            logger.warning("Could not fetch Ollama usage history: %s", exc)
        return data
