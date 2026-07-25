"""Locating bundled asset files, whether run from a checkout or installed."""

import os
import sys
from typing import Optional


def project_dir() -> str:
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def asset(name: str) -> Optional[str]:
    candidates = (
        os.path.join(project_dir(), "assets", name),
        os.path.join(sys.prefix, "share", "llm-usage-meter", name),
    )
    return next((path for path in candidates if os.path.isfile(path)), None)
