import json
import os
import tempfile
import unittest
from unittest.mock import patch

from llm_meter import config


class ConfigFileTests(unittest.TestCase):
    def setUp(self):
        self._dir = tempfile.TemporaryDirectory()
        patcher = patch.object(config, "config_dir", return_value=self._dir.name)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(self._dir.cleanup)

    def write(self, text: str) -> None:
        with open(config.config_path(), "w", encoding="utf-8") as handle:
            handle.write(text)

    def test_missing_file_yields_defaults(self):
        cfg = config.load_config()

        self.assertEqual(cfg.refresh_interval, config.DEFAULT_REFRESH_INTERVAL)
        self.assertEqual(cfg.providers, {})

    def test_round_trip_keeps_provider_settings(self):
        cfg = config.Config(refresh_interval=30, provider_order=["opencode", "codex"])
        cfg.provider("opencode")["workspace_id"] = "wrk_123"
        cfg.set_provider_enabled("codex", False)

        config.save_config(cfg)
        loaded = config.load_config()

        self.assertEqual(loaded.refresh_interval, 30)
        self.assertEqual(loaded.provider_order, ["opencode", "codex"])
        self.assertEqual(loaded.provider("opencode")["workspace_id"], "wrk_123")
        self.assertFalse(loaded.is_provider_enabled("codex"))
        self.assertTrue(loaded.is_provider_enabled("opencode"))

    def test_saved_file_is_owner_only(self):
        config.save_config(config.Config())

        mode = os.stat(config.config_path()).st_mode & 0o777
        self.assertEqual(mode, 0o600)

    def test_unsupported_interval_falls_back_to_default(self):
        self.write(json.dumps({"refresh_interval": 7}))

        self.assertEqual(config.load_config().refresh_interval, config.DEFAULT_REFRESH_INTERVAL)

    def test_corrupt_file_does_not_raise(self):
        self.write("{ not json")

        self.assertEqual(config.load_config().refresh_interval, config.DEFAULT_REFRESH_INTERVAL)

    def test_provider_entries_of_the_wrong_shape_are_dropped(self):
        self.write(json.dumps({"providers": {"codex": {"enabled": False}, "bogus": 5}}))

        loaded = config.load_config()

        self.assertFalse(loaded.is_provider_enabled("codex"))
        self.assertNotIn("bogus", loaded.providers)

    def test_writing_twice_leaves_no_temporary_file(self):
        config.save_config(config.Config())
        config.save_config(config.Config(refresh_interval=60))

        self.assertEqual(sorted(os.listdir(self._dir.name)), ["config.json"])


class ProviderOrderTests(unittest.TestCase):
    def test_missing_ids_are_appended_in_registry_order(self):
        cfg = config.Config(provider_order=["opencode"])

        self.assertEqual(
            cfg.ordered_provider_ids(["codex", "opencode", "cursor"]),
            ["opencode", "codex", "cursor"],
        )

    def test_unknown_saved_ids_are_ignored(self):
        cfg = config.Config(provider_order=["ghost", "codex", "opencode"])

        self.assertEqual(cfg.ordered_provider_ids(["codex", "opencode"]), ["codex", "opencode"])

    def test_move_swaps_neighbours(self):
        cfg = config.Config(provider_order=["codex", "opencode"])

        self.assertTrue(cfg.move_provider("opencode", -1, ["codex", "opencode"]))
        self.assertEqual(cfg.provider_order, ["opencode", "codex"])
        self.assertFalse(cfg.move_provider("opencode", -1, ["codex", "opencode"]))
        self.assertTrue(cfg.move_provider("opencode", 1, ["codex", "opencode"]))
        self.assertEqual(cfg.provider_order, ["codex", "opencode"])

    def test_corrupt_order_entries_are_dropped(self):
        self._dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._dir.cleanup)
        with patch.object(config, "config_dir", return_value=self._dir.name):
            with open(config.config_path(), "w", encoding="utf-8") as handle:
                handle.write(json.dumps({"provider_order": ["codex", 3, "codex", "opencode"]}))
            loaded = config.load_config()

        self.assertEqual(loaded.provider_order, ["codex", "opencode"])


if __name__ == "__main__":
    unittest.main()
