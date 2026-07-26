import unittest
from unittest.mock import patch

from tests._support import FakeKeyring, install_keyring_stub

install_keyring_stub()

from llm_meter import keystore

ACCOUNT = "codex-credentials"


class KeystoreTests(unittest.TestCase):
    def setUp(self):
        self.fake = FakeKeyring()
        patcher = patch.object(keystore, "keyring", self.fake)
        patcher.start()
        self.addCleanup(patcher.stop)

    def entries_for(self, prefix: str) -> list[str]:
        return [service for service, _ in self.fake.entries if service.startswith(prefix)]

    def test_round_trip(self):
        keystore.set(ACCOUNT, '{"token": "abc"}')

        self.assertEqual(keystore.get(ACCOUNT), '{"token": "abc"}')

    def test_value_is_not_stored_in_the_clear(self):
        keystore.set(ACCOUNT, "super-secret-token")

        stored = self.fake.get_password(keystore.SERVICE, ACCOUNT)
        self.assertNotIn("super-secret-token", stored)
        self.assertTrue(stored.startswith("z1:"))

    def test_missing_account_reads_as_none(self):
        self.assertIsNone(keystore.get("nothing-here"))

    def test_long_value_stays_as_one_item_outside_windows(self):
        secret = "".join(f"{index:07d}-payload-" for index in range(400))
        with patch.object(keystore, "_chunking_enabled", return_value=False):
            keystore.set(ACCOUNT, secret)

            stored = self.fake.get_password(keystore.SERVICE, ACCOUNT)
            self.assertTrue(stored.startswith("z1:"))
            self.assertEqual(self.entries_for(f"{keystore.SERVICE} {ACCOUNT}"), [])
            self.assertEqual(keystore.get(ACCOUNT), secret)

    def test_long_value_is_split_into_chunks_on_windows(self):
        secret = "".join(f"{index:07d}-payload-" for index in range(400))
        with patch.object(keystore, "_chunking_enabled", return_value=True):
            keystore.set(ACCOUNT, secret)

            manifest = self.fake.get_password(keystore.SERVICE, ACCOUNT)
            self.assertTrue(manifest.startswith("m1:"))
            self.assertGreater(len(self.entries_for(f"{keystore.SERVICE} {ACCOUNT}")), 1)
            self.assertEqual(keystore.get(ACCOUNT), secret)

    def test_overwriting_a_chunked_value_removes_the_old_chunks(self):
        with patch.object(keystore, "_chunking_enabled", return_value=True):
            keystore.set(ACCOUNT, "".join(f"{index:07d}-payload-" for index in range(400)))
            keystore.set(ACCOUNT, "short")

            self.assertEqual(keystore.get(ACCOUNT), "short")
            self.assertEqual(self.entries_for(f"{keystore.SERVICE} {ACCOUNT}"), [])

    def test_delete_removes_chunks_too(self):
        with patch.object(keystore, "_chunking_enabled", return_value=True):
            keystore.set(ACCOUNT, "".join(f"{index:07d}-payload-" for index in range(400)))
            keystore.delete(ACCOUNT)

            self.assertIsNone(keystore.get(ACCOUNT))
            self.assertEqual(self.fake.entries, {})

    def test_deleting_an_absent_account_is_not_an_error(self):
        keystore.delete(ACCOUNT)

    def test_incomplete_chunk_set_is_reported(self):
        with patch.object(keystore, "_chunking_enabled", return_value=True):
            keystore.set(ACCOUNT, "".join(f"{index:07d}-payload-" for index in range(400)))
            chunk = next(iter(self.entries_for(f"{keystore.SERVICE} {ACCOUNT}")))
            del self.fake.entries[(chunk, ACCOUNT)]

            with self.assertRaises(keystore.KeystoreError):
                keystore.get(ACCOUNT)

    def test_damaged_payload_is_reported(self):
        self.fake.set_password(keystore.SERVICE, ACCOUNT, "z1:not-really-base85")

        with self.assertRaises(keystore.KeystoreError):
            keystore.get(ACCOUNT)

    def test_a_failed_write_leaves_nothing_behind(self):
        self.fake.limit = 950  # accepts a single chunk, rejects the manifest write
        with patch.object(keystore, "_chunking_enabled", return_value=True):
            with patch.object(keystore, "_MANIFEST_PREFIX", "m1:" + "x" * 1000):
                with self.assertRaises(keystore.KeystoreError):
                    keystore.set(
                        ACCOUNT, "".join(f"{index:07d}-payload-" for index in range(400))
                    )

        self.assertEqual(self.fake.entries, {})

    def test_get_migrates_legacy_chunks_when_chunking_is_off(self):
        secret = "".join(f"{index:07d}-payload-" for index in range(400))
        with patch.object(keystore, "_chunking_enabled", return_value=True):
            keystore.set(ACCOUNT, secret)
        self.assertTrue(
            self.fake.get_password(keystore.SERVICE, ACCOUNT).startswith("m1:")
        )

        with patch.object(keystore, "_chunking_enabled", return_value=False):
            self.assertEqual(keystore.get(ACCOUNT), secret)

        stored = self.fake.get_password(keystore.SERVICE, ACCOUNT)
        self.assertTrue(stored.startswith("z1:"))
        self.assertEqual(self.entries_for(f"{keystore.SERVICE} {ACCOUNT}"), [])


if __name__ == "__main__":
    unittest.main()
