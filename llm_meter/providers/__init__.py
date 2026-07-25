"""Provider registry.

``PROVIDER_CLASSES`` is the default tab order; the user's ``provider_order``
setting can rearrange it.
"""

from ..config import Config
from .base import (
    ErrorKind,
    MenuEntry,
    Metric,
    Provider,
    ProviderError,
    ProviderUi,
    Section,
    Snapshot,
    State,
)
from .codex.provider import CodexProvider
from .cursor.provider import CursorProvider
from .opencode.provider import OpenCodeProvider

PROVIDER_CLASSES: tuple[type[Provider], ...] = (
    CodexProvider,
    OpenCodeProvider,
    CursorProvider,
)

__all__ = [
    "ErrorKind",
    "MenuEntry",
    "Metric",
    "Provider",
    "ProviderError",
    "ProviderUi",
    "Section",
    "Snapshot",
    "State",
    "PROVIDER_CLASSES",
    "known_provider_ids",
    "build_providers",
]


def known_provider_ids() -> list[str]:
    return [cls.id for cls in PROVIDER_CLASSES]


def build_providers(cfg: Config, ui: ProviderUi) -> list[Provider]:
    instances = {cls.id: cls(cfg, ui) for cls in PROVIDER_CLASSES}
    return [
        instances[provider_id]
        for provider_id in cfg.ordered_provider_ids(known_provider_ids())
        if provider_id in instances
    ]
