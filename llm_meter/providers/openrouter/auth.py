"""OpenRouter API key handling.

The credits endpoint authenticates API callers with a bearer key. The docs
label it "management key required", yet a regular inference key
(``sk-or-v1-...``) reads the account's own credits just fine — verified live
2026-09 — so the everyday chat key is what users are asked to paste. The key
is supplied by the user and kept in the OS credential store.
"""

import logging
import re
from typing import Optional

from ... import keystore
from ...i18n import tr

logger = logging.getLogger(__name__)

KEYSTORE_ACCOUNT = "openrouter-api-key"

_MIN_KEY_LENGTH = 30

# Both key families share the sk-or- prefix; a key that the API refuses to
# read credits with is rejected with 403, which the provider reports as a
# hint instead of dropping the key.
_KEY_RE = re.compile(r"^sk-or-[A-Za-z0-9_-]+$")


class AuthError(Exception):
    """The API key could not be read or stored."""


def instructions() -> str:
    return tr(
        "1. https://openrouter.ai/settings/keys 에서 API 키를 생성하세요\n"
        "2. 키 값(sk-or-...)을 복사하세요",
        "1. Create an API key at https://openrouter.ai/settings/keys\n"
        "2. Copy the key value (sk-or-...)",
    )


def clean_key(value: Optional[str]) -> Optional[str]:
    """Normalize a pasted management key; None when it cannot be one."""
    if not value:
        return None
    text = value.strip().strip("\"'").strip()
    if text.lower().startswith("authorization:"):
        text = text.split(":", 1)[1].strip()
    if text[:7].lower() == "bearer ":
        text = text[7:].strip()
    text = text.rstrip(";").strip().strip("\"'").strip()
    if not text or any(char.isspace() for char in text):
        return None
    if len(text) < _MIN_KEY_LENGTH:
        return None
    if not _KEY_RE.fullmatch(text):
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
