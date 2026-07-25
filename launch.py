#!/usr/bin/env python3
"""Launcher used by the autostart entries (LaunchAgent / registry Run key).

Inserts the project directory into sys.path so ``llm_meter`` imports work
regardless of the working directory launchd or Explorer starts us in.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from llm_meter.__main__ import main  # noqa: E402

if __name__ == "__main__":
    main()
