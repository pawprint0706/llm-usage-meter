"""OpenCode console client: Go plan usage and Zen credit billing.

The console is a SolidStart app, so both data sets arrive embedded in the
server-rendered HTML as seroval-serialized JavaScript:

    rollingUsage:$R[36]={status:"ok",resetInSec:18000,usagePercent:0}
    {customerID:"cus_...",balance:1613089290,monthlyLimit:20,...}

A real recursive parser (not a regex) reads the value directly following each
key, so an error-state object can never swallow the next period's data, nested
objects stay nested, and ``!0``/``!1`` booleans parse correctly.
"""

import logging
import os
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Optional, Type, Union

import requests

logger = logging.getLogger(__name__)

CONSOLE_BASE = "https://opencode.ai"
AUTH_PAGE = f"{CONSOLE_BASE}/auth"

USAGE_KEYS = ("rollingUsage", "weeklyUsage", "monthlyUsage")

# Zen money fields are stored as dollars * 1e8 (integer) in the console SSR.
MONEY_SCALE = 100_000_000

_REDIRECT_CODES = (301, 302, 303, 307, 308)


def go_page(workspace_id: str) -> str:
    return f"{CONSOLE_BASE}/workspace/{workspace_id}/go"


def zen_page(workspace_id: str) -> str:
    return f"{CONSOLE_BASE}/workspace/{workspace_id}/billing"


def stats_page(workspace_id: str) -> str:
    return f"{CONSOLE_BASE}/workspace/{workspace_id}/usage"


def workspace_page(workspace_id: str) -> str:
    return f"{CONSOLE_BASE}/workspace/{workspace_id}"


class ApiError(Exception):
    """Base class for console API errors."""


class AuthExpiredError(ApiError):
    """The session key is missing, invalid or expired."""


class FetchError(ApiError):
    """The console returned an unexpected HTTP response."""


class ParseError(ApiError):
    """The console HTML could not be parsed."""


@dataclass
class GoUsage:
    rolling: dict
    weekly: dict
    monthly: dict
    mine: bool = True
    use_balance: bool = False


@dataclass
class ZenBilling:
    balance: Optional[float] = None
    monthly_limit: Optional[float] = None
    monthly_usage: Optional[float] = None
    time_monthly_usage_updated: Optional[str] = None
    reload_enabled: bool = False
    reload_amount: Optional[float] = None
    reload_trigger: Optional[float] = None
    payment_method: Optional[str] = None


@dataclass
class ConsoleData:
    go: Optional[GoUsage] = None
    zen: Optional[ZenBilling] = None
    go_error: Optional[str] = None
    zen_error: Optional[str] = None


def _session(session_key: str) -> requests.Session:
    session = requests.Session()
    session.cookies.set("auth", session_key, domain="opencode.ai", path="/")
    return session


# --------------------------------------------------------------------------
# Seroval / JS object-literal parser
# --------------------------------------------------------------------------

_WS = " \t\r\n"
_NUM_RE = re.compile(r"-?(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?")
_IDENT_RE = re.compile(r"[A-Za-z_$][\w$]*")
_KEY_RE = re.compile(r"[\w$]+")
_REF_ASSIGN_RE = re.compile(r"\$R\[\d+\]\s*=\s*")
_REF_RE = re.compile(r"\$R\[\d+\]")
_CTOR_RE = re.compile(r"\s*[A-Za-z_$][\w$]*\s*\(")

_ESCAPES = {"n": "\n", "t": "\t", "r": "\r", "b": "\b", "f": "\f", "v": "\v", "0": "\0"}


def _skip_ws(text: str, index: int) -> int:
    while index < len(text) and text[index] in _WS:
        index += 1
    return index


