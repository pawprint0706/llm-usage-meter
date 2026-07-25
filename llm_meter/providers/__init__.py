"""Provider registry.

Order here is the order the cards appear in the popup.
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
from .opencode.provider import OpenCodeProvider

PROVIDER_CLASSES: tuple[type[Provider], ...] = (CodexProvider, OpenCodeProvider)

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
    "build_providers",
]


def build_providers(cfg: Config, ui: ProviderUi) -> list[Provider]:
    return [cls(cfg, ui) for cls in PROVIDER_CLASSES]
