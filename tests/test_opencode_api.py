import tempfile
import unittest
from unittest.mock import Mock, patch

import requests

from llm_meter import config
from llm_meter.providers.opencode import api

GO_HTML = (
    'x;mine:!0,useBalance:!1,region:"us",'
    'rollingUsage:$R[1]={status:"ok",resetInSec:13174,usagePercent:53,cost:-0.5},'
    'weeklyUsage:$R[2]={status:"ok",resetInSec:108000,usagePercent:20},'
    'monthlyUsage:$R[3]={status:"ok",resetInSec:400000,usagePercent:10}}more'
)

BILLING_HTML = (
    '$R[36]($R[16],$R[274]={customerID:"cus_x",balance:1613089290,'
    'monthlyLimit:20,monthlyUsage:387000000,'
    'timeMonthlyUsageUpdated:new Date("2026-07-31T05:29:20.000Z"),'
    'reload:!1,reloadAmount:20,'
    'reloadTrigger:5,paymentMethodType:"card",'
    'timeCreated:new Date("2026-07-20T11:25:36.000Z")});'
)


def parse(text):
    value, _ = api._parse_value(text, 0)
    return value


class ValueParserTests(unittest.TestCase):
    def test_numbers(self):
        self.assertEqual(parse("53"), 53)
        self.assertEqual(parse("-7"), -7)
        self.assertEqual(parse("-12.5"), -12.5)
        self.assertEqual(parse("1.5e-7"), 1.5e-7)

    def test_seroval_booleans_and_blanks(self):
        self.assertIs(parse("!0"), True)
        self.assertIs(parse("!1"), False)
        self.assertIsNone(parse("null"))
        self.assertIsNone(parse("undefined"))
        self.assertIsNone(parse("void 0"))
        self.assertIsNone(parse("-Infinity"))

    def test_strings_with_escapes(self):
        self.assertEqual(parse('"a\\"b\\nc"'), 'a"b\nc')
        self.assertEqual(parse('"\\uD55C"'), "한")

    def test_nested_structures_stay_nested(self):
        obj = parse('{status:"ok",usage:{amount:6.3},limit:{amount:12}}')

        self.assertEqual(obj["usage"]["amount"], 6.3)
        self.assertEqual(obj["limit"]["amount"], 12)

    def test_arrays_and_reference_assignments(self):
        self.assertEqual(parse("[1,!0,null,{a:2}]"), [1, True, None, {"a": 2}])
        self.assertEqual(parse("$R[4]={a:$R[5]=1,b:$R[6]}"), {"a": 1, "b": None})

    def test_quoted_and_bare_keys(self):
        self.assertEqual(parse('{"quoted":1,bare:2}'), {"quoted": 1, "bare": 2})
        self.assertEqual(parse("{}"), {})

    def test_dates_keep_their_iso_string(self):
        self.assertEqual(parse('new Date("2026-07-20T11:25:36.000Z")'), "2026-07-20T11:25:36.000Z")

    def test_garbage_raises(self):
        with self.assertRaises(api.ParseError):
            parse("###")