def _parse_value(text: str, index: int) -> tuple[Any, int]:
    """Parse one JS value starting at ``text[index]``; returns (value, next index).

    Supports objects, arrays, strings, numbers (incl. exponents), ``!0``/``!1``
    booleans, null/undefined/void 0, ``new Date(...)`` constructor calls, and
    ``$R[n]=`` reference assignments. Bare ``$R[n]`` back-references cannot be
    resolved and become None.
    """
    index = _skip_ws(text, index)
    while True:
        match = _REF_ASSIGN_RE.match(text, index)
        if not match:
            break
        index = match.end()
    if index >= len(text):
        raise ParseError("unexpected end of input")
    char = text[index]
    if char == "{":
        return _parse_object(text, index)
    if char == "[":
        return _parse_array(text, index)
    if char in "\"'":
        return _parse_string(text, index)
    if char == "!" and index + 1 < len(text) and text[index + 1] in "01":
        return text[index + 1] == "0", index + 2
    if text.startswith("-Infinity", index):
        return None, index + len("-Infinity")
    match = _NUM_RE.match(text, index)
    if match:
        raw = match.group(0)
        number = float(raw)
        if number.is_integer() and "." not in raw and "e" not in raw.lower():
            return int(number), match.end()
        return number, match.end()
    match = _REF_RE.match(text, index)
    if match:
        return None, match.end()
    match = _IDENT_RE.match(text, index)
    if match:
        word, end = match.group(0), match.end()
        if word in ("null", "undefined", "NaN", "Infinity"):
            return None, end
        if word == "true":
            return True, end
        if word == "false":
            return False, end
        if word == "void":
            skipped = re.compile(r"\s*0").match(text, end)
            return None, (skipped.end() if skipped else end)
        if word == "new":
            return _parse_construction(text, end)
        return word, end  # bare identifier -- keep as string
    raise ParseError(f"unexpected character at {index}: {text[index:index + 20]!r}")


def _parse_construction(text: str, index: int) -> tuple[Any, int]:
    """Parse ``new Ctor(arg, ...)`` and return its first argument.

    The console serializes timestamps as ``new Date("2026-07-20T11:25:36.000Z")``;
    the ISO string is the useful part and keeps the surrounding object parseable.
    """
    match = _CTOR_RE.match(text, index)
    if not match:
        raise ParseError(f"expected a constructor call at {index}")
    index = match.end()
    index = _skip_ws(text, index)
    first: Any = None
    if index < len(text) and text[index] == ")":
        return None, index + 1
    while index < len(text):
        value, index = _parse_value(text, index)
        if first is None:
            first = value
        index = _skip_ws(text, index)
        if index < len(text) and text[index] == ",":
            index += 1
            continue
        if index < len(text) and text[index] == ")":
            return first, index + 1
        raise ParseError(f"expected ',' or ')' at {index}")
    raise ParseError("unterminated constructor call")


def _parse_object(text: str, index: int) -> tuple[dict, int]:
    obj: dict = {}
    index = _skip_ws(text, index + 1)
    if index < len(text) and text[index] == "}":
        return obj, index + 1
    while index < len(text):
        index = _skip_ws(text, index)
        if index < len(text) and text[index] in "\"'":
            key, index = _parse_string(text, index)
        else:
            match = _KEY_RE.match(text, index)
            if not match:
                raise ParseError(f"bad object key at {index}: {text[index:index + 20]!r}")
            key, index = match.group(0), match.end()
        index = _skip_ws(text, index)
        if index >= len(text) or text[index] != ":":
            raise ParseError(f"expected ':' at {index}")
        value, index = _parse_value(text, index + 1)
        obj[key] = value
        index = _skip_ws(text, index)
        if index < len(text) and text[index] == ",":
            index += 1
            continue
        if index < len(text) and text[index] == "}":
            return obj, index + 1
        raise ParseError(f"expected ',' or '}}' at {index}")
    raise ParseError("unterminated object")


def _parse_array(text: str, index: int) -> tuple[list, int]:
    array: list = []
    index = _skip_ws(text, index + 1)
    if index < len(text) and text[index] == "]":
        return array, index + 1
    while index < len(text):
        value, index = _parse_value(text, index)
        array.append(value)
        index = _skip_ws(text, index)
        if index < len(text) and text[index] == ",":
            index += 1
            continue
        if index < len(text) and text[index] == "]":
            return array, index + 1
        raise ParseError(f"expected ',' or ']' at {index}")
    raise ParseError("unterminated array")


def _parse_string(text: str, index: int) -> tuple[str, int]:
    quote = text[index]
    index += 1
    out: list[str] = []
    while index < len(text):
        char = text[index]
        if char == "\\":
            if index + 1 >= len(text):
                raise ParseError("unterminated escape")
            following = text[index + 1]
            if following in ("u", "x"):
                width = 4 if following == "u" else 2
                try:
                    out.append(chr(int(text[index + 2:index + 2 + width], 16)))
                except ValueError:
                    raise ParseError(f"bad \\{following} escape at {index}") from None
                index += 2 + width
                continue
            out.append(_ESCAPES.get(following, following))
            index += 2
            continue
        if char == quote:
            return "".join(out), index + 1
        out.append(char)
        index += 1
    raise ParseError("unterminated string")


