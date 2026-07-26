"""Register/unregister start-at-login for the current user (no admin rights).

Per platform:
  - macOS:   ~/Library/LaunchAgents/<label>.plist (RunAtLoad). Writing the
             file is enough; launchd loads it at the next login. We never
             bootstrap it immediately -- the app is already running and
             would end up with two tray icons.
  - Windows: HKCU\\Software\\Microsoft\\Windows\\CurrentVersion\\Run value
             pointing at the frozen exe, or pythonw.exe + launch.py.
  - Linux:   XDG autostart .desktop file.

Entries embed absolute paths, so ``refresh_if_stale()`` rewrites them at
startup in case the binary or project folder moved.
"""

import logging
import os
import platform
import subprocess
import sys

from . import paths

logger = logging.getLogger(__name__)

APP_LABEL = "local.llm-usage-meter"
APP_NAME = "LLM Usage Meter"

_SYSTEM = platform.system()
_RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
_RUN_VALUE = "LlmUsageMeter"


def _project_dir() -> str:
    return paths.project_dir()


def _launcher() -> str:
    return os.path.join(_project_dir(), "launch.py")


def _python() -> str:
    executable = sys.executable
    if _SYSTEM == "Windows":
        pythonw = os.path.join(os.path.dirname(executable), "pythonw.exe")
        if os.path.exists(pythonw):
            return pythonw
    return executable


def _command() -> list[str]:
    if paths.frozen():
        return [paths.executable_path()]
    return [_python(), _launcher()]


def _workdir() -> str:
    if paths.frozen():
        return os.path.dirname(paths.executable_path())
    return _project_dir()


def _plist_path() -> str:
    return os.path.expanduser(f"~/Library/LaunchAgents/{APP_LABEL}.plist")


def _plist_dict() -> dict:
    from . import config

    log_path = os.path.join(config.config_dir(), "launchd.log")
    return {
        "Label": APP_LABEL,
        "ProgramArguments": _command(),
        "RunAtLoad": True,
        "WorkingDirectory": _workdir(),
        "StandardOutPath": log_path,
        "StandardErrorPath": log_path,
    }


def _registry_command() -> str:
    parts = _command()
    return " ".join(f'"{part}"' for part in parts)


def _read_registry():
    import winreg

    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _RUN_KEY, 0, winreg.KEY_READ) as key:
            return winreg.QueryValueEx(key, _RUN_VALUE)[0]
    except FileNotFoundError:
        return None


def _desktop_path() -> str:
    return os.path.expanduser("~/.config/autostart/llm-usage-meter.desktop")


def _desktop_content() -> str:
    parts = _command()
    exec_line = " ".join(f'"{part}"' for part in parts)
    return (
        "[Desktop Entry]\n"
        "Type=Application\n"
        f"Name={APP_NAME}\n"
        f"Exec={exec_line}\n"
        "X-GNOME-Autostart-enabled=true\n"
    )


def is_enabled() -> bool:
    try:
        if _SYSTEM == "Darwin":
            return os.path.exists(_plist_path())
        if _SYSTEM == "Windows":
            return _read_registry() is not None
        return os.path.exists(_desktop_path())
    except Exception:
        logger.exception("Could not check the start-at-login state")
        return False


def enable() -> None:
    """Register start-at-login; takes effect at the next login."""
    if _SYSTEM == "Darwin":
        import plistlib

        path = _plist_path()
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as handle:
            plistlib.dump(_plist_dict(), handle)
    elif _SYSTEM == "Windows":
        import winreg

        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
            winreg.SetValueEx(key, _RUN_VALUE, 0, winreg.REG_SZ, _registry_command())
    else:
        path = _desktop_path()
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(_desktop_content())
    logger.info("Start-at-login enabled")


def disable() -> None:
    if _SYSTEM == "Darwin":
        try:
            os.unlink(_plist_path())
        except FileNotFoundError:
            pass
        # If launchd loaded it in this session, unload quietly too.
        try:
            subprocess.run(
                ["launchctl", "bootout", f"gui/{os.getuid()}/{APP_LABEL}"],
                capture_output=True,
                timeout=10,
            )
        except (OSError, subprocess.TimeoutExpired):
            pass
    elif _SYSTEM == "Windows":
        import winreg

        try:
            with winreg.OpenKey(
                winreg.HKEY_CURRENT_USER, _RUN_KEY, 0, winreg.KEY_SET_VALUE
            ) as key:
                winreg.DeleteValue(key, _RUN_VALUE)
        except FileNotFoundError:
            pass
    else:
        try:
            os.unlink(_desktop_path())
        except FileNotFoundError:
            pass
    logger.info("Start-at-login disabled")


def toggle() -> bool:
    """Flip the setting and return the new state."""
    if is_enabled():
        disable()
        return False
    enable()
    return True


def refresh_if_stale() -> None:
    """Rewrite the autostart entry if the binary or project folder moved."""
    try:
        if not is_enabled():
            return
        if _SYSTEM == "Darwin":
            import plistlib

            with open(_plist_path(), "rb") as handle:
                current = plistlib.load(handle).get("ProgramArguments")
            stale = current != _command()
        elif _SYSTEM == "Windows":
            stale = _read_registry() != _registry_command()
        else:
            try:
                with open(_desktop_path(), "r", encoding="utf-8") as handle:
                    stale = handle.read() != _desktop_content()
            except OSError:
                stale = True
        if stale:
            logger.info("Start-at-login entry is stale; rewriting with current paths")
            enable()
    except Exception:
        logger.exception("Could not refresh the start-at-login registration")
