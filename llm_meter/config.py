"""Non-secret application settings.

Secrets (OAuth tokens, session keys) live in the OS credential store, see
``keystore``. This file only holds preferences and cheap cached lookups such
as the discovered OpenCode workspace ID.
"""

import json
import logging
import os
import threading
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

_write_lock = threading.Lock()

APP_DIR_NAME = ".llm-usage-meter"
REFRESH_OPTIONS = (5, 10, 30, 60)
DEFAULT_REFRESH_INTERVAL = 10


@dataclass
class Config:
    refresh_interval: int = DEFAULT_REFRESH_INTERVAL
    providers: dict[str, dict[str, Any]] = field(default_factory=dict)

    def provider(self, provider_id: str) -> dict[str, Any]:
        """Per-provider settings bag, created on first access."""
        return self.providers.setdefault(provider_id, {})

    def is_provider_enabled(self, provider_id: str) -> bool:
        return bool(self.provider(provider_id).get("enabled", True))

    def set_provider_enabled(self, provider_id: str, enabled: bool) -> None:
        self.provider(provider_id)["enabled"] = bool(enabled)


def config_dir() -> str:
    path = os.path.join(os.path.expanduser("~"), APP_DIR_NAME)
    os.makedirs(path, mode=0o700, exist_ok=True)
    return path


def config_path() -> str:
    return os.path.join(config_dir(), "config.json")


def load_config() -> Config:
    try:
        with open(config_path(), "r", encoding="utf-8") as handle:
            data = json.load(handle)
    except FileNotFoundError:
        return Config()
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        logger.warning("Config file unreadable (%s); starting with defaults", exc)
        return Config()
    if not isinstance(data, dict):
        return Config()

    try:
        interval = int(data.get("refresh_interval", DEFAULT_REFRESH_INTERVAL))
    except (TypeError, ValueError):
        interval = DEFAULT_REFRESH_INTERVAL
    if interval not in REFRESH_OPTIONS:
        interval = DEFAULT_REFRESH_INTERVAL

    raw_providers = data.get("providers")
    providers: dict[str, dict[str, Any]] = {}
    if isinstance(raw_providers, dict):
        for key, value in raw_providers.items():
            if isinstance(key, str) and isinstance(value, dict):
                providers[key] = value

    return Config(refresh_interval=interval, providers=providers)


def save_config(cfg: Config) -> None:
    """Atomically write the config with owner-only permissions.

    Serialized, because provider workers save from their own threads.
    """
    path = config_path()
    payload = {"refresh_interval": cfg.refresh_interval, "providers": cfg.providers}
    temp_path = path + ".tmp"
    with _write_lock:
        descriptor = os.open(temp_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, indent=2, ensure_ascii=False)
            os.replace(temp_path, path)
        except OSError:
            try:
                os.unlink(temp_path)
            except OSError:
                pass
            raise
