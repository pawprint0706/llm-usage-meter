"""OpenAI device-code OAuth and credential storage for Codex.

Uses the same public client ID and device-code flow as the Codex CLI, but never
reads or writes the CLI's ``auth.json`` — tokens live in the OS credential store
(see ``llm_meter.keystore``).
"""

import base64
import json
import logging
import threading
import time
import webbrowser
from dataclasses import asdict, dataclass
from typing import Callable, Optional

import requests

from ... import keystore

logger = logging.getLogger(__name__)

ISSUER = "https://auth.openai.com"
CLIENT_ID = "app_EMoamEEZ73f0CkXaXp7hrann"
KEYSTORE_ACCOUNT = "codex-oauth"
REFRESH_AFTER_SECONDS = 8 * 24 * 60 * 60
LOGIN_TIMEOUT_SECONDS = 15 * 60

_refresh_lock = threading.Lock()


class AuthError(Exception):
    """Authentication failed or needs user action."""


class LoginCancelled(AuthError):
    """The login wait was cancelled by the application."""


@dataclass
class Credentials:
    access_token: str
    refresh_token: str
    id_token: Optional[str]
    account_id: Optional[str]
    email: Optional[str]
    plan_type: Optional[str]
    refreshed_at: float


@dataclass
class DeviceCode:
    verification_url: str
    user_code: str
    device_auth_id: str
    interval: int


def _decode_jwt_payload(token: Optional[str]) -> dict:
    if not token:
        return {}
    try:
        payload = token.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        return json.loads(base64.urlsafe_b64decode(payload).decode("utf-8"))
    except (IndexError, ValueError, UnicodeDecodeError, json.JSONDecodeError):
        return {}


def _identity(id_token: Optional[str]) -> tuple[Optional[str], Optional[str], Optional[str]]:
    claims = _decode_jwt_payload(id_token)
    auth_claims = claims.get("https://api.openai.com/auth") or {}
    profile = claims.get("https://api.openai.com/profile") or {}
    return (
        auth_claims.get("chatgpt_account_id"),
        claims.get("email") or profile.get("email"),
        auth_claims.get("chatgpt_plan_type"),
    )


def load_credentials() -> Optional[Credentials]:
    try:
        raw = keystore.get(KEYSTORE_ACCOUNT)
    except keystore.KeystoreError as exc:
        raise AuthError(str(exc)) from exc
    if not raw:
        return None
    try:
        credentials = Credentials(**json.loads(raw))
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise AuthError("Stored login data is invalid. Sign out and sign in again.") from exc
    if (
        not isinstance(credentials.access_token, str)
        or not credentials.access_token
        or not isinstance(credentials.refresh_token, str)
        or not credentials.refresh_token
        or isinstance(credentials.refreshed_at, bool)
        or not isinstance(credentials.refreshed_at, (int, float))
    ):
        raise AuthError("Stored login data is invalid. Sign out and sign in again.")
    return credentials


def save_credentials(credentials: Credentials) -> None:
    try:
        keystore.set(KEYSTORE_ACCOUNT, json.dumps(asdict(credentials), separators=(",", ":")))
    except keystore.KeystoreError as exc:
        raise AuthError(str(exc)) from exc


def delete_credentials() -> None:
    try:
        keystore.delete(KEYSTORE_ACCOUNT)
    except keystore.KeystoreError as exc:
        raise AuthError(str(exc)) from exc


