"""Tests for asset / executable path helpers (source and frozen layouts)."""

import os
import sys
import tempfile
import unittest
from unittest.mock import patch

from llm_meter import paths


class PathsTests(unittest.TestCase):
    def test_asset_finds_checkout_file(self):
        found = paths.asset("cursor-cube.svg")
        self.assertIsNotNone(found)
        self.assertTrue(os.path.isfile(found))

    def test_asset_missing_returns_none(self):
        self.assertIsNone(paths.asset("no-such-asset.bin"))

    def test_frozen_bundle_dir_uses_meipass(self):
        with tempfile.TemporaryDirectory() as tmp:
            assets = os.path.join(tmp, "assets")
            os.makedirs(assets)
            target = os.path.join(assets, "cursor-cube.svg")
            with open(target, "w", encoding="utf-8") as handle:
                handle.write("<svg/>")
            with patch.object(paths, "frozen", return_value=True), patch.object(
                sys, "_MEIPASS", tmp, create=True
            ):
                self.assertEqual(paths.bundle_dir(), tmp)
                self.assertEqual(paths.asset("cursor-cube.svg"), target)

    def test_frozen_executable_path(self):
        with patch.object(paths, "frozen", return_value=True), patch.object(
            sys, "executable", "/Applications/LLM Usage Meter.app/Contents/MacOS/llm-usage-meter"
        ):
            self.assertEqual(
                paths.executable_path(),
                "/Applications/LLM Usage Meter.app/Contents/MacOS/llm-usage-meter",
            )


class AutostartFrozenTests(unittest.TestCase):
    def test_frozen_command_is_just_the_binary(self):
        from llm_meter import autostart

        binary = "/opt/llm-usage-meter/llm-usage-meter"
        with patch.object(paths, "frozen", return_value=True), patch.object(
            paths, "executable_path", return_value=binary
        ):
            self.assertEqual(autostart._command(), [binary])
            self.assertEqual(autostart._workdir(), "/opt/llm-usage-meter")


if __name__ == "__main__":
    unittest.main()
