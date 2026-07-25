"""Tests for Cursor usage-summary parsing and HTTP helpers."""

import unittest
from datetime import datetime, timezone
from unittest.mock import Mock

from llm_meter.providers.cursor import api
from llm_meter.providers.cursor.auth import Session


SUMMARY = {
    "billingCycleStart": "2026-07-23T01:15:44.000Z",
    "billingCycleEnd": "2026-08-23T01:15:44.000Z",
    "membershipType": "pro",
    "limitType": "user",
    "isUnlimited": False,
    "autoModelSelectedDisplayMessage": "You've used 20% of your included total usage",
    "namedModelSelectedDisplayMessage": "You've used 99% of your included API usage",
    "individualUsage": {
        "plan": {
            "enabled": True,
            "used": 2000,
            "limit": 2000,
            "remaining": 0,
            "breakdown": {"included": 2000, "bonus": 4981, "total": 6981},
            "autoPercentUsed": 8.336666666666668,
            "apiPercentUsed": 99.55555555555556,
            "totalPercentUsed": 20.234782608695653,
        },
        "onDemand": {"enabled": False, "used": 0, "limit": None, "remaining": None},
    },
    "teamUsage": {},
}


class ParseTests(unittest.TestCase):
    def test_summary_maps_plan_percentages_and_cents(self):
        data = api.parse_usage_summary(SUMMARY)

        self.assertEqual(data.membership_type, "pro")
        self.assertFalse(data.is_unlimited)
        self.assertEqual(data.billing_cycle_start, datetime(2026, 7, 23, 1, 15, 44, tzinfo=timezone.utc))
        self.assertEqual(data.billing_cycle_end, datetime(2026, 8, 23, 1, 15, 44, tzinfo=timezone.utc))
        self.assertAlmostEqual(data.plan.total_percent, 20.234782608695653)
        self.assertAlmostEqual(data.plan.auto_percent, 8.336666666666668)
        self.assertAlmostEqual(data.plan.api_percent, 99.55555555555556)
        self.assertEqual(data.plan.used_cents, 2000)
        self.assertEqual(data.plan.limit_cents, 2000)
        self.assertEqual(data.plan.bonus_cents, 4981)
        self.assertFalse(data.on_demand.enabled)
        self.assertAlmostEqual(data.gauge_percent, 20.234782608695653)

    def test_cents_convert_to_dollars(self):
        self.assertEqual(api.cents_to_dollars(2000), 20.0)
        self.assertEqual(api.cents_to_dollars(4981), 49.81)
        self.assertIsNone(api.cents_to_dollars(None))

    def test_plan_info_fills_display_fields(self):
        data = api.parse_usage_summary(SUMMARY)

        api.apply_plan_info(
            data,
            {
                "planInfo": {
                    "planName": "Pro",
                    "includedAmountCents": 2000,
                    "price": "$20/mo",
                    "billingCycleEnd": "1787447744000",
                }
            },
        )

        self.assertEqual(data.plan_name, "Pro")
        self.assertEqual(data.plan_price, "$20/mo")

    def test_hard_limit_records_overage_policy(self):
        data = api.parse_usage_summary(SUMMARY)

        api.apply_hard_limit(data, {"noUsageBasedAllowed": True})

        self.assertTrue(data.no_usage_based_allowed)

    def test_epoch_millis_timestamps_parse(self):
        moment = api._parse_timestamp("1784769344000")

        self.assertEqual(moment, datetime(2026, 7, 23, 1, 15, 44, tzinfo=timezone.utc))

    def test_a_non_object_summary_is_rejected(self):
        with self.assertRaises(api.FetchError):
            api.parse_usage_summary([])


class FetchTests(unittest.TestCase):
    def setUp(self):
        self.session = Session(
            access_token="a.b.c",
            user_id="user_1",
            cookie_value="user_1::a.b.c",
            source="cli",
        )

    def test_fetch_usage_combines_summary_helpers(self):
        client = Mock()
        client.request.side_effect = [
            Mock(status_code=200, **{"json.return_value": SUMMARY}),
            Mock(status_code=200, **{"json.return_value": {"noUsageBasedAllowed": True}}),
            Mock(
                status_code=200,
                **{
                    "json.return_value": {
                        "planInfo": {"planName": "Pro", "price": "$20/mo"}
                    }
                },
            ),
        ]

        data = api.fetch_usage(self.session, client=client)

        self.assertEqual(data.plan_name, "Pro")
        self.assertTrue(data.no_usage_based_allowed)
        self.assertEqual(client.request.call_count, 3)

    def test_unauthorized_is_reported(self):
        client = Mock()
        client.request.return_value = Mock(status_code=401)

        with self.assertRaises(api.AuthExpiredError):
            api.fetch_usage(self.session, client=client)

    def test_helper_failures_still_return_the_summary(self):
        client = Mock()
        client.request.side_effect = [
            Mock(status_code=200, **{"json.return_value": SUMMARY}),
            Mock(status_code=500),
            Mock(status_code=500),
        ]

        data = api.fetch_usage(self.session, client=client)

        self.assertEqual(data.membership_type, "pro")
        self.assertIsNone(data.plan_name)
        self.assertIsNone(data.no_usage_based_allowed)


if __name__ == "__main__":
    unittest.main()
