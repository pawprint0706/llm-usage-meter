"""Secret storage in the OS credential store (Keychain / Credential Manager).

On Windows, Credential Manager caps a single item's size, so values are
compressed and — when still too large — split across several generation-tagged
items with a manifest entry as the commit point. A reader therefore only ever
sees the complete previous value or the complete new one, never a half-written
mix.

On macOS / Linux the credential stores accept large items. We keep a single
service name so Keychain does not re-prompt for a new ACL every time a token is
refreshed (chunk service names embed a random generation id).

macOS Keychain ACLs are per-binary: an item created by the packaged ``.app`` is
not freely readable by ``.venv/bin/python`` (and vice versa). Each denied or
one-shot "Allow" then re-prompts on the next ``get_password``. Providers can
hit the store several times per refresh, which feels like an infinite password
dialog loop. We therefore:

* cache successful reads in-process
* after a user cancel / access denial, latch that account for this process so
  we stop re-prompting until the user explicitly saves or clears the secret
* skip the pre-write ``get`` outside Windows (no chunks to clean up)
"""

import base64
import logging
import secrets
import sys
import threading
import zlib

import keyring

logger = logging.getLogger(__name__)

SERVICE = "LLM Usage Meter"

_COMPRESSED_PREFIX = "z1:"
_MANIFEST_PREFIX = "m1:"
# Windows CRED_TYPE_GENERIC CredentialBlob limit is ~2560 bytes; stay under it.
_CHUNK_SIZE = 900

# Process-local cache / denial latch. Values are decoded secrets; None means
# "known absent". Denied accounts are tracked separately so we do not call into
# Keychain again and re-surface the password dialog.
_lock = threading.Lock()
_cache: dict[str, str | None] = {}
_denied: set[str] = set()


class KeystoreError(Exception):
    """The OS credential store could not be read or written."""


def _chunking_enabled() -> bool:
    return sys.platform == "win32"


def _chunk_service(account: str, generation: str, index: int) -> str:
    return f"{SERVICE} {account} {generation} {index}"


def _is_access_denied(exc: BaseException) -> bool:
    """True when macOS (or another backend) refused interactive keychain access."""
    text = str(exc).lower()
    if "keychain access denied" in text or "user canceled" in text:
        return True
    # errSecUserCanceled (-128), errSecAuthFailed (-25293),
    # errSecInteractionNotAllowed (-25308).
    for code in ("-128", "-25293", "-25308"):
        if code in text:
            return True
    return False


def _manifest(value: str | None) -> tuple[str, int] | None:
    if not value or not value.startswith(_MANIFEST_PREFIX):
        return None
    try:
        _, generation, count_text = value.split(":", 2)
        count = int(count_text)
    except ValueError as exc:
        raise KeystoreError("Stored data is invalid.") from exc
    if not generation or not 1 <= count <= 64:
        raise KeystoreError("Stored data is invalid.")
    return generation, count


def _delete_chunks(account: str, value: str | None) -> None:
    try:
        manifest = _manifest(value)
    except KeystoreError:
        return
    if not manifest:
        return
    generation, count = manifest
    for index in range(count):
        try:
            keyring.delete_password(_chunk_service(account, generation, index), account)
        except keyring.errors.PasswordDeleteError:
            pass
        except Exception:
            logger.warning("Could not remove an obsolete secret chunk", exc_info=True)


def _delete_windows_collision(account: str) -> None:
    """Remove the legacy flat item some Windows keyring backends leave behind."""
    if sys.platform != "win32":
        return
    try:
        keyring.delete_password(f"{account}@{SERVICE}", account)
    except keyring.errors.PasswordDeleteError:
        pass
    except Exception:
        logger.warning("Could not remove the replaced Windows credential", exc_info=True)


def _decode(raw: str) -> str:
    if not raw.startswith(_COMPRESSED_PREFIX):
        return raw
    try:
        packed = base64.b85decode(raw[len(_COMPRESSED_PREFIX) :].encode("ascii"))
        return zlib.decompress(packed).decode("utf-8")
    except (ValueError, UnicodeError, zlib.error) as exc:
        raise KeystoreError("Stored data is invalid.") from exc


def _encode(value: str) -> str:
    return _COMPRESSED_PREFIX + base64.b85encode(
        zlib.compress(value.encode("utf-8"), level=9)
    ).decode("ascii")


