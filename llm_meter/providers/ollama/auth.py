"""Ollama Cloud session-cookie handling.

The settings page authenticates browsers with two cookies: ``aid`` (a stable
anonymous id) and ``__Secure-session`` (the signed session). Reading cookies
out of an installed browser is not viable (Chromium 127+ App-Bound Encryption
on Windows, Keychain and Full-Disk-Access prompts on macOS), so each value is
supplied by the user — pasted from the clipboard or typed in — and kept in the
OS credential store.
"""

import logging
from typing import Optional

from ... import keystore
from ...i18n import tr

logger = logging.getLogger(__name__)

AID_ACCOUNT = "ollama-aid"
SESSION_ACCOUNT = "ollama-session"

_MIN_AID_LENGTH = 8
_MIN_SESSION_LENGTH = 40


class AuthError(Exception):
    """The session cookies could not be read or stored."""


def instructions() -> str:
    return tr(
        "1. https://ollama.com/settings 에서 로그인하세요\n"
        "2. 개발자 도구(F12) → 애플리케이션/저장소 → 쿠키 → https://ollama.com\n"
        "3. 'aid' 쿠키와 '__Secure-session' 쿠키의 값을 각각 복사하세요",
        "1. Sign in at https://ollama.com/settings\n"
        "2. Open DevTools (F12) → Application/Storage → Cookies → https://ollama.com\n"
        "3. Copy the values of the 'aid' and '__Secure-session' cookies",
    )


def clean_aid(value: Optional[str]) -> Optional[str]:
    """Normalize a pasted ``aid`` cookie value; None when it cannot be one."""
    if not value:
        return None
    text = value.strip().strip("\"'").strip()
    if text.lower().startswith("aid="):
        text = text[len("aid="):]
    # Quotes are stripped again: DevTools copies the pair as aid="value";
    text = text.strip().rstrip(";").strip().strip("\"'").strip()
    if "\n" in text or "\r" in text or any(char.isspace() for char in text):
        return None
    if len(text) < _MIN_AID_LENGTH:
        return None
    return text


def clean_session(value: Optional[str]) -> Optional[str]:
    """Normalize a pasted ``__Secure-session`` cookie value; None when invalid."""
    if not value:
        return None
    text = value.strip().strip("\"'").strip()
    if text.lower().startswith("__secure-session="):
        text = text[len("__secure-session="):]
    text = text.strip().rstrip(";").strip().strip("\"'").strip()
    if "\n" in text or "\r" in text or any(char.isspace() for char in text):
        return None
    if len(text) < _MIN_SESSION_LENGTH:
        return None
    return text


def load_aid() -> Optional[str]:
    try:
        return keystore.get(AID_ACCOUNT)
    except keystore.KeystoreError as exc:
        raise AuthError(str(exc)) from exc


def save_aid(value: str) -> None:
    try:
        keystore.set(AID_ACCOUNT, value)
    except keystore.KeystoreError as exc:
        raise AuthError(str(exc)) from exc


def delete_aid() -> None:
    try:
        keystore.delete(AID_ACCOUNT)
    except keystore.KeystoreError as exc:
        raise AuthError(str(exc)) from exc


def load_session() -> Optional[str]:
    try:
        return keystore.get(SESSION_ACCOUNT)
    except keystore.KeystoreError as exc:
        raise AuthError(str(exc)) from exc


def save_session(value: str) -> None:
    try:
        keystore.set(SESSION_ACCOUNT, value)
    except keystore.KeystoreError as exc:
        raise AuthError(str(exc)) from exc


def delete_session() -> None:
    try:
        keystore.delete(SESSION_ACCOUNT)
    except keystore.KeystoreError as exc:
        raise AuthError(str(exc)) from exc
