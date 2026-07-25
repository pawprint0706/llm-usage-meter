"""Test package.

The UI language is pinned to English so assertions can compare literal strings
regardless of the machine's locale.
"""

import logging
import os

os.environ.setdefault("LLM_METER_LANG", "en")

# Several tests exercise error paths on purpose; their warnings are not failures.
logging.disable(logging.WARNING)
