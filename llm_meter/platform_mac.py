"""macOS-only integration: menu-bar-only app, activation and notifications.

Every function is a no-op on other platforms or when PyObjC is unavailable.
"""

import logging
import sys
from typing import Callable, Optional

logger = logging.getLogger(__name__)

_ACCESSORY_POLICY = 1  # NSApplicationActivationPolicyAccessory


def _shared_app():
    if sys.platform != "darwin":
        return None
    try:
        from AppKit import NSApplication

        return NSApplication.sharedApplication()
    except Exception:
        logger.debug("AppKit is unavailable", exc_info=True)
        return None


def hide_dock_icon() -> None:
    """Run as a menu-bar accessory: no Dock icon, no app menu.

    Accessory rather than Prohibited, so modal dialogs still work.
    """
    app = _shared_app()
    if app is None:
        return
    try:
        app.setActivationPolicy_(_ACCESSORY_POLICY)
    except Exception:
        logger.debug("Could not set the accessory activation policy", exc_info=True)


def activate_app() -> None:
    """Bring our windows forward so a dialog can take keyboard focus."""
    app = _shared_app()
    if app is None:
        return
    try:
        app.activateIgnoringOtherApps_(True)
    except Exception:
        logger.debug("Could not activate the application", exc_info=True)


def monitor_mouse_down(callback: Callable[[], None]) -> Optional[object]:
    """Call ``callback`` for clicks delivered to another macOS application."""
    if sys.platform != "darwin":
        return None
    try:
        from AppKit import (
            NSEvent,
            NSEventMaskLeftMouseDown,
            NSEventMaskOtherMouseDown,
            NSEventMaskRightMouseDown,
        )

        mask = (
            NSEventMaskLeftMouseDown
            | NSEventMaskRightMouseDown
            | NSEventMaskOtherMouseDown
        )
        return NSEvent.addGlobalMonitorForEventsMatchingMask_handler_(
            mask, lambda _event: callback()
        )
    except Exception:
        logger.debug("Could not install the global mouse monitor", exc_info=True)
        return None


def stop_monitor(monitor: Optional[object]) -> None:
    if monitor is None or sys.platform != "darwin":
        return
    try:
        from AppKit import NSEvent

        NSEvent.removeMonitor_(monitor)
    except Exception:
        logger.debug("Could not remove the global mouse monitor", exc_info=True)


def notify(title: str, message: str) -> bool:
    """Post a banner owned by this process. True when delivered.

    Qt's own tray notification shells out to osascript, whose banner belongs to
    Script Editor — clicking it would launch that app. Delivering through
    NSUserNotificationCenter makes us the owner, so a click just dismisses it.
    """
    if sys.platform != "darwin":
        return False
    try:
        from Foundation import NSUserNotification, NSUserNotificationCenter

        center = NSUserNotificationCenter.defaultUserNotificationCenter()
        if center is None:
            return False
        note = NSUserNotification.alloc().init()
        note.setTitle_(title)
        note.setInformativeText_(message)
        center.deliverNotification_(note)
        return True
    except Exception:
        logger.debug("Native notification failed", exc_info=True)
        return False
