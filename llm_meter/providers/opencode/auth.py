"""OpenCode session-key handling.

The console authenticates browsers with an ``auth`` cookie. Reading that cookie
out of an installed browser is not viable (Chromium 127+ App-Bound Encryption on
Windows, Keychain and Full-Disk-Access prompts on macOS), so the key is supplied
by the user — pasted from the clipboard or typed in — and kept in the OS
credential store.
"""

import logging
from typing import Optional

from ... import keystore
from ...i18n import tr

logger = logging.getLogger(__name__)

KEYSTORE_ACCOUNT = "opencode-session"

_MIN_KEY_LENGTH = 20


class AuthError(Exception):
    """The session key could not be read or stored."""


def instructions() -> str:
    return tr(
        "1. https://opencode.ai/auth 에서 로그인하세요\n"
        "2. 개발자 도구(F12) → 애플리케이션/저장소 → 쿠키 → https://opencode.ai\n"
        "3. 'auth' 쿠키의 값을 복사하세요",
        "1. Sign in at https://opencode.ai/auth\n"
        "2. Open DevTools (F12) → Application/Storage → Cookies → https://opencode.ai\n"
        "3. Copy the value of the 'auth' cookie",
    )


def clean_session_key(value: Optional[str]) -> Optional[str]:
    """Normalize a pasted session key; None when it cannot be one."""
    if not value:
        return None
    text = value.strip().strip("\"'").strip()
    if text.lower().startswith("auth="):
        text = text[len("auth="):]
    # Quotes are stripped again: DevTools copies the pair as auth="value";
    text = text.strip().rstrip(";").strip().strip("\"'").strip()
    if len(text) < _MIN_KEY_LENGTH or any(char.isspace() for char in text):
        return None
    return text


def load_session_key() -> Optional[str]:
    try:
        return keystore.get(KEYSTORE_ACCOUNT)
    except keystore.KeystoreError as exc:
        raise AuthError(str(exc)) from exc


def save_session_key(value: str) -> None:
    try:
        keystore.set(KEYSTORE_ACCOUNT, value)
    except keystore.KeystoreError as exc:
        raise AuthError(str(exc)) from exc


def delete_session_key() -> None:
    try:
        keystore.delete(KEYSTORE_ACCOUNT)
    except keystore.KeystoreError as exc:
        raise AuthError(str(exc)) from exc