def _find_and_parse(html: str, key: str, want: Type) -> Optional[Union[dict, bool]]:
    """Return the first parseable value of the wanted type following ``key:``.

    Occurrences whose value fails to parse are skipped, so a loading/void
    placeholder earlier in the page cannot mask the real data.
    """
    for match in re.finditer(rf"\b{re.escape(key)}[\"']?\s*:", html):
        try:
            value, _ = _parse_value(html, match.end())
        except (ParseError, IndexError):
            continue
        if want is bool:
            if isinstance(value, bool):
                return value
        elif isinstance(value, want) and value:
            return value
    return None


def parse_go_usage(html: str) -> Optional[GoUsage]:
    """Extract the Go subscription usage objects from the console SSR HTML."""
    found: dict = {}
    for key in USAGE_KEYS:
        obj = _find_and_parse(html, key, dict)
        if obj:
            found[key] = obj
    if not found:
        return None

    # mine/useBalance are common words; only search near the usage block.
    anchor = html.find("rollingUsage")
    window = html[max(0, anchor - 5000):anchor + 5000] if anchor >= 0 else html
    flags = {}
    for key in ("useBalance", "mine"):
        value = _find_and_parse(window, key, bool)
        if value is not None:
            flags[key] = value

    return GoUsage(
        rolling=found.get("rollingUsage", {}),
        weekly=found.get("weeklyUsage", {}),
        monthly=found.get("monthlyUsage", {}),
        mine=flags.get("mine", True),
        use_balance=flags.get("useBalance", False),
    )


def _scaled(value: Any) -> Optional[float]:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value) / MONEY_SCALE


def _plain(value: Any) -> Optional[float]:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def parse_zen_billing(html: str) -> Optional[ZenBilling]:
    """Extract the Zen credit billing object from the console SSR HTML.

    The billing record is identified by its ``customerID`` key so a stray
    ``balance`` word elsewhere on the page cannot be mistaken for the value.
    Falls back to the first number directly following a ``balance:`` key.
    """
    for match in re.finditer(r"\{\s*customerID[\"']?\s*:", html):
        try:
            billing, _ = _parse_object(html, match.start())
        except (ParseError, IndexError):
            continue
        if not isinstance(billing, dict) or "balance" not in billing:
            continue
        return ZenBilling(
            balance=_scaled(billing.get("balance")),
            monthly_limit=_plain(billing.get("monthlyLimit")),
            monthly_usage=_scaled(billing.get("monthlyUsage")),
            time_monthly_usage_updated=(
                billing.get("timeMonthlyUsageUpdated")
                if isinstance(billing.get("timeMonthlyUsageUpdated"), str)
                else None
            ),
            reload_enabled=bool(billing.get("reload")),
            reload_amount=_plain(billing.get("reloadAmount")),
            reload_trigger=_plain(billing.get("reloadTrigger")),
            payment_method=(
                billing.get("paymentMethodType")
                if isinstance(billing.get("paymentMethodType"), str)
                else None
            ),
        )

    for match in re.finditer(r"\bbalance[\"']?\s*:", html):
        try:
            value, _ = _parse_value(html, match.end())
        except (ParseError, IndexError):
            continue
        balance = _scaled(value)
        if balance is not None:
            return ZenBilling(balance=balance)
    return None


def usage_updated_at(value: Optional[str]) -> Optional[datetime]:
    """The UTC instant a Zen usage figure was last settled, or None when unknown.

    The console embeds ``timeMonthlyUsageUpdated`` as a seroval ``new Date(...)``
    call whose ISO string the parser keeps verbatim (``...Z`` suffix included).
    """
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def find_nested_key(data: Any, key: str) -> Any:
    """Depth-first search for ``key`` in nested dicts/lists."""
    if isinstance(data, dict):
        if key in data:
            return data[key]
        for value in data.values():
            found = find_nested_key(value, key)
            if found is not None:
                return found
    elif isinstance(data, list):
        for value in data:
            found = find_nested_key(value, key)
            if found is not None:
                return found
    return None


def usage_percent(window: Optional[dict]) -> Optional[float]:
    """``usagePercent`` from a usage object, searching nested dicts too."""
    if not isinstance(window, dict):
        return None
    value = window.get("usagePercent")
    if value is None:
        value = find_nested_key(window, "usagePercent")
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def reset_in_seconds(window: Optional[dict]) -> Optional[float]:
    if not isinstance(window, dict):
        return None
    value = window.get("resetInSec")
    if value is None:
        value = find_nested_key(window, "resetInSec")
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


# --------------------------------------------------------------------------
# HTTP layer
# --------------------------------------------------------------------------

