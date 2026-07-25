"""Provider framework: the contract every monitored service implements.

A provider owns its own authentication, HTTP calls and result shaping. The app
only knows about the neutral value objects below, so adding a service means
adding one package under ``llm_meter/providers`` — no UI changes.

Threading contract
------------------
``fetch()`` and menu entries with ``background=True`` run on a worker thread and
may only touch the fire-and-forget helpers on :class:`ProviderUi`. Menu entries
with ``background=False`` run on the GUI thread and may additionally use the
blocking helpers (clipboard read, text prompt).
"""

import logging
import threading
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Callable, Optional, Protocol

from ..config import Config
from ..i18n import tr

logger = logging.getLogger(__name__)


class State(Enum):
    SIGNED_OUT = "signed_out"
    LOADING = "loading"
    READY = "ready"
    ERROR = "error"


class ErrorKind(Enum):
    AUTH = "auth"
    NETWORK = "network"
    DATA = "data"
    UNKNOWN = "unknown"


class ProviderError(Exception):
    """A failure a provider can describe to the user."""

    def __init__(self, message: str, kind: ErrorKind = ErrorKind.UNKNOWN):
        super().__init__(message)
        self.kind = kind


@dataclass
class Metric:
    """One measured value, optionally with a usage bar and a trailing note."""

    label: str
    value: str = ""
    percent: Optional[float] = None
    detail: Optional[str] = None
    muted: bool = False


@dataclass
class Section:
    """A titled group of metrics, linking to the matching web page."""

    title: str
    metrics: list[Metric] = field(default_factory=list)
    url: Optional[str] = None
    note: Optional[str] = None
    empty_text: Optional[str] = None


@dataclass
class Snapshot:
    sections: list[Section] = field(default_factory=list)
    tooltip: str = ""
    badge: Optional[str] = None
    gauge_percent: Optional[float] = None


class ProviderUi(Protocol):
    """Host services a provider may call. See the threading contract above."""

    def changed(self, provider: "Provider") -> None:
        """Repaint this provider's card (fire-and-forget, any thread)."""

    def copy_to_clipboard(self, text: str) -> None:
        """Fire-and-forget, any thread."""

    def open_url(self, url: str) -> None:
        """Fire-and-forget, any thread."""

    def notify(self, message: str) -> None:
        """Post a desktop notification. Fire-and-forget, any thread."""

    def request_refresh(self, provider: "Provider") -> None:
        """Queue a background refresh for this provider. Any thread."""

    def clipboard_text(self) -> Optional[str]:
        """GUI thread only."""

    def ask_text(self, title: str, prompt: str, secret: bool = False) -> Optional[str]:
        """Modal single-line input. GUI thread only."""


@dataclass
class MenuEntry:
    label: str = ""
    run: Optional[Callable[[], None]] = None
    background: bool = True
    separator: bool = False
    enabled: bool = True
    checked: Optional[bool] = None

    @classmethod
    def sep(cls) -> "MenuEntry":
        return cls(separator=True)


class Provider:
    """Base class handling the refresh lifecycle and state bookkeeping."""

    id: str = ""
    name: str = ""
    accent: str = "#8B8FA3"

    def __init__(self, cfg: Config, ui: ProviderUi):
        self.cfg = cfg
        self.ui = ui
        self.settings = cfg.provider(self.id)
        self.state = State.SIGNED_OUT
        self.message = ""
        self.snapshot: Optional[Snapshot] = None
        self.data = None
        self.fetched_at: Optional[float] = None
        self._alive = True
        self._fetching = threading.Lock()

    # ------------------------------------------------------------- subclasses

    def is_authenticated(self) -> bool:
        raise NotImplementedError

    def load(self):
        """Retrieve raw provider data. Worker thread; raises :class:`ProviderError`."""
        raise NotImplementedError

    def render(self, data) -> Snapshot:
        """Turn raw data into display values. Called again on every UI tick, so
        relative countdowns stay current without another network round trip."""
        raise NotImplementedError

    def menu(self) -> list[MenuEntry]:
        return []

    def primary_action(self) -> Optional[MenuEntry]:
        """The sign-in entry a signed-out card offers as a button."""
        return None

    def on_auth_error(self) -> None:
        """Hook for providers that must drop a rejected credential."""

    def signed_out_hint(self) -> str:
        return tr("로그인이 필요합니다", "Sign-in required")

    # ------------------------------------------------------------- lifecycle

    @property
    def enabled(self) -> bool:
        return self.cfg.is_provider_enabled(self.id)

    @property
    def alive(self) -> bool:
        """False once the app is shutting down; long polls must give up."""
        return self._alive

    def shutdown(self) -> None:
        self._alive = False

    @property
    def sections(self) -> list[Section]:
        return self.snapshot.sections if self.snapshot else []

    def changed(self) -> None:
        self.ui.changed(self)

    def set_message(self, message: str) -> None:
        self.message = message
        self.changed()

    def reset(self, message: str = "") -> None:
        """Return to the signed-out state, discarding cached data."""
        self.snapshot = None
        self.data = None
        self.fetched_at = None
        self.state = State.SIGNED_OUT
        self.message = message
        self.changed()

    def rebuild(self) -> None:
        """Re-render cached data so countdowns stay current between fetches."""
        if self.data is None or self.state is not State.READY:
            return
        try:
            self.snapshot = self.render(self.data)
        except Exception:
            logger.exception("Could not re-render %s", self.id)
            return
        self.changed()

    def refresh(self) -> None:
        """Fetch and store a snapshot. Worker thread; never raises.

        Concurrent calls are dropped rather than queued: a second in-flight
        request would only race the first one to the same result.
        """
        if not self.enabled:
            return
        if not self._fetching.acquire(blocking=False):
            return
        try:
            self._refresh_locked()
        finally:
            self._fetching.release()

    def _refresh_locked(self) -> None:
        if not self.is_authenticated():
            self.state = State.SIGNED_OUT
            self.message = ""
            self.changed()
            return
        self.state = State.LOADING
        self.changed()
        try:
            data = self.load()
            snapshot = self.render(data)
        except ProviderError as exc:
            self._apply_error(exc)
        except Exception as exc:  # noqa: BLE001 — a provider bug must not kill the app
            logger.exception("Unexpected error refreshing %s", self.id)
            self.state = State.ERROR
            self.message = tr("예기치 않은 오류 · 로그 확인", "Unexpected error · see log")
            logger.debug("cause: %s", exc)
        else:
            self.data = data
            self.snapshot = snapshot
            self.fetched_at = time.time()
            self.state = State.READY
            self.message = ""
        self.changed()

    def _apply_error(self, exc: ProviderError) -> None:
        logger.warning("%s refresh failed (%s): %s", self.id, exc.kind.value, exc)
        if exc.kind is ErrorKind.AUTH:
            self.on_auth_error()
            if not self.is_authenticated():
                self.state = State.SIGNED_OUT
                self.snapshot = None
                self.fetched_at = None
            else:
                self.state = State.ERROR
            self.message = str(exc)
            return
        self.state = State.ERROR
        self.message = str(exc)
