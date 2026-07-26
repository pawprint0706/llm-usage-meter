"""Tests for Cursor session resolution (local first, pasted fallback)."""

import base64
import json
import os
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from tests._support import FakeKeyring, install_keyring_stub

install_keyring_stub()

from llm_meter import keystore
from llm_meter.providers.cursor import auth as cursor_auth


def jwt(claims: dict) -> str:
    body = base64.urlsafe_b64encode(json.dumps(claims).encode()).decode().rstrip("=")
    return f"eyJhbGciOiJub25lIn0.{body}.sig"


def session_jwt(user: str = "user_01ABC", prefix: str | None = "auth0") -> str:
    sub = f"{prefix}|{user}" if prefix else user
    return jwt({"sub": sub, "type": "session", "aud": "https://cursor.com"})


class StoreBackedTestCase(unittest.TestCase):
    def setUp(self):
        keystore.clear_cache()
        self.fake = FakeKeyring()
        patcher = patch.object(keystore, "keyring", self.fake)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(keystore.clear_cache)


class CleanSessionTokenTests(unittest.TestCase):
    def test_cookie_pair_is_accepted(self):
        token = session_jwt()
        raw = f"user_01ABC::{token}"

        self.assertEqual(cursor_auth.clean_session_token(raw), raw)

    def test_url_encoded_separator_is_decoded(self):
        token = session_jwt()

        self.assertEqual(
            cursor_auth.clean_session_token(f"user_01ABC%3A%3A{token}"),
            f"user_01ABC::{token}",
        )

    def test_cookie_name_prefix_is_stripped(self):
        token = session_jwt()

        self.assertEqual(
            cursor_auth.clean_session_token(f"WorkosCursorSessionToken={token}"),
            token,
        )

    def test_raw_jwt_is_kept_when_sub_is_present(self):
        token = session_jwt()

        self.assertEqual(cursor_auth.clean_session_token(token), token)

    def test_garbage_is_rejected(self):
        for value in (None, "", "short", "two words", "user_01::not-a-jwt"):
            with self.subTest(value=value):
                self.assertIsNone(cursor_auth.clean_session_token(value))

    def test_instructions_point_at_the_workos_cookie(self):
        text = cursor_auth.instructions()

        self.assertIn("cursor.com/dashboard/usage", text)
        self.assertIn("WorkosCursorSessionToken", text)


class UserIdFromTokenTests(unittest.TestCase):
    def test_auth0_prefix_is_stripped(self):
        self.assertEqual(
            cursor_auth.user_id_from_token(session_jwt(prefix="auth0")),
            "user_01ABC",
        )

    def test_bare_user_sub_passes_through(self):
        self.assertEqual(
            cursor_auth.user_id_from_token(session_jwt(prefix=None)),
            "user_01ABC",
        )


class SessionFromTokenTests(unittest.TestCase):
    def test_builds_cookie_value_from_jwt(self):
        token = session_jwt()

        session = cursor_auth.session_from_token(token, source="cli")

        self.assertEqual(session.user_id, "user_01ABC")
        self.assertEqual(session.access_token, token)
        self.assertEqual(session.cookie_value, f"user_01ABC::{token}")
        self.assertEqual(session.source, "cli")


class PastedStorageTests(StoreBackedTestCase):
    def test_round_trip_stores_cookie_form(self):
        token = session_jwt()

        cursor_auth.save_pasted_session_token(token)

        stored = cursor_auth.load_pasted_session_token()
        self.assertEqual(stored, f"user_01ABC::{token}")

    def test_signing_out_clears_the_store(self):
        cursor_auth.save_pasted_session_token(session_jwt())

        cursor_auth.delete_pasted_session_token()

        self.assertIsNone(cursor_auth.load_pasted_session_token())


class ResolveSessionTests(StoreBackedTestCase):
    def test_local_ide_wins_over_pasted(self):
        ide = session_jwt(user="user_IDE")
        pasted = f"user_PASTE::{session_jwt(user='user_PASTE')}"

        session = cursor_auth.resolve_session(
            env={},
            ide_token=ide,
            cli_token=None,
            pasted=pasted,
            probe_local=False,
        )

        self.assertIsNotNone(session)
        self.assertEqual(session.source, "ide")
        self.assertEqual(session.user_id, "user_IDE")

    def test_cli_is_used_when_ide_is_missing(self):
        cli = session_jwt(user="user_CLI")

        session = cursor_auth.resolve_session(
            env={},
            ide_token=None,
            cli_token=cli,
            pasted=None,
            probe_local=False,
        )

        self.assertEqual(session.source, "cli")
        self.assertEqual(session.user_id, "user_CLI")

    def test_pasted_is_the_fallback(self):
        pasted = f"user_PASTE::{session_jwt(user='user_PASTE')}"

        session = cursor_auth.resolve_session(
            env={},
            ide_token=None,
            cli_token=None,
            pasted=pasted,
            probe_local=False,
        )

        self.assertEqual(session.source, "keystore")
        self.assertEqual(session.user_id, "user_PASTE")

    def test_env_override_wins(self):
        env_token = session_jwt(user="user_ENV")

        session = cursor_auth.resolve_session(
            env={cursor_auth.ENV_SESSION_TOKEN: env_token},
            ide_token=session_jwt(user="user_IDE"),
            pasted=f"user_PASTE::{session_jwt(user='user_PASTE')}",
            probe_local=False,
        )

        self.assertEqual(session.source, "env")
        self.assertEqual(session.user_id, "user_ENV")

    def test_nothing_available_means_signed_out(self):
        self.assertIsNone(
            cursor_auth.resolve_session(
                env={},
                ide_token=None,
                cli_token=None,
                pasted=None,
                probe_local=False,
            )
        )


class IdeStateDbTests(unittest.TestCase):
    def test_access_token_is_read_from_item_table(self):
        token = session_jwt()
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "state.vscdb")
            with sqlite3.connect(path) as conn:
                conn.execute("CREATE TABLE ItemTable (key TEXT, value TEXT)")
                conn.execute(
                    "INSERT INTO ItemTable (key, value) VALUES (?, ?)",
                    (cursor_auth.ACCESS_TOKEN_KEY, token),
                )

            self.assertEqual(cursor_auth.read_ide_access_token(path), token)

    def test_missing_db_returns_none(self):
        self.assertIsNone(cursor_auth.read_ide_access_token("/no/such/state.vscdb"))


if __name__ == "__main__":
    unittest.main()