class GoUsageTests(unittest.TestCase):
    def test_all_three_periods_are_extracted(self):
        go = api.parse_go_usage(GO_HTML)

        self.assertEqual(api.usage_percent(go.rolling), 53)
        self.assertEqual(api.reset_in_seconds(go.rolling), 13174)
        self.assertEqual(api.usage_percent(go.weekly), 20)
        self.assertEqual(api.usage_percent(go.monthly), 10)
        self.assertIs(go.mine, True)
        self.assertIs(go.use_balance, False)

    def test_an_error_state_period_does_not_swallow_the_next_one(self):
        html = (
            'rollingUsage:$R[1]={status:"error",message:"x"},'
            'weeklyUsage:$R[2]={status:"ok",usagePercent:20}}'
        )

        go = api.parse_go_usage(html)

        self.assertEqual(go.rolling, {"status": "error", "message": "x"})
        self.assertIsNone(api.usage_percent(go.rolling))
        self.assertEqual(api.usage_percent(go.weekly), 20)

    def test_a_loading_placeholder_does_not_mask_the_real_data(self):
        html = 'rollingUsage:void 0,junk:1,rollingUsage:{status:"ok",usagePercent:7}}'

        self.assertEqual(api.usage_percent(api.parse_go_usage(html).rolling), 7)

    def test_a_login_page_yields_nothing(self):
        self.assertIsNone(api.parse_go_usage("<html>login page</html>"))

    def test_percent_is_found_in_nested_shapes(self):
        self.assertEqual(api.find_nested_key({"a": {"b": {"usagePercent": 42}}}, "usagePercent"), 42)
        self.assertEqual(api.usage_percent({"window": {"usagePercent": 9}}), 9)
        self.assertIsNone(api.usage_percent({"status": "error"}))
        self.assertIsNone(api.usage_percent(None))


class ZenBillingTests(unittest.TestCase):
    def test_money_fields_are_unscaled(self):
        zen = api.parse_zen_billing(BILLING_HTML)

        self.assertAlmostEqual(zen.balance, 16.1308929)
        self.assertEqual(zen.monthly_limit, 20)
        self.assertAlmostEqual(zen.monthly_usage, 3.87)
        self.assertEqual(zen.time_monthly_usage_updated, "2026-07-31T05:29:20.000Z")
        self.assertFalse(zen.reload_enabled)
        self.assertEqual(zen.reload_amount, 20)
        self.assertEqual(zen.reload_trigger, 5)
        self.assertEqual(zen.payment_method, "card")

    def test_the_customer_record_wins_over_a_stray_balance_word(self):
        html = 'balance:"nope",{customerID:"cus_x",balance:500000000}'

        self.assertEqual(api.parse_zen_billing(html).balance, 5.0)

    def test_a_bare_balance_is_used_as_a_fallback(self):
        self.assertEqual(api.parse_zen_billing("balance:500000000").balance, 5.0)
        self.assertEqual(api.parse_zen_billing("balance:0").balance, 0.0)

    def test_a_non_numeric_balance_is_ignored(self):
        self.assertIsNone(api.parse_zen_billing('balance:"nope",cost:1'))


class UsageUpdatedAtTests(unittest.TestCase):
    def test_the_iso_string_is_parsed_as_utc(self):
        updated = api.usage_updated_at("2026-07-31T05:29:20.000Z")

        self.assertIsNotNone(updated)
        self.assertEqual((updated.year, updated.month, updated.day), (2026, 7, 31))
        self.assertEqual(updated.utcoffset().total_seconds(), 0)

    def test_missing_or_garbage_values_are_none(self):
        self.assertIsNone(api.usage_updated_at(None))
        self.assertIsNone(api.usage_updated_at("not a date"))


class WorkspaceDiscoveryTests(unittest.TestCase):
    def response(self, status=200, location=None, text=""):
        return Mock(status_code=status, headers={"location": location} if location else {}, text=text)

    def session_returning(self, *responses):
        session = Mock()
        session.get.side_effect = responses
        return session

    def test_the_id_comes_from_the_auth_redirect(self):
        session = self.session_returning(self.response(302, "/workspace/wrk_abc123/go"))

        with patch.object(api, "_session", return_value=session):
            self.assertEqual(api.find_workspace_id("key"), "wrk_abc123")

    def test_a_redirect_to_the_login_page_means_the_key_is_dead(self):
        session = self.session_returning(self.response(302, "https://opencode.ai/auth/login"))

        with patch.object(api, "_session", return_value=session):
            with self.assertRaises(api.AuthExpiredError):
                api.find_workspace_id("key")


