"""OpenRouter credit balance client.

One official documented endpoint backs this module:

    GET https://openrouter.ai/api/v1/credits
    -> {"data": {"total_credits": 100.5, "total_usage": 25.75}}

The docs mark the endpoint "management key required", and the API does answer
403 "Only management keys can perform this operation" to some keys — but a
regular inference key (``sk-or-v1-...``) reads the account's own credits just
fine (verified live 2026-09). 403 therefore stays a separate :class:`ScopeError`
so a valid key is reported as a hint, not dropped.
"""

from dataclasses import dataclass
from typing import Any, Optional

import requests

CREDITS_URL = "https://openrouter.ai/api/v1/credits"
CREDITS_PAGE = "https://openrouter.ai/settings/credits"
ACTIVITY_PAGE = "https://openrouter.ai/activity"
LOGS_PAGE = "https://openrouter.ai/logs"


class ApiError(Exception):
    """Base API error."""


class AuthExpiredError(ApiError):
    """The API key was rejected."""


class ScopeError(ApiError):
    """The key is valid but not a management key."""


class FetchError(ApiError):
    """The server response was not usable."""


class ParseError(ApiError):
    """The payload shape was unexpected."""


@dataclass
class CreditsData:
    """Prepaid credit totals: balance is what was purchased minus what was used."""

    total_credits: float
    total_usage: float

    @property
    def balance(self) -> float:
        return self.total_credits - self.total_usage

    @property
    def percent(self) -> Optional[float]:
        """Spent share of every credit ever purchased; 100 means balance 0.

        None when there is nothing to compare against (no credit ever
        purchased), since the meter has no denominator.
        """
        if self.total_credits <= 0:
            return None
        return max(0.0, min(100.0, self.total_usage / self.total_credits * 100.0))


def _number(value: Any) -> Optional[float]:
    """Accept both numbers and decimal strings."""
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value.strip())
        except ValueError:
            return None
    return None


def parse_credits(payload: Any) -> CreditsData:
    if not isinstance(payload, dict):
        raise ParseError("Credits response is not an object.")
    data = payload.get("data")
    if not isinstance(data, dict):
        raise ParseError("Credits response has no data object.")
    total_credits = _number(data.get("total_credits"))
    total_usage = _number(data.get("total_usage"))
    if total_credits is None or total_usage is None:
        raise ParseError("Credits response has no usable numbers.")
    return CreditsData(total_credits=total_credits, total_usage=total_usage)


def fetch_credits(
    api_key: str, session: Optional[requests.Session] = None
) -> CreditsData:
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Accept": "application/json",
        "User-Agent": "LlmUsageMeter/1.0",
    }
    client = session or requests
    response = client.get(CREDITS_URL, headers=headers, timeout=15)
    if response.status_code == 401:
        raise AuthExpiredError("HTTP 401")
    if response.status_code == 403:
        raise ScopeError("HTTP 403")
    if not 200 <= response.status_code < 300:
        raise FetchError(f"HTTP {response.status_code}")
    try:
        payload = response.json()
    except ValueError as exc:
        raise FetchError("The API returned invalid JSON.") from exc
    return parse_credits(payload)
