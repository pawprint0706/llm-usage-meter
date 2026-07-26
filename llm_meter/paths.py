"""Locating bundled asset files, whether run from a checkout, install, or frozen binary."""

import os
import sys
from typing import Optional


def frozen() -> bool:
    """True when running inside a PyInstaller (or similar) bundle."""
    return bool(getattr(sys, "frozen", False))


def project_dir() -> str:
    """Checkout / install root (source tree). Not meaningful when frozen."""
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def bundle_dir() -> str:
    """Directory that holds packaged data files (assets, etc.)."""
    if frozen():
        # PyInstaller one-file/one-dir extract / _internal dir.
        meipass = getattr(sys, "_MEIPASS", None)
        if meipass:
            return meipass
        return os.path.dirname(os.path.abspath(sys.executable))
    return project_dir()


def executable_path() -> str:
    """Path users (and autostart) should invoke to start this app."""
    if frozen():
        return os.path.abspath(sys.executable)
    return os.path.abspath(sys.argv[0]) if sys.argv else sys.executable


def asset(name: str) -> Optional[str]:
    candidates = (
        os.path.join(bundle_dir(), "assets", name),
        os.path.join(project_dir(), "assets", name),
        os.path.join(sys.prefix, "share", "llm-usage-meter", name),
    )
    return next((path for path in candidates if os.path.isfile(path)), None)
