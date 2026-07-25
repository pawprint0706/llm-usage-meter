import base64
import json
import time
import unittest
from unittest.mock import Mock, patch

from tests._support import FakeKeyring, install_keyring_stub

install_keyring_stub()

from llm_meter import keystore
from llm_meter.providers.codex import auth as codex_auth
from llm_meter.providers.opencode import auth as opencode_auth


def jwt(claims: dict) -> str:
    body = base64.urlsafe_b64encode(json.dumps(claims).encode()).decode().rstrip("=")
    return f"header.{body}.signature"


def credentials(**overrides) -> codex_auth.Credentials:
    values = {
        "access_token": "access",
        "refresh_token": "refresh",
        "id_token": None,
        "account_id": "acct_1",
        "email": "user@example.com",
        "plan_type": "plus",
        "refreshed_at": time.time(),
    }
    values.update(overrides)
    return codex_auth.Credentials(**values)


class StoreBackedTestCase(unittest.TestCase):
    def setUp(self):
        self.fake = FakeKeyring()
        patcher = patch.object(keystore, "keyring", self.fake)
        patcher.start()
        self.addCleanup(patcher.stop)


class CodexCredentialTests(StoreBackedTestCase):
    def test_round_trip_through_the_credential_store(self):
        codex_auth.save_credentials(credentials())

        loaded = codex_auth.load_credentials()

        self.assertEqual(loaded.access_token, "access")
        self.assertEqual(loaded.plan_type, "plus")

    def test_nothing_stored_reads_as_signed_out(self):
        self.assertIsNone(codex_auth.load_credentials())

    def test_damaged_data_asks_for_a_fresh_sign_in(self):
        self.fake.set_password(keystore.SERVICE, codex_auth.KEYSTORE_ACCOUNT, "not-json")

        with self.assertRaises(codex_auth.AuthError):
            codex_auth.load_credentials()

    def test_data_missing_a_token_is_refused(self):
        keystore.set(codex_auth.KEYSTORE_ACCOUNT, json.dumps({**{
            "access_token": "",
            "refresh_token": "refresh",
            "id_token": None,
            "account_id": None,
            "email": None,
            "plan_type": None,
            "refreshed_at": time.time(),
        }}))

        with self.assertRaises(codex_auth.AuthError):
            codex_auth.load_credentials()

    def test_signing_out_clears_the_store(self):
        codex_auth.save_credentials(credentials())

        codex_auth.delete_credentials()

        self.assertIsNone(codex_auth.load_credentials())


class CodexIdentityTests(unittest.TestCase):
    def test_the_plan_and_account_come_from_the_id_token(self):
        token = jwt(
            {
                "https://api.openai.com/auth": {
                    "chatgpt_account_id": "acct_9",
                    "chatgpt_plan_type": "pro",
                },
                "email": "person@example.com",
            }
        )

        self.assertEqual(codex_auth._identity(token), ("acct_9", "person@example.com", "pro"))

    def test_an_unreadable_token_yields_nothing(self):
        self.assertEqual(codex_auth._identity("garbage"), (None, None, None))


class CodexRefreshTests(StoreBackedTestCase):
    def test_a_fresh_token_is_used_as_is(self):
        codex_auth.save_credentials(credentials(access_token=jwt({"exp": time.time() + 3600})))

        with patch.object(codex_auth, "refresh_credentials") as refresh:
            codex_auth.valid_credentials()

        refresh.assert_not_called()

    def test_a_token_about_to_expire_is_refreshed(self):
        codex_auth.save_credentials(credentials(access_token=jwt({"exp": time.time() + 60})))

        with patch.object(codex_auth, "refresh_credentials") as refresh:
            codex_auth.valid_credentials()

        refresh.assert_called_once()

    def test_a_long_unused_login_is_refreshed(self):
        stale = time.time() - codex_auth.REFRESH_AFTER_SECONDS - 1
        codex_auth.save_credentials(credentials(refreshed_at=stale))

        with patch.object(codex_auth, "refresh_credentials") as refresh:
            codex_auth.valid_credentials()

        refresh.assert_called_once()

    def test_refreshing_stores_the_new_tokens(self):
        session = Mock()
        session.post.return_value = Mock(
            status_code=200,
            **{"json.return_value": {"access_token": "new-access", "refresh_token": "new-refresh"}},
        )

        updated = codex_auth.refresh_credentials(credentials(), session=session)

        self.assertEqual(updated.access_token, "new-access")
        self.assertEqual(codex_auth.load_credentials().refresh_token, "new-refresh")

    def test_a_rejected_refresh_asks_for_a_new_sign_in(self):
        session = Mock()
        session.post.return_value = Mock(status_code=400)

        with self.assertRaises(codex_auth.AuthError):
            codex_auth.refresh_credentials(credentials(), session=session)

    def test_a_refresh_response_without_a_token_is_refused(self):
        session = Mock()
        session.post.return_value = Mock(status_code=200, **{"json.return_value": {}})

        with self.assertRaises(codex_auth.AuthError):
            codex_auth.refresh_credentials(credentials(), session=session)


class SessionKeyCleanupTests(unittest.TestCase):
    def test_a_plain_key_passes_through(self):
        self.assertEqual(opencode_auth.clean_session_key("k" * 40), "k" * 40)

    def test_a_pasted_cookie_pair_is_unwrapped(self):
        self.assertEqual(opencode_auth.clean_session_key('auth="' + "k" * 40 + '";'), "k" * 40)

    def test_surrounding_whitespace_and_quotes_are_dropped(self):
        self.assertEqual(opencode_auth.clean_session_key("  '" + "k" * 40 + "'  "), "k" * 40)

    def test_values_that_cannot_be_keys_are_rejected(self):
        for value in (None, "", "short", "auth=", "two words in here somewhere"):
            with self.subTest(value=value):
                self.assertIsNone(opencode_auth.clean_session_key(value))

    def test_the_instructions_point_at_the_auth_cookie(self):
        text = opencode_auth.instructions()

        self.assertIn("opencode.ai/auth", text)
        self.assertIn("'auth'", text)


class SessionKeyStorageTests(StoreBackedTestCase):
    def test_round_trip(self):
        opencode_auth.save_session_key("k" * 40)

        self.assertEqual(opencode_auth.load_session_key(), "k" * 40)

    def test_nothing_stored_reads_as_signed_out(self):
        self.assertIsNone(opencode_auth.load_session_key())

    def test_signing_out_clears_the_store(self):
        opencode_auth.save_session_key("k" * 40)

        opencode_auth.delete_session_key()

        self.assertIsNone(opencode_auth.load_session_key())

    def test_a_store_failure_is_reported_as_an_auth_error(self):
        with patch.object(keystore, "set", side_effect=keystore.KeystoreError("locked")):
            with self.assertRaises(opencode_auth.AuthError):
                opencode_auth.save_session_key("k" * 40)


if __name__ == "__main__":
    unittest.main()
