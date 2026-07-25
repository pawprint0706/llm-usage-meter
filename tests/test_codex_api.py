import time
import unittest
from datetime import datetime, timezone
from unittest.mock import Mock, patch

from tests._support import install_keyring_stub

install_keyring_stub()

from llm_meter.providers.codex import api, auth


def credentials(access_token="access", refresh_token="refresh"):
    return auth.Credentials(
        access_token=access_token,
        refresh_token=refresh_token,
        id_token=None,
        account_id="account",
        email=None,
        plan_type="plus",
        refreshed_at=time.time(),
    )


USAGE_PAYLOAD = {
    "plan_type": "plus",
    "rate_limit": {
        "primary_window": {
            "used_percent": 10,
            "reset_at": 1_800_000_000,
            "limit_window_seconds": 18_000,
        },
        "secondary_window": {
            "used_percent": 85,
            "reset_at": 1_800_100_000,
            "limit_window_seconds": 604_800,
        },
    },
    "credits": {
        "has_credits": True,
        "balance": "12.50",
        "approx_local_messages": [40, 120],
        "approx_cloud_messages": [10, 30],
    },
}


class UsageParsingTests(unittest.TestCase):
    def test_windows_are_ordered_and_the_longest_one_leads(self):
        data = api.parse_usage(USAGE_PAYLOAD)

        self.assertEqual([window.window_seconds for window in data.windows], [18_000, 604_800])
        self.assertEqual(data.primary_window.window_seconds, 604_800)
        self.assertEqual(data.primary_window.used_percent, 85)
        self.assertEqual(data.plan_type, "plus")

    def test_remaining_percent_is_clamped(self):
        data = api.parse_usage(
            {
                "rate_limit": {
                    "primary_window": {
                        "used_percent": 130,
                        "reset_at": 1_800_000_000,
                        "limit_window_seconds": 18_000,
                    }
                }
            }
        )

        self.assertEqual(data.windows[0].remaining_percent, 0.0)

    def test_reset_at_is_timezone_aware(self):
        data = api.parse_usage(USAGE_PAYLOAD)

        self.assertEqual(data.windows[0].reset_at.tzinfo, timezone.utc)

    def test_a_payload_without_windows_is_rejected(self):
        with self.assertRaises(api.ResponseError):
            api.parse_usage({"rate_limit": {"primary_window": {"used_percent": 10}}})

    def test_a_payload_without_rate_limit_is_rejected(self):
        with self.assertRaises(api.ResponseError):
            api.parse_usage({"plan_type": "plus"})


class CreditParsingTests(unittest.TestCase):
    def test_decimal_strings_and_message_ranges(self):
        credits = api.parse_usage(USAGE_PAYLOAD).credits

        self.assertTrue(credits.has_credits)
        self.assertEqual(credits.balance, 12.5)
        self.assertEqual(credits.approx_local_messages, (40, 120))
        self.assertEqual(credits.approx_cloud_messages, (10, 30))

    def test_a_missing_credits_object_reads_as_empty(self):
        credits = api.parse_credits(None)

        self.assertFalse(credits.has_credits)
        self.assertEqual(credits.balance, 0.0)
        self.assertIsNone(credits.approx_local_messages)

    def test_empty_message_range_is_dropped(self):
        credits = api.parse_credits({"approx_local_messages": [0, 0]})

        self.assertIsNone(credits.approx_local_messages)

    def test_unlimited_and_overage_flags(self):
        credits = api.parse_credits({"unlimited": True, "overage_limit_reached": True})

        self.assertTrue(credits.unlimited)
        self.assertTrue(credits.overage_limit_reached)


class ResetCreditTests(unittest.TestCase):
    def test_available_credits_are_counted_when_the_server_omits_the_count(self):
        credits, count = api.parse_reset_credits(
            {
                "credits": [
                    {"id": "one", "reset_type": "full_reset", "status": "available"},
                    {"id": "two", "reset_type": "full_reset", "status": "used"},
                ]
            }
        )

        self.assertEqual(len(credits), 2)
        self.assertEqual(count, 1)

    def test_the_server_count_wins_when_present(self):
        _credits, count = api.parse_reset_credits(
            {"available_count": 3, "credits": [{"id": "one", "status": "available"}]}
        )

        self.assertEqual(count, 3)

    def test_iso_expiration_is_timezone_aware(self):
        credits, _count = api.parse_reset_credits(
            {"credits": [{"id": "one", "status": "available", "expires_at": "2026-08-13T00:00:00Z"}]}
        )

        self.assertEqual(credits[0].expires_at, datetime(2026, 8, 13, tzinfo=timezone.utc))

    def test_entries_without_an_id_are_skipped(self):
        credits, _count = api.parse_reset_credits({"credits": [{"status": "available"}, "junk"]})

        self.assertEqual(credits, [])


class FetchTests(unittest.TestCase):
    @patch("llm_meter.providers.codex.api.auth.valid_credentials")
    @patch("llm_meter.providers.codex.api.fetch_usage")
    def test_unauthorized_refreshes_the_token_and_retries_once(
        self, fetch_usage, valid_credentials
    ):
        valid_credentials.side_effect = [credentials("old"), credentials("new")]
        expected = Mock(spec=api.UsageData)
        fetch_usage.side_effect = [api.UnauthorizedError("HTTP 401"), expected]

        result = api.fetch_with_refresh(session=Mock())

        self.assertIs(result, expected)
        self.assertEqual(fetch_usage.call_count, 2)
        self.assertEqual(valid_credentials.call_args_list[1].kwargs, {"force_refresh": True})

    @patch("llm_meter.providers.codex.api.auth.valid_credentials")
    @patch("llm_meter.providers.codex.api.fetch_usage")
    def test_a_second_rejection_is_not_retried(self, fetch_usage, valid_credentials):
        valid_credentials.side_effect = [credentials("old"), credentials("new")]
        fetch_usage.side_effect = api.UnauthorizedError("HTTP 403")

        with self.assertRaises(api.UnauthorizedError):
            api.fetch_with_refresh()

        self.assertEqual(fetch_usage.call_count, 2)

    @patch("llm_meter.providers.codex.api.auth.valid_credentials", return_value=None)
    def test_signed_out_is_reported_as_unauthorized(self, _valid_credentials):
        with self.assertRaises(api.UnauthorizedError):
            api.fetch_with_refresh()

    def test_reset_credit_failure_does_not_lose_the_usage_windows(self):
        session = Mock()
        usage = Mock(status_code=200, **{"json.return_value": USAGE_PAYLOAD})
        session.get.side_effect = [usage, Mock(status_code=500)]

        data = api.fetch_usage(credentials(), session=session)

        self.assertEqual(len(data.windows), 2)
        self.assertEqual(data.reset_credits, [])
        self.assertIn("500", data.reset_credits_error)

    def test_an_expired_token_on_the_reset_endpoint_is_raised(self):
        session = Mock()
        usage = Mock(status_code=200, **{"json.return_value": USAGE_PAYLOAD})
        session.get.side_effect = [usage, Mock(status_code=401)]

        with self.assertRaises(api.UnauthorizedError):
            api.fetch_usage(credentials(), session=session)


if __name__ == "__main__":
    unittest.main()
