import unittest
from unittest.mock import Mock

import requests

from llm_meter.providers.openrouter import api

PAYLOAD = {"data": {"total_credits": 100.5, "total_usage": 25.75}}


class ParseCreditsTests(unittest.TestCase):
    def test_balance_and_percent_are_derived_from_the_totals(self):
        data = api.parse_credits(PAYLOAD)

        self.assertEqual(data.total_credits, 100.5)
        self.assertEqual(data.total_usage, 25.75)
        self.assertAlmostEqual(data.balance, 74.75)
        self.assertAlmostEqual(data.percent, 25.621890547263682)

    def test_decimal_strings_are_accepted(self):
        data = api.parse_credits({"data": {"total_credits": "100.5", "total_usage": "25.75"}})

        self.assertAlmostEqual(data.balance, 74.75)

    def test_usage_over_the_purchased_credits_clamps_the_meter(self):
        data = api.parse_credits({"data": {"total_credits": 10, "total_usage": 12}})

        self.assertEqual(data.percent, 100.0)
        self.assertEqual(data.balance, -2.0)

    def test_no_purchased_credit_has_no_meter(self):
        data = api.parse_credits({"data": {"total_credits": 0, "total_usage": 0}})

        self.assertIsNone(data.percent)

    def test_payloads_that_cannot_be_credits_are_rejected(self):
        for payload in (
            None,
            [],
            "credits",
            {},
            {"data": None},
            {"data": []},
            {"data": {}},
            {"data": {"total_credits": 100.5}},
            {"data": {"total_credits": "many", "total_usage": 25.75}},
        ):
            with self.subTest(payload=payload):
                with self.assertRaises(api.ParseError):
                    api.parse_credits(payload)

    def test_boolean_values_are_rejected(self):
        with self.assertRaises(api.ParseError):
            api.parse_credits({"data": {"total_credits": True, "total_usage": 1}})


class FetchCreditsTests(unittest.TestCase):
    def fetch(self, response):
        session = Mock()
        session.get.return_value = response
        return api.fetch_credits("sk-or-v1-test", session=session), session

    def page(self, status=200, payload=PAYLOAD):
        return Mock(status_code=status, json=Mock(return_value=payload))

    def test_the_credits_endpoint_carries_the_bearer_key(self):
        data, session = self.fetch(self.page())

        self.assertEqual(data.total_credits, 100.5)
        self.assertEqual(session.get.call_args.args[0], api.CREDITS_URL)
        headers = session.get.call_args.kwargs["headers"]
        self.assertEqual(headers["Authorization"], "Bearer sk-or-v1-test")
        self.assertEqual(session.get.call_args.kwargs["timeout"], 15)

    def test_a_rejected_key_is_an_auth_error(self):
        with self.assertRaises(api.AuthExpiredError):
            self.fetch(self.page(401))

    def test_an_inference_key_response_is_a_scope_error(self):
        with self.assertRaises(api.ScopeError):
            self.fetch(self.page(403))

    def test_other_statuses_are_fetch_errors(self):
        with self.assertRaises(api.FetchError) as caught:
            self.fetch(self.page(500))

        self.assertIn("500", str(caught.exception))

    def test_invalid_json_is_a_fetch_error(self):
        response = Mock(status_code=200, json=Mock(side_effect=ValueError("nope")))

        with self.assertRaises(api.FetchError):
            self.fetch(response)

    def test_network_errors_reach_the_caller_unmapped(self):
        session = Mock()
        session.get.side_effect = requests.ConnectionError("boom")

        with self.assertRaises(requests.ConnectionError):
            api.fetch_credits("sk-or-v1-test", session=session)


if __name__ == "__main__":
    unittest.main()