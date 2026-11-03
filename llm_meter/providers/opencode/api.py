"""OpenCode Go usage via the bearer-authenticated usage endpoint."""

from dataclasses import dataclass
from datetime import datetime
from uuid import uuid4

import requests

USAGE_URL = "https://opencode.ai/zen/go/v1/usage"
CONSOLE_PAGE = "https://opencode.ai/console"
_SESSION_TAG = f"llm-usage-meter-{uuid4()}"


class ApiError(Exception):
    """Base class for usage API errors."""


class AuthExpiredError(ApiError):
    """The API key is missing, invalid or expired."""


class ScopeError(ApiError):
    """The key cannot access Go usage (permissions or subscription)."""


class FetchError(ApiError):
    """The server returned an unexpected HTTP response."""


class ParseError(ApiError):
    """The usage response does not match the expected contract."""


@dataclass
class UsageWindow:
    percent: float
    status: str
    resets_at: datetime


@dataclass
class GoUsage:
    rolling: UsageWindow
    weekly: UsageWindow
    monthly: UsageWindow


def _parse_window(value: object, name: str) -> UsageWindow:
    if not isinstance(value, dict):
        raise ParseError(f"missing Go usage window: {name}")
    percent = value.get("percent")
    if (
        isinstance(percent, bool)
        or not isinstance(percent, (int, float))
        or not 0 <= percent <= 100
    ):
        raise ParseError(f"invalid Go usage percentage: {name}")
    status = value.get("status")
    if status not in ("ok", "rate-limited"):
        raise ParseError(f"invalid Go usage status: {name}")
    raw_reset = value.get("resetsAt")
    if not isinstance(raw_reset, str):
        raise ParseError(f"missing Go reset timestamp: {name}")
    try:
        resets_at = datetime.fromisoformat(raw_reset.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ParseError(f"invalid Go reset timestamp: {name}") from exc
    if resets_at.tzinfo is None or resets_at.utcoffset() is None:
        raise ParseError(f"Go reset timestamp has no timezone: {name}")
    return UsageWindow(percent=float(percent), status=status, resets_at=resets_at)


def parse_usage(payload: object) -> GoUsage:
    """Require all three windows; never turn a partial response into a meter."""
    usage = payload.get("usage") if isinstance(payload, dict) else None
    if not isinstance(usage, dict):
        raise ParseError("missing Go usage object")
    return GoUsage(**{
        name: _parse_window(usage.get(name), name)
        for name in ("rolling", "weekly", "monthly")
    })


def fetch_usage(api_key: str) -> GoUsage:
    if not api_key:
        raise AuthExpiredError("no API key")
    response = requests.get(
        USAGE_URL,
        headers={
            "Accept": "application/json",
            "Authorization": f"Bearer {api_key}",
            "User-Agent": "llm-usage-meter",
            "x-opencode-session": _SESSION_TAG,
        },
        timeout=15,
        allow_redirects=False,
    )
    if response.status_code == 401:
        raise AuthExpiredError("OpenCode API key rejected (HTTP 401)")
    if response.status_code == 403:
        raise ScopeError("OpenCode Go usage unavailable (HTTP 403)")
    if response.status_code != 200:
        raise FetchError(f"OpenCode usage API returned HTTP {response.status_code}")
    try:
        payload = response.json()
    except ValueError as exc:
        raise ParseError("OpenCode usage API returned invalid JSON") from exc
    return parse_usage(payload)
