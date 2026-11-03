"""User-supplied OpenCode API keys, kept in the OS credential store."""

import re
from typing import Optional

from ... import keystore
from ...i18n import tr

KEYSTORE_ACCOUNT = "opencode-api-key"


class AuthError(Exception):
    """The API key could not be read or stored."""


def instructions() -> str:
    return tr(
        "1. https://opencode.ai/console 에서 로그인하세요\n"
        "2. Account → Personal API keys에서 Go 구독 워크스페이스의 개인 API 키를 생성하세요\n"
        "3. API 키를 복사하세요 (브라우저의 'auth' 쿠키가 아닙니다)",
        "1. Sign in at https://opencode.ai/console\n"
        "2. In Account → Personal API keys, create a personal API key for your Go workspace\n"
        "3. Copy the API key (not the browser's 'auth' cookie)",
    )


def clean_key(value: Optional[str]) -> Optional[str]:
    """Accept a key or pasted bearer header, never a browser cookie pair."""
    if not value:
        return None
    text = value.strip().strip("\"'").strip()
    if text.lower().startswith("authorization:"):
        text = text.split(":", 1)[1].strip()
    if text[:7].lower() == "bearer ":
        text = text[7:].strip()
    text = text.rstrip(";").strip().strip("\"'").strip()
    if not re.fullmatch(r"[A-Za-z0-9_-]{20,}", text):
        return None
    return text


def load_key() -> Optional[str]:
    try:
        return keystore.get(KEYSTORE_ACCOUNT)
    except keystore.KeystoreError as exc:
        raise AuthError(str(exc)) from exc


def save_key(value: str) -> None:
    try:
        keystore.set(KEYSTORE_ACCOUNT, value)
    except keystore.KeystoreError as exc:
        raise AuthError(str(exc)) from exc


def delete_key() -> None:
    try:
        keystore.delete(KEYSTORE_ACCOUNT)
    except keystore.KeystoreError as exc:
        raise AuthError(str(exc)) from exc