def _read_raw(account: str) -> tuple[str | None, bool]:
    """Return ``(payload_or_none, was_chunked)``."""
    raw = keyring.get_password(SERVICE, account)
    manifest = _manifest(raw)
    if not manifest:
        return raw, False
    generation, count = manifest
    chunks = [
        keyring.get_password(_chunk_service(account, generation, index), account)
        for index in range(count)
    ]
    if any(chunk is None for chunk in chunks):
        raise KeystoreError("Stored data is incomplete.")
    return "".join(chunk for chunk in chunks if chunk is not None), True


def _remember(account: str, value: str | None) -> None:
    with _lock:
        _denied.discard(account)
        _cache[account] = value


def _remember_denied(account: str) -> None:
    with _lock:
        _denied.add(account)
        _cache.pop(account, None)


def _cached(account: str) -> tuple[str, str | None]:
    """Return ``(status, value)`` where status is ``miss``, ``hit``, or ``denied``."""
    with _lock:
        if account in _denied:
            return "denied", None
        if account in _cache:
            return "hit", _cache[account]
    return "miss", None


def clear_cache(account: str | None = None) -> None:
    """Drop cached reads / denial latches (tests and explicit re-auth)."""
    with _lock:
        if account is None:
            _cache.clear()
            _denied.clear()
            return
        _cache.pop(account, None)
        _denied.discard(account)


def get(account: str) -> str | None:
    """Read a secret, or None when nothing is stored for `account`."""
    status, cached = _cached(account)
    if status == "denied":
        raise KeystoreError(
            "Could not access the system credential store: "
            "Keychain access was denied earlier in this session. "
            "Quit and reopen the app, then click Always Allow — "
            "or sign in again from this same executable."
        )
    if status == "hit":
        return cached
    try:
        raw, was_chunked = _read_raw(account)
    except KeystoreError:
        raise
    except Exception as exc:
        if _is_access_denied(exc):
            _remember_denied(account)
            logger.warning(
                "Keychain denied access for %s; not prompting again this session",
                account,
            )
        raise KeystoreError(f"Could not access the system credential store: {exc}") from exc
    if not raw:
        _remember(account, None)
        return None
    value = _decode(raw)
    # Legacy macOS installs may still have Windows-style chunks; fold them into
    # one item so Keychain stops prompting for generation-tagged service names.
    if was_chunked and not _chunking_enabled():
        try:
            # Capture the manifest before set() replaces it. set() skips the
            # pre-write get outside Windows, so it would leave orphan chunks.
            previous = keyring.get_password(SERVICE, account)
            set(account, value)
            _delete_chunks(account, previous)
        except KeystoreError:
            logger.warning("Could not migrate chunked secret to a single item", exc_info=True)
            _remember(account, value)
        except Exception:
            logger.warning("Could not migrate chunked secret to a single item", exc_info=True)
            _remember(account, value)
    else:
        _remember(account, value)
    return value


def set(account: str, value: str) -> None:  # noqa: A001 — mirrors keyring's naming
    packed = _encode(value)
    if _chunking_enabled():
        chunks = [packed[i : i + _CHUNK_SIZE] for i in range(0, len(packed), _CHUNK_SIZE)]
    else:
        chunks = [packed]
    generation = secrets.token_hex(8)
    # Outside Windows there are no generation-tagged chunks to clean up, so skip
    # the pre-write get — it would only re-prompt Keychain for the same item.
    previous = None
    try:
        if _chunking_enabled():
            previous = keyring.get_password(SERVICE, account)
        if len(chunks) == 1:
            keyring.set_password(SERVICE, account, packed)
        else:
            for index, chunk in enumerate(chunks):
                keyring.set_password(_chunk_service(account, generation, index), account, chunk)
            keyring.set_password(
                SERVICE, account, f"{_MANIFEST_PREFIX}{generation}:{len(chunks)}"
            )
    except Exception as exc:
        for index in range(len(chunks)):
            try:
                keyring.delete_password(_chunk_service(account, generation, index), account)
            except Exception:
                pass
        if _is_access_denied(exc):
            _remember_denied(account)
        raise KeystoreError(f"Could not save data securely: {exc}") from exc
    _delete_chunks(account, previous)
    _delete_windows_collision(account)
    _remember(account, value)


def delete(account: str) -> None:
    current = None
    try:
        current = keyring.get_password(SERVICE, account)
        keyring.delete_password(SERVICE, account)
    except keyring.errors.PasswordDeleteError:
        current = None
    except Exception as exc:
        if _is_access_denied(exc):
            _remember_denied(account)
        raise KeystoreError(f"Could not remove stored data: {exc}") from exc
    _delete_chunks(account, current)
    clear_cache(account)
