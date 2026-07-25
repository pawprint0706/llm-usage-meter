"""Cursor personal usage and spending client.

Undocumented dashboard endpoints behind the WorkOS session cookie:

    GET  /api/usage-summary                         cycle limits + percentages
    POST /api/dashboard/get-hard-limit              on-demand / overage switch
    POST /api/dashboard/get-plan-info               plan name and included cents

Spending and usage pages on cursor.com read the same session.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional

import requests

from .auth import Session

API_BASE = "https://cursor.com"
USAGE_SUMMARY_URL = f"{API_BASE}/api/usage-summary"
HARD_LIMIT_URL = f"{API_BASE}/api/dashboard/get-hard-limit"
PLAN_INFO_URL = f"{API_BASE}/api/dashboard/get-plan-info"

USAGE_PAGE = f"{API_BASE}/dashboard/usage"
SPENDING_PAGE = f"{API_BASE}/dashboard/spending"


class FetchError(Exception):
    """A Cursor API call failed."""


class AuthExpiredError(FetchError):
    """The session cookie was rejected."""


@dataclass
class PlanUsage:
    """Included-plan meter for the current billing cycle.

    ``used`` / ``limit`` / breakdown values are dollar-cents as returned by the
    dashboard (``includedAmountCents`` on plan-info matches ``limit`` here).
    """

    enabled: bool = False
    used_cents: Optional[float] = None
    limit_cents: Optional[float] = None
    remaining_cents: Optional[float] = None
    included_cents: Optional[float] = None
    bonus_cents: Optional[float] = None
    total_cents: Optional[float] = None
    auto_percent: Optional[float] = None
    api_percent: Optional[float] = None
    total_percent: Optional[float] = None


@dataclass
class OnDemandUsage:
    enabled: bool = False
    used_cents: Optional[float] = None
    limit_cents: Optional[float] = None
    remaining_cents: Optional[float] = None


@dataclass
class UsageData:
    membership_type: Optional[str] = None
    plan_name: Optional[str] = None
    plan_price: Optional[str] = None
    billing_cycle_start: Optional[datetime] = None
    billing_cycle_end: Optional[datetime] = None
    is_unlimited: bool = False
    plan: PlanUsage = field(default_factory=PlanUsage)
    on_demand: OnDemandUsage = field(default_factory=OnDemandUsage)
    no_usage_based_allowed: Optional[bool] = None
    auto_message: Optional[str] = None
    named_message: Optional[str] = None

    @property
    def gauge_percent(self) -> Optional[float]:
        if self.plan.total_percent is not None:
            return self.plan.total_percent
        if self.plan.api_percent is not None:
            return self.plan.api_percent
        return self.plan.auto_percent


def _headers(session: Session) -> dict[str, str]:
    return {
        "Accept": "application/json",
        "Content-Type": "application/json",
        "Cookie": f"WorkosCursorSessionToken={session.cookie_value}",
        "Origin": API_BASE,
        "Referer": USAGE_PAGE,
        "User-Agent": "llm-usage-meter",
    }


def _parse_timestamp(value: Any) -> Optional[datetime]:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        # Dashboard POSTs use epoch milliseconds; GET ISO strings elsewhere.
        seconds = value / 1000.0 if value > 1e12 else float(value)
        try:
            return datetime.fromtimestamp(seconds, tz=timezone.utc)
        except (OverflowError, OSError, ValueError):
            return None
    if isinstance(value, str):
        text = value.strip()
        if text.isdigit():
            return _parse_timestamp(int(text))
        try:
            return datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            return None
    return None


def _as_float(value: Any) -> Optional[float]:
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


def _as_bool(value: Any) -> Optional[bool]:
    return value if isinstance(value, bool) else None


def _request_json(
    method: str,
    url: str,
    session: Session,
    *,
    body: Optional[dict] = None,
    client: Optional[requests.Session] = None,
) -> Any:
    http = client or requests
    response = http.request(
        method,
        url,
        headers=_headers(session),
        json=body,
        timeout=20,
    )
    if response.status_code in (401, 403):
        raise AuthExpiredError("not_authenticated")
    if response.status_code != 200:
        raise FetchError(f"{url} failed (HTTP {response.status_code})")
    try:
        return response.json()
    except ValueError as exc:
        raise FetchError(f"{url} returned invalid JSON") from exc


def cents_to_dollars(cents: Optional[float]) -> Optional[float]:
    if cents is None:
        return None
    return cents / 100.0


def parse_plan_usage(raw: Any) -> PlanUsage:
    if not isinstance(raw, dict):
        return PlanUsage()
    breakdown = raw.get("breakdown") if isinstance(raw.get("breakdown"), dict) else {}
    return PlanUsage(
        enabled=bool(raw.get("enabled")),
        used_cents=_as_float(raw.get("used")),
        limit_cents=_as_float(raw.get("limit")),
        remaining_cents=_as_float(raw.get("remaining")),
        included_cents=_as_float(breakdown.get("included")),
        bonus_cents=_as_float(breakdown.get("bonus")),
        total_cents=_as_float(breakdown.get("total")),
        auto_percent=_as_float(raw.get("autoPercentUsed")),
        api_percent=_as_float(raw.get("apiPercentUsed")),
        total_percent=_as_float(raw.get("totalPercentUsed")),
    )


def parse_on_demand(raw: Any) -> OnDemandUsage:
    if not isinstance(raw, dict):
        return OnDemandUsage()
    return OnDemandUsage(
        enabled=bool(raw.get("enabled")),
        used_cents=_as_float(raw.get("used")),
        limit_cents=_as_float(raw.get("limit")),
        remaining_cents=_as_float(raw.get("remaining")),
    )


def parse_usage_summary(payload: Any) -> UsageData:
    if not isinstance(payload, dict):
        raise FetchError("usage-summary returned an unexpected payload")
    individual = payload.get("individualUsage")
    if not isinstance(individual, dict):
        individual = {}
    membership = payload.get("membershipType")
    return UsageData(
        membership_type=membership if isinstance(membership, str) else None,
        billing_cycle_start=_parse_timestamp(payload.get("billingCycleStart")),
        billing_cycle_end=_parse_timestamp(payload.get("billingCycleEnd")),
        is_unlimited=bool(payload.get("isUnlimited")),
        plan=parse_plan_usage(individual.get("plan")),
        on_demand=parse_on_demand(individual.get("onDemand")),
        auto_message=(
            payload.get("autoModelSelectedDisplayMessage")
            if isinstance(payload.get("autoModelSelectedDisplayMessage"), str)
            else None
        ),
        named_message=(
            payload.get("namedModelSelectedDisplayMessage")
            if isinstance(payload.get("namedModelSelectedDisplayMessage"), str)
            else None
        ),
    )


def apply_plan_info(data: UsageData, payload: Any) -> None:
    if not isinstance(payload, dict):
        return
    info = payload.get("planInfo")
    if not isinstance(info, dict):
        return
    name = info.get("planName")
    price = info.get("price")
    if isinstance(name, str) and name:
        data.plan_name = name
    if isinstance(price, str) and price:
        data.plan_price = price
    included = _as_float(info.get("includedAmountCents"))
    if included is not None and data.plan.limit_cents is None:
        data.plan.limit_cents = included
    end = _parse_timestamp(info.get("billingCycleEnd"))
    if end is not None and data.billing_cycle_end is None:
        data.billing_cycle_end = end


def apply_hard_limit(data: UsageData, payload: Any) -> None:
    if not isinstance(payload, dict):
        return
    flag = _as_bool(payload.get("noUsageBasedAllowed"))
    if flag is not None:
        data.no_usage_based_allowed = flag


def fetch_me(
    session: Session, client: Optional[requests.Session] = None
) -> dict[str, Any]:
    data = _request_json("GET", f"{API_BASE}/api/auth/me", session, client=client)
    if not isinstance(data, dict):
        raise FetchError("auth/me returned an unexpected payload")
    return data


def fetch_usage(
    session: Session, client: Optional[requests.Session] = None
) -> UsageData:
    """Load the current billing-cycle usage snapshot."""
    summary = _request_json("GET", USAGE_SUMMARY_URL, session, client=client)
    data = parse_usage_summary(summary)

    try:
        apply_hard_limit(
            data, _request_json("POST", HARD_LIMIT_URL, session, body={}, client=client)
        )
    except FetchError:
        # Hard-limit is supplementary; keep the summary if it fails.
        pass

    try:
        apply_plan_info(
            data, _request_json("POST", PLAN_INFO_URL, session, body={}, client=client)
        )
    except FetchError:
        pass

    return data
