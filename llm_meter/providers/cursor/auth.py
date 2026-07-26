"""Cursor session resolution for personal usage endpoints.

Personal spending/usage on ``cursor.com`` is gated by the WorkOS web session
cookie ``WorkosCursorSessionToken``, not by a User/Agent API key. There is no
public OAuth client for this, so we reuse a session the user already has:

1. Local Cursor session (IDE ``state.vscdb``, then ``cursor-agent`` keychain)
2. A browser cookie the user pasted into this app's credential store

Resolution never writes back to Cursor's own stores.
"""

from __future__ import annotations

import base64
import json
import logging
import os
import sqlite3
import sys
from dataclasses import dataclass
from typing import Optional
from urllib.parse import unquote

from ... import keystore
from ...i18n import tr

logger = logging.getLogger(__name__)

KEYSTORE_ACCOUNT = "cursor-session"
ENV_SESSION_TOKEN = "CURSOR_SESSION_TOKEN"
CLI_KEYCHAIN_SERVICE = "cursor-access-token"
CLI_KEYCHAIN_ACCOUNT = "cursor-user"
ACCESS_TOKEN_KEY = "cursorAuth/accessToken"

_MIN_JWT_LENGTH = 40


class AuthError(Exception):
    """The session could not be read or stored."""


@dataclass(frozen=True)
class Session:
    """A cookie-ready Cursor web session.

    ``cookie_value`` is the decoded ``user_…::jwt`` form. Send it as
    ``WorkosCursorSessionToken`` (encoding ``::`` as ``%3A%3A`` is optional;
    both forms are accepted by cursor.com today).
    """

    access_token: str
    user_id: str
    cookie_value: str
    source: str  # env | ide | cli | keystore


def instructions() -> str:
    return tr(
        "1. https://cursor.com/dashboard/usage 에서 로그인하세요\n"
        "2. 개발자 도구(F12) → 애플리케이션/저장소 → 쿠키 → https://cursor.com\n"
        "3. 'WorkosCursorSessionToken' 쿠키의 값을 복사하세요",
        "1. Sign in at https://cursor.com/dashboard/usage\n"
        "2. Open DevTools (F12) → Application/Storage → Cookies → https://cursor.com\n"
        "3. Copy the value of the 'WorkosCursorSessionToken' cookie",
    )


def _decode_jwt_payload(token: str) -> dict:
    try:
        payload = token.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        return json.loads(base64.urlsafe_b64decode(payload).decode("utf-8"))
    except (IndexError, ValueError, UnicodeDecodeError, json.JSONDecodeError):
        return {}


def user_id_from_token(access_token: str) -> Optional[str]:
    """Derive the cookie user id from a session JWT ``sub`` claim."""
    sub = _decode_jwt_payload(access_token).get("sub")
    if not isinstance(sub, str) or not sub:
        return None
    # Browser cookies use ``user_…``; IDE/CLI JWTs often carry ``auth0|user_…``.
    if "|" in sub:
        sub = sub.rsplit("|", 1)[-1]
    return sub if sub.startswith("user_") else sub


def _looks_like_jwt(value: str) -> bool:
    parts = value.split(".")
    return len(parts) == 3 and all(parts) and len(value) >= _MIN_JWT_LENGTH


def clean_session_token(value: Optional[str]) -> Optional[str]:
    """Normalize a pasted cookie / token; None when it cannot be one."""
    if not value:
        return None
    text = value.strip().strip("\"'").strip()
    lower = text.lower()
    for prefix in ("workoscursorsessiontoken=", "cookie:"):
        if lower.startswith(prefix):
            text = text[len(prefix) :]
            lower = text.lower()
    text = text.strip().rstrip(";").strip().strip("\"'").strip()
    text = unquote(text)
    if "\n" in text or "\r" in text or any(char.isspace() for char in text):
        return None
    if "::" in text:
        user_id, _, token = text.partition("::")
        user_id, token = user_id.strip(), token.strip()
        if not user_id or not _looks_like_jwt(token):
            return None
        return f"{user_id}::{token}"
    if _looks_like_jwt(text) and user_id_from_token(text):
        return text
    return None


def session_from_token(raw: str, source: str) -> Session:
    """Build a :class:`Session` from a JWT or ``user_…::jwt`` string."""
    cleaned = clean_session_token(raw)
    if not cleaned:
        raise AuthError(tr("잘못된 세션 토큰입니다", "That is not a valid session token"))
    if "::" in cleaned:
        user_id, _, access_token = cleaned.partition("::")
    else:
        access_token = cleaned
        user_id = user_id_from_token(access_token) or ""
    if not user_id or not _looks_like_jwt(access_token):
        raise AuthError(tr("잘못된 세션 토큰입니다", "That is not a valid session token"))
    return Session(
        access_token=access_token,
        user_id=user_id,
        cookie_value=f"{user_id}::{access_token}",
        source=source,
    )


