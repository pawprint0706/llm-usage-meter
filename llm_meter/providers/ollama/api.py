"""Ollama Cloud settings client: usage, model stats and extra-usage balance.

The settings page is a server-rendered htmx app, so all data arrives embedded
in the HTML of ``GET /settings`` — there is no JSON API. The page is fetched
with the ``aid`` and ``__Secure-session`` cookies and parsed with regexes
anchored on stable data attributes:

    aria-label="Monthly usage $0.03 of $60 used"   (included-usage meter)
    data-time="2026-10-01T00:38:02Z"               (reset instant, UTC)
    data-usage-segment data-model="..." data-requests="14"
    Balance remaining ... $0
"""

import logging
import os
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

import requests

logger = logging.getLogger(__name__)

SETTINGS_PAGE = "https://ollama.com/settings"

_REDIRECT_CODES = (301, 302, 303, 307, 308)

# The included-usage meter exposes dollars, not percent: the plan grants a
# monthly credit and the bar segments are per-model shares of it.
_METER_RE = re.compile(
    r'aria-label="(Monthly usage) \$([0-9.]+) of \$([0-9.]+) used"'
)
_RESET_RE = re.compile(r'data-time="([^"]+)"')
_SEGMENT_RE = re.compile(
    r'data-usage-segment[^>]*data-model="([^"]*)"[^>]*data-requests="(\d+)"'
)
_BALANCE_RE = re.compile(
    r"Balance remaining.*?<div class=\"text-2xl[^\"]*\"[^>]*>([^<]*)<",
    re.DOTALL,
)
_PLAN_RE = re.compile(
    r"<span\s+class=\"text-xs font-normal px-2 py-0.5 rounded-full"
    r" bg-neutral-100 text-neutral-600 capitalize\"\s*>\s*([^<]*)</span\s*>"
)


class ApiError(Exception):
    """Base class for settings-page errors."""


class AuthExpiredError(ApiError):
    """The session cookies are missing, invalid or expired."""


class FetchError(ApiError):
    """The settings page returned an unexpected HTTP response."""


class ParseError(ApiError):
    """The settings HTML could not be parsed."""


@dataclass
class UsageWindow:
    label: str
    used: float
    total: float
    percent: float
    reset_at: Optional[datetime]
    models: list["ModelUsage"]


@dataclass
class ModelUsage:
    name: str
    requests: int


@dataclass
class SettingsData:
    plan: Optional[str] = None
    monthly: Optional[UsageWindow] = None
    balance: Optional[float] = None
    balance_text: Optional[str] = None
    error: Optional[str] = None


def _session(aid: str, session_value: str) -> requests.Session:
    session = requests.Session()
    session.cookies.set("aid", aid, domain="ollama.com", path="/")
    session.cookies.set("__Secure-session", session_value, domain="ollama.com", path="/")
    return session


def _parse_money(text: str) -> Optional[float]:
    """Parse a '$12.50' / '$0' balance string; None when it is not money."""
    if not text:
        return None
    match = re.search(r"\$([0-9]+(?:\.[0-9]+)?)", text)
    if not match:
        return None
    return float(match.group(1))


def _parse_reset(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def parse_settings(html: str) -> SettingsData:
    """Extract usage, model stats and balance from the settings HTML."""
    data = SettingsData()

    plan = _PLAN_RE.search(html)
    if plan:
        data.plan = plan.group(1).strip() or None

    meters = list(_METER_RE.finditer(html))
    resets = list(_RESET_RE.finditer(html))
    segments = list(_SEGMENT_RE.finditer(html))

    # Each meter carries its own model segments, so assign every segment to
    # the meter it appears after.
    by_meter: dict[int, list[ModelUsage]] = {}
    for segment in segments:
        meter_index = None
        for index, match in enumerate(meters):
            if match.start() < segment.start():
                meter_index = index
            else:
                break
        if meter_index is None:
            continue
        by_meter.setdefault(meter_index, []).append(
            ModelUsage(name=segment.group(1), requests=int(segment.group(2)))
        )

    for index, match in enumerate(meters):
        label = match.group(1)
        used = float(match.group(2))
        total = float(match.group(3))
        percent = used / total * 100 if total else 0.0
        # The reset instant is the first data-time after the meter; the ones
        # further down belong to the recent-requests list.
        reset = next(
            (_parse_reset(reset.group(1)) for reset in resets if reset.start() > match.start()),
            None,
        )
        if label == "Monthly usage":
            data.monthly = UsageWindow(
                label=label,
                used=used,
                total=total,
                percent=percent,
                reset_at=reset,
                models=by_meter.get(index, []),
            )

    balance = _BALANCE_RE.search(html)
    if balance:
        text = balance.group(1).strip()
        data.balance_text = text
        data.balance = _parse_money(text)

    if data.monthly is None and data.balance is None:
        data.error = "no usage data found in the HTML"
    return data


def fetch_settings(aid: str, session_value: str) -> SettingsData:
    """Fetch and parse the settings page.

    :raises AuthExpiredError: when the cookies are missing/expired.
    :raises FetchError: when the page is unreachable or unparseable.
    :raises requests.RequestException: on network failure.
    """
    if not aid or not session_value:
        raise AuthExpiredError("no session cookies")

    session = _session(aid, session_value)
    response = session.get(SETTINGS_PAGE, timeout=15, allow_redirects=False)

    if response.status_code in _REDIRECT_CODES:
        location = response.headers.get("location", "?")
        raise AuthExpiredError(f"session expired (redirected to {location})")
    if response.status_code in (401, 403):
        raise AuthExpiredError(f"HTTP {response.status_code}")
    if response.status_code != 200:
        raise FetchError(f"the settings page returned HTTP {response.status_code}")

    data = parse_settings(response.text)
    if data.error:
        dump = _dump_html(response.text)
        raise FetchError(f"{data.error} (page saved to {dump})")
    return data


def _dump_html(html: str) -> str:
    """Save the unparseable page for diagnosis; returns the path."""
    from ... import config

    path = os.path.join(config.config_dir(), "ollama-last-fetch.html")
    try:
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(html)
    except OSError as exc:
        logger.warning("Could not dump the HTML: %s", exc)
        return "<dump failed>"
    return path
