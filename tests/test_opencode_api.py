import copy
import unittest
from datetime import datetime, timezone
from unittest.mock import Mock, patch

import requests

from llm_meter.providers.opencode import api


def usage_payload():
    return {"usage": {
        "rolling": {"percent": 53.25, "status": "ok", "resetsAt": "2026-11-03T12:00:00Z"},
        "weekly": {"percent": 0, "status": "rate-limited", "resetsAt": "2026-11-05T12:00:00+09:00"},
        "monthly": {"percent": 100, "status": "ok", "resetsAt": "2026-12-01T00:00:00.000Z"},
    }}


class GoUsageTests(unittest.TestCase):
    def test_used_percentages_limits_and_absolute_resets_are_preserved(self):
        go = api.parse_usage(usage_payload())

        self.assertEqual(go.rolling.percent, 53.25)
        self.assertEqual(go.weekly.percent, 0)
        self.assertEqual(go.weekly.status, "rate-limited")
        self.assertEqual(go.monthly.percent, 100)
        self.assertEqual(go.rolling.resets_at, datetime(2026, 11, 3, 12, tzinfo=timezone.utc))
        self.assertEqual(go.weekly.resets_at, datetime(2026, 11, 5, 3, tzinfo=timezone.utc))

    def test_missing_or_non_object_usage_is_rejected(self):
        for payload in (None, [], {}, {"usage": None}, {"usage": []}):
            with self.subTest(payload=payload), self.assertRaises(api.ParseError):
                api.parse_usage(payload)

    def test_one_bad_window_rejects_the_whole_reading(self):
        invalid = (
            None, [], {},
            {"percent": 3, "status": "error", "resetsAt": "2026-11-03T12:00:00Z"},
        )
        for name in ("rolling", "weekly", "monthly"):
            for value in invalid:
                payload = usage_payload()
                payload["usage"][name] = value
                with self.subTest(name=name, value=value), self.assertRaises(api.ParseError):
                    api.parse_usage(payload)
            payload = usage_payload()
            del payload["usage"][name]
            with self.subTest(missing=name), self.assertRaises(api.ParseError):
                api.parse_usage(payload)

    def test_percent_must_be_finite_numeric_and_in_range(self):
        for percent in (True, False, None, "53", -0.1, 100.1, float("nan"), float("inf")):
            payload = usage_payload()
            payload["usage"]["rolling"]["percent"] = percent
            with self.subTest(percent=percent), self.assertRaises(api.ParseError):
                api.parse_usage(payload)

    def test_reset_must_be_an_absolute_timestamp(self):
        for reset in (None, 123, "bad", "2026-11-03", "2026-11-03T12:00:00", "2026-13-01T00:00:00Z"):
            payload = usage_payload()
            payload["usage"]["monthly"]["resetsAt"] = reset
            with self.subTest(reset=reset), self.assertRaises(api.ParseError):
                api.parse_usage(payload)


class FetchUsageTests(unittest.TestCase):
    def fetch(self, status=200, payload=None):
        response = Mock(status_code=status)
        response.json.return_value = copy.deepcopy(payload if payload is not None else usage_payload())
        with patch.object(api.requests, "get", return_value=response):
            return api.fetch_usage("test-key")

    def test_only_401_means_the_api_key_is_rejected(self):
        with self.assertRaises(api.AuthExpiredError):
            self.fetch(401)
        with self.assertRaises(api.ScopeError):
            self.fetch(403)
        for status in (302, 404, 429, 500):
            with self.subTest(status=status), self.assertRaises(api.FetchError):
                self.fetch(status)

    def test_invalid_json_is_a_data_error_not_a_network_error(self):
        response = Mock(status_code=200)
        response.json.side_effect = requests.exceptions.JSONDecodeError("bad", "not json", 0)
        with patch.object(api.requests, "get", return_value=response):
            with self.assertRaises(api.ParseError):
                api.fetch_usage("test-key")

    def test_a_malformed_success_is_not_accepted(self):
        with self.assertRaises(api.ParseError):
            self.fetch(payload={"error": "unavailable"})

    def test_transport_errors_are_not_reclassified_as_bad_credentials(self):
        with patch.object(api.requests, "get", side_effect=requests.Timeout("timeout")):
            with self.assertRaises(requests.Timeout):
                api.fetch_usage("test-key")


if __name__ == "__main__":
    unittest.main()
