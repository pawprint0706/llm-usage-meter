"""Shared test helpers: dependency stubs and a recording ProviderUi."""

import sys
import types
from typing import Optional
from unittest.mock import Mock


def install_keyring_stub() -> None:
    """Let the credential-store tests run on a machine without keyring."""
    try:
        import keyring  # noqa: F401
    except ModuleNotFoundError:
        keyring = types.ModuleType("keyring")
        keyring.errors = types.SimpleNamespace(
            KeyringError=type("KeyringError", (Exception,), {}),
            PasswordDeleteError=type("PasswordDeleteError", (Exception,), {}),
        )
        keyring.get_password = Mock()
        keyring.set_password = Mock()
        keyring.delete_password = Mock()
        sys.modules["keyring"] = keyring


class FakeKeyring:
    """In-memory stand-in for the OS credential store.

    ``limit`` mimics a backend that rejects long values, which is what forces
    the keystore to split a secret across several entries.
    """

    def __init__(self, limit: Optional[int] = None):
        self.entries: dict[tuple[str, str], str] = {}
        self.limit = limit
        import keyring.errors

        self.errors = keyring.errors

    def get_password(self, service: str, account: str) -> Optional[str]:
        return self.entries.get((service, account))

    def set_password(self, service: str, account: str, value: str) -> None:
        if self.limit is not None and len(value) > self.limit:
            raise RuntimeError("value too long for this backend")
        self.entries[(service, account)] = value

    def delete_password(self, service: str, account: str) -> None:
        if (service, account) not in self.entries:
            raise self.errors.PasswordDeleteError("not found")
        del self.entries[(service, account)]


class FakeUi:
    """Records what a provider asked the host to do."""

    def __init__(self, clipboard: Optional[str] = None, answer: Optional[str] = None):
        self.clipboard = clipboard
        self.answer = answer
        self.answers: list[Optional[str]] = []
        self.changed_count = 0
        self.copied: list[str] = []
        self.opened: list[str] = []
        self.notified: list[str] = []
        self.refresh_requests: list[str] = []
        self.prompts: list[tuple[str, str, bool]] = []

    def changed(self, provider) -> None:
        self.changed_count += 1

    def copy_to_clipboard(self, text: str) -> None:
        self.copied.append(text)

    def open_url(self, url: str) -> None:
        self.opened.append(url)

    def notify(self, message: str) -> None:
        self.notified.append(message)

    def request_refresh(self, provider) -> None:
        self.refresh_requests.append(provider.id)

    def clipboard_text(self) -> Optional[str]:
        return self.clipboard

    def ask_text(self, title: str, prompt: str, secret: bool = False) -> Optional[str]:
        self.prompts.append((title, prompt, secret))
        if self.answers:
            return self.answers.pop(0)
        return self.answer
