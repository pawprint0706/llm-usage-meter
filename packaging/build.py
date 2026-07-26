#!/usr/bin/env python3
"""Build a platform-native standalone binary with PyInstaller.

Usage (from the repo root, inside a venv that has the app deps + pyinstaller):

    python packaging/build.py

Artifacts are written under ``dist/``.
"""

from __future__ import annotations

import argparse
import platform
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SPEC = Path(__file__).resolve().parent / "llm_usage_meter.spec"
DIST = ROOT / "dist"
BUILD = ROOT / "build"


def main() -> int:
    parser = argparse.ArgumentParser(description="Build LLM Usage Meter standalone binary")
    parser.add_argument(
        "--clean",
        action="store_true",
        help="remove previous build/ and dist/ before building",
    )
    args = parser.parse_args()

    if args.clean:
        for path in (BUILD, DIST):
            if path.exists():
                shutil.rmtree(path)
                print(f"Removed {path}")

    cmd = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--noconfirm",
        "--clean",
        str(SPEC),
    ]
    print("+", " ".join(cmd))
    subprocess.check_call(cmd, cwd=ROOT)

    system = platform.system()
    if system == "Darwin":
        artifact = DIST / "LLM Usage Meter.app"
    elif system == "Windows":
        artifact = DIST / "llm-usage-meter.exe"
    else:
        artifact = DIST / "llm-usage-meter"

    if not artifact.exists():
        print(f"ERROR: expected artifact missing: {artifact}", file=sys.stderr)
        return 1

    print(f"Built: {artifact}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
