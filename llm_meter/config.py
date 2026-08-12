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
REFRESH_OPTIONS = (10, 30, 60)
DEFAULT_REFRESH_INTERVAL = 10


@dataclass
class Config:
    refresh_interval: int = DEFAULT_REFRESH_INTERVAL
    provider_order: list[str] = field(default_factory=list)
    providers: dict[str, dict[str, Any]] = field(default_factory=dict)

    def provider(self, provider_id: str) -> dict[str, Any]:
        """Per-provider settings bag, created on first access."""
        return self.providers.setdefault(provider_id, {})

    def is_provider_enabled(self, provider_id: str) -> bool:
        return bool(self.provider(provider_id).get("enabled", True))

    def set_provider_enabled(self, provider_id: str, enabled: bool) -> None:
        self.provider(provider_id)["enabled"] = bool(enabled)

    def ordered_provider_ids(self, known_ids: list[str]) -> list[str]:
        """Saved order first, then any newly registered ids in registry order."""
        known = list(dict.fromkeys(known_ids))
        known_set = set(known)
        ordered = [provider_id for provider_id in self.provider_order if provider_id in known_set]
        for provider_id in known:
            if provider_id not in ordered:
                ordered.append(provider_id)
        return ordered

    def move_provider(self, provider_id: str, delta: int, known_ids: list[str]) -> bool:
        """Swap ``provider_id`` with its neighbour. Returns False if it cannot move."""
        order = self.ordered_provider_ids(known_ids)
        try:
            index = order.index(provider_id)
        except ValueError:
            return False
        target = index + delta
        if target < 0 or target >= len(order):
            return False
        order[index], order[target] = order[target], order[index]
        self.provider_order = order
        return True


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

    provider_order: list[str] = []
    raw_order = data.get("provider_order")
    if isinstance(raw_order, list):
        for item in raw_order:
            if isinstance(item, str) and item and item not in provider_order:
                provider_order.append(item)

    return Config(
        refresh_interval=interval,
        provider_order=provider_order,
        providers=providers,
    )


def save_config(cfg: Config) -> None:
    """Atomically write the config with owner-only permissions.

    Serialized, because provider workers save from their own threads.
    """
    path = config_path()
    payload = {
        "refresh_interval": cfg.refresh_interval,
        "provider_order": cfg.provider_order,
        "providers": cfg.providers,
    }
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
