"""Korean/English localization helper.

Language resolution order:
  1. ``LLM_METER_LANG`` env var ("ko" | "en") — explicit override, also used by tests
  2. macOS preferred language (Foundation.NSLocale.preferredLanguages)
  3. Windows user UI language (GetUserDefaultUILanguage)
  4. POSIX locale env vars (LANGUAGE / LC_ALL / LC_MESSAGES / LANG)
  5. fallback: English
"""

import logging
import os
import sys

logger = logging.getLogger(__name__)

_detected: str | None = None


def _detect() -> str:
    if sys.platform == "darwin":
        try:
            from Foundation import NSLocale

            languages = NSLocale.preferredLanguages()
            if languages:
                return "ko" if str(languages[0]).lower().startswith("ko") else "en"
        except Exception:
            logger.debug("NSLocale language detection failed", exc_info=True)
    if sys.platform == "win32":
        try:
            import ctypes

            language_id = ctypes.windll.kernel32.GetUserDefaultUILanguage()
            return "ko" if (language_id & 0x3FF) == 0x12 else "en"
        except Exception:
            logger.debug("Windows language detection failed", exc_info=True)
    for name in ("LANGUAGE", "LC_ALL", "LC_MESSAGES", "LANG"):
        value = os.environ.get(name)
        if value:
            return "ko" if value.lower().startswith("ko") else "en"
    return "en"


def current_lang() -> str:
    override = os.environ.get("LLM_METER_LANG")
    if override in ("ko", "en"):
        return override
    global _detected
    if _detected is None:
        _detected = _detect()
    return _detected


def tr(ko: str, en: str) -> str:
    """Return `ko` when the UI language is Korean, else `en`."""
    return ko if current_lang() == "ko" else en