def ide_state_db_paths() -> list[str]:
    """Candidate paths for Cursor IDE ``state.vscdb`` (stable first)."""
    home = os.path.expanduser("~")
    if sys.platform == "darwin":
        base = os.path.join(home, "Library", "Application Support")
        return [
            os.path.join(base, "Cursor", "User", "globalStorage", "state.vscdb"),
            os.path.join(base, "Cursor - Insiders", "User", "globalStorage", "state.vscdb"),
        ]
    if sys.platform == "win32":
        appdata = os.environ.get("APPDATA") or os.path.join(home, "AppData", "Roaming")
        return [
            os.path.join(appdata, "Cursor", "User", "globalStorage", "state.vscdb"),
            os.path.join(appdata, "Cursor - Insiders", "User", "globalStorage", "state.vscdb"),
        ]
    config = os.environ.get("XDG_CONFIG_HOME") or os.path.join(home, ".config")
    return [
        os.path.join(config, "Cursor", "User", "globalStorage", "state.vscdb"),
        os.path.join(config, "cursor", "User", "globalStorage", "state.vscdb"),
    ]


def read_ide_access_token(db_path: Optional[str] = None) -> Optional[str]:
    """Read ``cursorAuth/accessToken`` from the IDE SQLite state DB."""
    paths = [db_path] if db_path else ide_state_db_paths()
    for path in paths:
        if not path or not os.path.isfile(path):
            continue
        try:
            # URI read-only avoids locking out a running Cursor IDE.
            uri = f"file:{path}?mode=ro"
            with sqlite3.connect(uri, uri=True, timeout=1.0) as conn:
                row = conn.execute(
                    "SELECT value FROM ItemTable WHERE key = ? LIMIT 1",
                    (ACCESS_TOKEN_KEY,),
                ).fetchone()
        except sqlite3.Error as exc:
            logger.debug("Could not read Cursor IDE state at %s: %s", path, exc)
            continue
        if not row or not isinstance(row[0], str):
            continue
        token = row[0].strip()
        if _looks_like_jwt(token):
            return token
    return None


def _read_keyring_cli_token() -> Optional[str]:
    """Read the JWT via the keyring API (not the ``security`` CLI).

    Spawning ``security find-generic-password -w`` asks Keychain on behalf of
    ``/usr/bin/security``, which re-prompts (or hangs on recent macOS) even when
    this app's own Python binary is already allowed. Prefer the in-process API.
    """
    try:
        import keyring
    except ImportError:
        return None
    try:
        token = keyring.get_password(CLI_KEYCHAIN_SERVICE, CLI_KEYCHAIN_ACCOUNT)
    except Exception as exc:  # noqa: BLE001 — backend-specific failures
        logger.debug("keyring CLI token read failed: %s", exc)
        return None
    if isinstance(token, str) and _looks_like_jwt(token.strip()):
        return token.strip()
    return None


def read_cli_access_token() -> Optional[str]:
    """Read the JWT written by ``cursor-agent`` / ``agent login``."""
    return _read_keyring_cli_token()


def load_pasted_session_token() -> Optional[str]:
    try:
        return keystore.get(KEYSTORE_ACCOUNT)
    except keystore.KeystoreError as exc:
        raise AuthError(str(exc)) from exc


def save_pasted_session_token(value: str) -> None:
    cleaned = clean_session_token(value)
    if not cleaned:
        raise AuthError(tr("잘못된 세션 토큰입니다", "That is not a valid session token"))
    # Prefer the cookie form so a raw JWT paste still round-trips with user id.
    session = session_from_token(cleaned, source="keystore")
    try:
        keystore.set(KEYSTORE_ACCOUNT, session.cookie_value)
    except keystore.KeystoreError as exc:
        raise AuthError(str(exc)) from exc


def delete_pasted_session_token() -> None:
    try:
        keystore.delete(KEYSTORE_ACCOUNT)
    except keystore.KeystoreError as exc:
        raise AuthError(str(exc)) from exc


def resolve_session(
    *,
    env: Optional[dict[str, str]] = None,
    ide_token: Optional[str] = None,
    cli_token: Optional[str] = None,
    pasted: Optional[str] = None,
    probe_local: bool = True,
) -> Optional[Session]:
    """Resolve a session: local Cursor login first, pasted cookie second.

    ``CURSOR_SESSION_TOKEN`` overrides everything when set (tests / debugging).
    """
    environ = os.environ if env is None else env
    override = environ.get(ENV_SESSION_TOKEN)
    if override:
        cleaned = clean_session_token(override)
        if cleaned:
            return session_from_token(cleaned, source="env")
        logger.warning("%s is set but is not a valid session token", ENV_SESSION_TOKEN)

    token = ide_token
    if token is None and probe_local:
        token = read_ide_access_token()
    if token:
        try:
            return session_from_token(token, source="ide")
        except AuthError:
            logger.debug("IDE access token was not usable", exc_info=True)

    token = cli_token
    if token is None and probe_local:
        token = read_cli_access_token()
    if token:
        try:
            return session_from_token(token, source="cli")
        except AuthError:
            logger.debug("CLI access token was not usable", exc_info=True)

    stored = pasted
    if stored is None:
        stored = load_pasted_session_token()
    if stored:
        try:
            return session_from_token(stored, source="keystore")
        except AuthError:
            logger.debug("Stored pasted session was not usable", exc_info=True)
    return None
