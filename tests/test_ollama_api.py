import unittest
from datetime import datetime, timezone
from unittest.mock import Mock, patch

import requests

from llm_meter.providers.ollama import api


def balance_payload():
    return {
        "included": {
            "balance_usd": 72.5, "allowance_usd": 100,
            "period": {"from": "2026-09-15T09:30:00Z", "until": "2026-10-15T09:30:00Z"},
        },
        "purchased": {"balance_usd": 25},
    }


def legacy_payload():
    return {"included": {
        "session": {"remaining_percent": 75, "resets_at": "2026-10-01T07:00:00Z"},
        "weekly": {"remaining_percent": 40, "resets_at": "2026-10-05T00:00:00Z"},
    }, "purchased": {"balance_usd": 0}}


def history_payload():
    return {"range": "30d", "scope": "self", "group_by": "model", "totals": {
        "request_count": 20,
        "groups": [{"model": "glm-5.3-flash", "request_count": 12},
                   {"model": "gpt-oss:120b", "request_count": 3}],
        "remainder": {"request_count": 5},
    }}


class BalanceTests(unittest.TestCase):
    def test_included_credits_are_not_confused_with_purchased_credits(self):
        data = api.parse_balance(balance_payload())
        monthly = data.windows[0]
        self.assertEqual(monthly.balance, 72.5)
        self.assertEqual(monthly.total, 100)
        self.assertAlmostEqual(monthly.percent, 27.5)
        self.assertEqual(data.purchased, 25)
        self.assertEqual(monthly.reset_at, datetime(2026, 10, 15, 9, 30, tzinfo=timezone.utc))
        self.assertIsNone(data.included_error)

    def test_legacy_remaining_percent_is_converted_to_used_percent(self):
        data = api.parse_balance(legacy_payload())
        self.assertEqual([window.label for window in data.windows], ["session", "weekly"])
        self.assertEqual([window.percent for window in data.windows], [25, 60])
        self.assertEqual(data.purchased, 0)

    def test_unknown_included_shape_keeps_purchased_balance_without_inventing_a_meter(self):
        for included in (None, {}, {"free": {"percent": 0}}, {"balance_usd": 0}):
            with self.subTest(included=included):
                payload = balance_payload()
                payload["included"] = included
                data = api.parse_balance(payload)
                self.assertEqual(data.purchased, 25)
                self.assertEqual(data.windows, [])
                self.assertIsNotNone(data.included_error)

    def test_zero_allowance_is_not_reported_as_zero_used_percent(self):
        payload = balance_payload()
        payload["included"].update(balance_usd=0, allowance_usd=0)
        monthly = api.parse_balance(payload).windows[0]
        self.assertIsNone(monthly.percent)
        self.assertEqual(monthly.total, 0)

    def test_usage_percent_is_bounded_when_balance_is_above_allowance_or_negative(self):
        for balance, percent in ((120, 0), (-1, 100), (0, 100), (100, 0)):
            with self.subTest(balance=balance):
                payload = balance_payload()
                payload["included"]["balance_usd"] = balance
                self.assertEqual(api.parse_balance(payload).windows[0].percent, percent)

    def test_invalid_purchased_balance_cannot_be_displayed_as_zero(self):
        for value in (None, True, "0", float("inf"), float("nan"), 10**1000):
            payload = balance_payload()
            payload["purchased"]["balance_usd"] = value
            with self.subTest(value=value), self.assertRaises(api.ParseError):
                api.parse_balance(payload)

    def test_one_malformed_legacy_window_does_not_make_a_partial_meter(self):
        for remaining in (-1, 101, True, "75", float("nan")):
            with self.subTest(remaining=remaining):
                payload = legacy_payload()
                payload["included"]["weekly"]["remaining_percent"] = remaining
                data = api.parse_balance(payload)
                self.assertEqual(data.windows, [])
                self.assertIsNotNone(data.included_error)

    def test_invalid_or_timezone_less_period_is_not_used_for_a_countdown(self):
        for until in (None, "bad", "2026-10-15T09:30:00", "2026-09-14T09:30:00Z"):
            with self.subTest(until=until):
                payload = balance_payload()
                payload["included"]["period"]["until"] = until
                data = api.parse_balance(payload)
                self.assertEqual(data.windows, [])
                self.assertIsNotNone(data.included_error)