class FetchConsoleTests(unittest.TestCase):
    def setUp(self):
        self._dir = tempfile.TemporaryDirectory()
        patcher = patch.object(config, "config_dir", return_value=self._dir.name)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(self._dir.cleanup)

    def fetch(self, *responses):
        session = Mock()
        session.get.side_effect = responses
        with patch.object(api, "_session", return_value=session):
            return api.fetch_console("key", "wrk_abc123"), session

    def page(self, status=200, text="", location=None):
        return Mock(
            status_code=status, text=text, headers={"location": location} if location else {}
        )

    def test_both_data_sets_come_from_one_request(self):
        data, session = self.fetch(self.page(text=GO_HTML + BILLING_HTML))

        self.assertEqual(api.usage_percent(data.go.rolling), 53)
        self.assertAlmostEqual(data.zen.balance, 16.1308929)
        self.assertEqual(session.get.call_count, 1)

    def test_billing_falls_back_to_the_workspace_home_page(self):
        data, session = self.fetch(self.page(text=GO_HTML), self.page(text=BILLING_HTML))

        self.assertAlmostEqual(data.zen.balance, 16.1308929)
        self.assertEqual(session.get.call_count, 2)
        self.assertIn("wrk_abc123", session.get.call_args_list[1].args[0])

    def test_a_home_page_failure_is_recorded_but_not_fatal(self):
        session = Mock()
        session.get.side_effect = [self.page(text=GO_HTML), requests.ConnectionError("down")]

        with patch.object(api, "_session", return_value=session):
            data = api.fetch_console("key", "wrk_abc123")

        self.assertIsNotNone(data.go)
        self.assertIsNone(data.zen)
        self.assertEqual(data.zen_error, "down")

    def test_a_redirect_means_the_session_expired(self):
        with self.assertRaises(api.AuthExpiredError):
            self.fetch(self.page(302, location="/auth"))

    def test_rejected_requests_are_auth_errors(self):
        with self.assertRaises(api.AuthExpiredError):
            self.fetch(self.page(403))

    def test_other_statuses_are_fetch_errors(self):
        with self.assertRaises(api.FetchError) as caught:
            self.fetch(self.page(404))

        self.assertIn("404", str(caught.exception))

    def test_unparseable_go_html_is_a_section_failure_and_is_dumped(self):
        data, _ = self.fetch(self.page(text="<html>nothing useful</html>"), self.page())

        self.assertIsNone(data.go)
        self.assertIn("opencode-last-fetch.html", data.go_error)

    def test_a_cancelled_go_plan_still_shows_the_zen_balance(self):
        data, session = self.fetch(self.page(text=BILLING_HTML))

        self.assertIsNone(data.go)
        self.assertIsNotNone(data.go_error)
        self.assertAlmostEqual(data.zen.balance, 16.1308929)
        self.assertEqual(session.get.call_count, 1)

    def test_a_go_page_failure_still_fetches_zen_from_the_home_page(self):
        data, session = self.fetch(self.page(500), self.page(text=BILLING_HTML))

        self.assertIsNone(data.go)
        self.assertIn("500", data.go_error)
        self.assertAlmostEqual(data.zen.balance, 16.1308929)
        self.assertEqual(session.get.call_count, 2)

    def test_a_total_billing_failure_is_recorded(self):
        data, _ = self.fetch(self.page(text=GO_HTML), self.page(text="<html>nothing</html>"))

        self.assertIsNotNone(data.go)
        self.assertIsNone(data.zen)
        self.assertEqual(data.zen_error, "no billing data found")

    def test_a_missing_key_is_refused_before_any_request(self):
        with self.assertRaises(api.AuthExpiredError):
            api.fetch_console("", "wrk_abc123")


class PageUrlTests(unittest.TestCase):
    def test_each_page_has_its_own_url(self):
        self.assertEqual(api.go_page("wrk_1"), "https://opencode.ai/workspace/wrk_1/go")
        self.assertEqual(api.zen_page("wrk_1"), "https://opencode.ai/workspace/wrk_1/billing")
        self.assertEqual(api.stats_page("wrk_1"), "https://opencode.ai/workspace/wrk_1/usage")


if __name__ == "__main__":
    unittest.main()