def find_workspace_id(session_key: str) -> Optional[str]:
    """Discover the workspace ID via the /auth redirect.

    :raises AuthExpiredError: if the console redirects to the login page.
    :raises requests.RequestException: on network failure.
    """
    session = _session(session_key)
    response = session.get(AUTH_PAGE, allow_redirects=False, timeout=10)
    if response.status_code in _REDIRECT_CODES:
        location = response.headers.get("location", "")
        match = re.search(r"/workspace/(wrk_[a-zA-Z0-9]+)", location)
        if match:
            return match.group(1)
        if "/auth/" in location:
            raise AuthExpiredError(f"not signed in (redirected to {location})")
        return None
    if response.status_code == 200:
        match = re.search(r"wrk_[a-zA-Z0-9]+", response.text)
        return match.group(0) if match else None
    logger.warning("/auth returned HTTP %s", response.status_code)
    return None


def fetch_console(session_key: str, workspace_id: Optional[str] = None) -> ConsoleData:
    """Fetch Go usage and Zen billing by parsing the console SSR HTML.

    Go and Zen are collected independently: a cancelled Go subscription must
    not hide the remaining Zen credit balance, and vice versa. Each side
    records its failure in ``ConsoleData.go_error`` / ``zen_error`` instead of
    raising, so the UI can show numbers for whichever side succeeded.

    :raises AuthExpiredError: when the session key is missing/expired.
    :raises FetchError: when the workspace is gone (HTTP 404) or undiscoverable.
    :raises requests.RequestException: on network failure.
    """
    if not session_key:
        raise AuthExpiredError("no session key")

    if workspace_id is None:
        workspace_id = find_workspace_id(session_key)
        if workspace_id is None:
            raise FetchError("could not discover the workspace ID")

    session = _session(session_key)
    data = ConsoleData()
    response = session.get(go_page(workspace_id), timeout=15, allow_redirects=False)

    if response.status_code in _REDIRECT_CODES:
        location = response.headers.get("location", "?")
        raise AuthExpiredError(f"session expired (redirected to {location})")
    if response.status_code in (401, 403):
        raise AuthExpiredError(f"HTTP {response.status_code}")
    if response.status_code == 404:
        # The workspace was removed or renamed: the provider rediscovers it.
        raise FetchError(f"the Go page returned HTTP {response.status_code}")
    if response.status_code != 200:
        data.go_error = f"the Go page returned HTTP {response.status_code}"
        logger.warning("Could not fetch the Go usage: %s", data.go_error)
    else:
        data.go = parse_go_usage(response.text)
        if data.go is None:
            dump = _dump_html(response.text)
            data.go_error = f"no usage data found in the HTML (page saved to {dump})"
            logger.warning("Could not read the Go usage: %s", data.go_error)
        data.zen = parse_zen_billing(response.text)

    if data.zen is None:
        # Older console builds only embed billing on the workspace home page.
        try:
            home = session.get(workspace_page(workspace_id), timeout=15, allow_redirects=False)
            if home.status_code == 200:
                data.zen = parse_zen_billing(home.text)
        except requests.RequestException as exc:
            logger.warning("Could not fetch the Zen balance: %s", exc)
            data.zen_error = str(exc)
    if data.zen is None and data.zen_error is None:
        # The Zen balance always exists (0 at worst), so this is a server issue.
        data.zen_error = "no billing data found"
        logger.warning("Could not read the Zen balance: %s", data.zen_error)
    return data


def _dump_html(html: str) -> str:
    """Save the unparseable page for diagnosis; returns the path."""
    from ... import config

    path = os.path.join(config.config_dir(), "opencode-last-fetch.html")
    try:
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(html)
    except OSError as exc:
        logger.warning("Could not dump the HTML: %s", exc)
        return "<dump failed>"
    return path


def check_session(session_key: str) -> Optional[bool]:
    """Whether the session key is valid.

    Returns True (valid), False (rejected) or None when the check could not be
    performed (network error / unexpected response). None must never be treated
    as an expired session.
    """
    if not session_key:
        return False
    session = _session(session_key)
    try:
        response = session.get(f"{CONSOLE_BASE}/auth/status", timeout=10)
    except requests.RequestException as exc:
        logger.warning("Session check failed (network): %s", exc)
        return None
    if response.status_code in (401, 403):
        return False
    if response.status_code != 200:
        logger.warning("/auth/status returned HTTP %s", response.status_code)
        return None
    try:
        payload = response.json()
    except ValueError:
        return None
    return bool(payload.get("account"))