class HistoryTests(unittest.TestCase):
    def test_model_groups_and_remainder_are_preserved_without_requiring_token_costs(self):
        data = api.parse_history(history_payload())
        self.assertEqual(data.requests, 20)
        self.assertEqual([(model.name, model.requests) for model in data.models],
                         [("glm-5.3-flash", 12), ("gpt-oss:120b", 3)])
        self.assertEqual(data.other_requests, 5)

    def test_empty_usage_is_a_real_zero_not_a_parse_failure(self):
        data = api.parse_history({"range": "30d", "totals": {"request_count": 0, "groups": []}})
        self.assertEqual(data.requests, 0)
        self.assertEqual(data.models, [])
        self.assertEqual(data.other_requests, 0)

    def test_a_different_period_is_not_mislabeled_as_30_days(self):
        payload = history_payload()
        payload["range"] = "7d"
        with self.assertRaises(api.ParseError):
            api.parse_history(payload)

    def test_malformed_counts_and_models_are_rejected(self):
        for group in ({"model": "m", "request_count": -1}, {"model": "m", "request_count": True},
                      {"model": "", "request_count": 1}, {"model": "m", "request_count": "3"}):
            payload = history_payload()
            payload["totals"]["groups"] = [group]
            with self.subTest(group=group), self.assertRaises(api.ParseError):
                api.parse_history(payload)


class FetchCloudTests(unittest.TestCase):
    def response(self, status=200, payload=None, headers=None):
        response = Mock(status_code=status, headers=headers or {})
        response.json.return_value = payload
        return response

    def fetch(self, *responses):
        with patch.object(api.requests, "Session") as constructor:
            session = constructor.return_value.__enter__.return_value
            session.get.side_effect = responses
            return api.fetch_cloud("synthetic-api-key")

    def test_history_failure_does_not_hide_current_balances(self):
        for failure in (self.response(503), self.response(403), self.response(429, headers={"Retry-After": "30"}),
                        self.response(payload={}), requests.Timeout("timeout")):
            with self.subTest(failure=failure):
                data = self.fetch(self.response(payload=balance_payload()), failure)
                self.assertEqual(data.balance.purchased, 25)
                self.assertAlmostEqual(data.balance.windows[0].percent, 27.5)
                self.assertIsNone(data.history)
                self.assertIsNotNone(data.history_error)

    def test_401_is_an_auth_error_even_if_balance_succeeded_first(self):
        with self.assertRaises(api.AuthExpiredError):
            self.fetch(self.response(401))
        with self.assertRaises(api.AuthExpiredError):
            self.fetch(self.response(payload=balance_payload()), self.response(401))

    def test_forbidden_is_not_invalid_credentials(self):
        with self.assertRaises(api.ScopeError):
            self.fetch(self.response(403))

    def test_rate_limit_exposes_retry_after_without_issuing_a_retry(self):
        with self.assertRaises(api.RateLimitError) as caught:
            self.fetch(self.response(429, headers={"Retry-After": "60"}))
        self.assertEqual(caught.exception.retry_after, 60)

    def test_missing_or_invalid_retry_after_is_not_a_fake_wait_time(self):
        for value in ("", "later", "-1", "0"):
            with self.subTest(value=value), self.assertRaises(api.RateLimitError) as caught:
                self.fetch(self.response(429, headers={"Retry-After": value}))
            self.assertIsNone(caught.exception.retry_after)

    def test_invalid_json_is_a_data_error_not_a_network_error(self):
        response = self.response()
        response.json.side_effect = requests.exceptions.JSONDecodeError("bad", "<html>", 0)
        with self.assertRaises(api.ParseError):
            self.fetch(response)

    def test_redirects_server_errors_and_transport_failures_do_not_expire_the_key(self):
        for status in (302, 404, 503):
            with self.subTest(status=status), self.assertRaises(api.FetchError):
                self.fetch(self.response(status))
        with self.assertRaises(requests.Timeout):
            self.fetch(requests.Timeout("timeout"))


if __name__ == "__main__":
    unittest.main()
