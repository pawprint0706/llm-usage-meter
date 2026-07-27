"""LLM Usage Meter entry point."""

import argparse
import logging
import os
import shutil
import sys


def _handlers() -> list[logging.Handler]:
    from . import config

    handlers: list[logging.Handler] = []
    # Under pythonw.exe there is no console and sys.stderr is None.
    if sys.stderr is not None:
        handlers.append(logging.StreamHandler(sys.stderr))
    try:
        handlers.append(
            logging.FileHandler(os.path.join(config.config_dir(), "app.log"), encoding="utf-8")
        )
    except OSError:
        pass
    return handlers


def _uninstall() -> None:
    from . import autostart, config, keystore
    from .providers.codex import auth as codex_auth
    from .providers.cursor import auth as cursor_auth
    from .providers.opencode import auth as opencode_auth

    autostart.disable()
    for remove in (
        codex_auth.delete_credentials,
        opencode_auth.delete_session_key,
        cursor_auth.delete_pasted_session_token,
    ):
        try:
            remove()
        except Exception:  # noqa: BLE001 — best effort, keep removing
            logging.getLogger(__name__).warning("Could not remove a secret", exc_info=True)
    logging.getLogger(__name__).info("Removed secrets from %s", keystore.SERVICE)
    shutil.rmtree(config.config_dir(), ignore_errors=True)


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="llm-usage-meter",
        description="Tray monitor for Codex and OpenCode usage.",
    )
    actions = parser.add_mutually_exclusive_group()
    actions.add_argument("--stop", action="store_true", help="stop the running instance and exit")
    actions.add_argument(
        "--replace", action="store_true", help="stop the running instance before starting"
    )
    actions.add_argument(
        "--uninstall",
        action="store_true",
        help="stop the app and remove autostart, stored logins and app data",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
        handlers=_handlers(),
    )
    log = logging.getLogger(__name__)

    from . import instance

    if args.uninstall:
        if not instance.stop_running():
            parser.error("could not stop the running LLM Usage Meter instance")
        _uninstall()
        return

    if args.stop:
        stopped = instance.stop_running()
        log.info("Stop requested: %s", "done" if stopped else "FAILED")
        sys.exit(0 if stopped else 1)

    if args.replace and not instance.stop_running():
        log.error("Could not stop the running instance; aborting")
        sys.exit(1)

    lock = instance.acquire_lock()
    if lock is None:
        log.warning("LLM Usage Meter is already running; exiting.")
        return

    from PySide6.QtGui import QIcon
    from PySide6.QtWidgets import QApplication, QSystemTrayIcon

    from . import paths
    from .app import APP_TITLE, MeterApp

    qt_app = QApplication(sys.argv)
    qt_app.setApplicationName(APP_TITLE)
    qt_app.setApplicationDisplayName(APP_TITLE)
    qt_app.setQuitOnLastWindowClosed(False)
    for name in ("app-icon.png", "app-icon.ico", "app-icon.icns"):
        icon_path = paths.asset(name)
        if icon_path:
            qt_app.setWindowIcon(QIcon(icon_path))
            break
    if not QSystemTrayIcon.isSystemTrayAvailable():
        log.error("No system tray is available on this desktop session")
        sys.exit(1)

    app = MeterApp(qt_app)
    app.start()
    try:
        sys.exit(qt_app.exec())
    except KeyboardInterrupt:
        app.quit()


if __name__ == "__main__":
    main()