def request_device_code(session: Optional[requests.Session] = None) -> DeviceCode:
    client = session or requests.Session()
    response = client.post(
        f"{ISSUER}/api/accounts/deviceauth/usercode",
        json={"client_id": CLIENT_ID},
        timeout=15,
    )
    if response.status_code != 200:
        raise AuthError(f"Device login request failed (HTTP {response.status_code}).")
    try:
        data = response.json()
        return DeviceCode(
            verification_url=f"{ISSUER}/codex/device",
            user_code=data.get("user_code") or data["usercode"],
            device_auth_id=data["device_auth_id"],
            interval=max(1, int(data.get("interval", 5))),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise AuthError("OpenAI returned an invalid device login response.") from exc


def _exchange_code(
    authorization_code: str, code_verifier: str, session: requests.Session
) -> Credentials:
    response = session.post(
        f"{ISSUER}/oauth/token",
        data={
            "grant_type": "authorization_code",
            "code": authorization_code,
            "redirect_uri": f"{ISSUER}/deviceauth/callback",
            "client_id": CLIENT_ID,
            "code_verifier": code_verifier,
        },
        timeout=20,
    )
    if response.status_code != 200:
        raise AuthError(f"Token exchange failed (HTTP {response.status_code}).")
    try:
        data = response.json()
        account_id, email, plan_type = _identity(data.get("id_token"))
        return Credentials(
            access_token=data["access_token"],
            refresh_token=data["refresh_token"],
            id_token=data.get("id_token"),
            account_id=account_id,
            email=email,
            plan_type=plan_type,
            refreshed_at=time.time(),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise AuthError("OpenAI returned an invalid token response.") from exc


def complete_device_login(
    device_code: DeviceCode,
    should_continue: Callable[[], bool] = lambda: True,
    session: Optional[requests.Session] = None,
    timeout: int = LOGIN_TIMEOUT_SECONDS,
) -> Credentials:
    client = session or requests.Session()
    deadline = time.monotonic() + timeout
    endpoint = f"{ISSUER}/api/accounts/deviceauth/token"
    while time.monotonic() < deadline:
        if not should_continue():
            raise LoginCancelled("Login was cancelled.")
        response = client.post(
            endpoint,
            json={
                "device_auth_id": device_code.device_auth_id,
                "user_code": device_code.user_code,
            },
            timeout=15,
        )
        if response.status_code == 200:
            try:
                data = response.json()
                logger.info("Device login approved; exchanging the authorization code")
                credentials = _exchange_code(
                    data["authorization_code"], data["code_verifier"], client
                )
            except (KeyError, TypeError, ValueError) as exc:
                raise AuthError("OpenAI returned an invalid login response.") from exc
            save_credentials(credentials)
            logger.info("OAuth credentials saved to the system credential store")
            return credentials
        if response.status_code not in (403, 404):
            raise AuthError(f"Device login failed (HTTP {response.status_code}).")
        time.sleep(min(device_code.interval, max(0, deadline - time.monotonic())))
    raise AuthError("Device login timed out after 15 minutes.")


def login(
    on_code: Callable[[DeviceCode], None],
    should_continue: Callable[[], bool] = lambda: True,
) -> Credentials:
    code = request_device_code()
    on_code(code)
    webbrowser.open(code.verification_url, new=2)
    return complete_device_login(code, should_continue=should_continue)


def _token_expires_soon(access_token: str) -> bool:
    expiration = _decode_jwt_payload(access_token).get("exp")
    return isinstance(expiration, (int, float)) and expiration <= time.time() + 300


def refresh_credentials(
    credentials: Credentials, session: Optional[requests.Session] = None
) -> Credentials:
    client = session or requests
    response = client.post(
        f"{ISSUER}/oauth/token",
        json={
            "client_id": CLIENT_ID,
            "grant_type": "refresh_token",
            "refresh_token": credentials.refresh_token,
        },
        timeout=20,
    )
    if response.status_code != 200:
        raise AuthError("The OpenAI login expired. Sign in again.")
    try:
        data = response.json()
        access_token = data["access_token"]
        if not isinstance(access_token, str) or not access_token:
            raise ValueError
        id_token = data.get("id_token", credentials.id_token)
        account_id, email, plan_type = _identity(id_token)
        updated = Credentials(
            access_token=access_token,
            refresh_token=data.get("refresh_token", credentials.refresh_token),
            id_token=id_token,
            account_id=account_id or credentials.account_id,
            email=email or credentials.email,
            plan_type=plan_type or credentials.plan_type,
            refreshed_at=time.time(),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise AuthError("OpenAI returned an invalid refresh response.") from exc
    if not isinstance(updated.refresh_token, str) or not updated.refresh_token:
        raise AuthError("OpenAI returned an invalid refresh response.")
    save_credentials(updated)
    return updated


def valid_credentials(force_refresh: bool = False) -> Optional[Credentials]:
    with _refresh_lock:
        credentials = load_credentials()
        if credentials is None:
            return None
        stale = time.time() - credentials.refreshed_at >= REFRESH_AFTER_SECONDS
        if force_refresh or stale or _token_expires_soon(credentials.access_token):
            return refresh_credentials(credentials)
        return credentials
