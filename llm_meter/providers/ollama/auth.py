"""User-supplied Ollama API keys, kept in the OS credential store."""

import re
from typing import Optional

from ... import keystore
from ...i18n import tr

KEYSTORE_ACCOUNT = "ollama-api-key"


class AuthError(Exception):
    """The API key could not be read or stored."""


def instructions() -> str:
    return tr(
        "1. https://ollama.com/settings/keys 에서 API 키를 생성하세요\n"
        "2. 키 값을 복사하세요",
        "1. Create an API key at https://ollama.com/settings/keys\n"
        "2. Copy the key value",
    )


def clean_key(value: Optional[str]) -> Optional[str]:
    if not value:
        return None
    text = value.strip().strip("\"'").strip()
    if text.lower().startswith("authorization:"):
        text = text.split(":", 1)[1].strip()
    if text[:7].lower() == "bearer ":
        text = text[7:].strip()
    text = text.rstrip(";").strip().strip("\"'").strip()
    if not re.fullmatch(r"[A-Za-z0-9._-]{20,}", text):
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
