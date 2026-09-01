import tempfile
import unittest
from unittest.mock import Mock, patch

import requests

from llm_meter import config
from llm_meter.providers.ollama import api

SETTINGS_HTML = """
<div class="flex justify-between mb-2">
  <span class="text-sm">Monthly usage</span>
  <span class="text-sm "
    >$0.03 of $60 used</span
  >
</div>
<div class="relative group" data-usage-meter="">
  <div class="relative h-3 overflow-hidden rounded-full bg-neutral-200" data-usage-track="" aria-label="Monthly usage $0.03 of $60 used">
    <div class="flex h-full overflow-hidden bg-neutral-950" style="width: 0.1%; ">
      <button type="button" class="relative h-full min-w-[2px] flex-none overflow-hidden border-r border-white p-0 last:border-r-0 focus-visible:outline-none" style="width: 100%; background: #4f46e5" data-usage-segment="" data-model="glm-5.3-flash" data-requests="14" aria-label="glm-5.3-flash: 14 requests"></button>
    </div>
  </div>
</div>
<div class="text-xs text-neutral-500 mt-1 local-time" data-time="2026-10-01T00:38:02Z">
  Resets in 4 weeks.
</div>
<div id="monthly-usage-models" class="mt-3 space-y-1.5">
  <div class="text-xs text-neutral-500">Models used this month</div>
  <div class="flex min-w-0 items-center gap-2 text-xs">
    <span class="h-2 w-2 flex-none rounded-sm" style="background: #4f46e5" aria-hidden="true"></span>
    <span class="min-w-0 flex-1 truncate text-neutral-700" title="glm-5.3-flash">glm-5.3-flash</span>
    <span class="flex-none tabular-nums text-neutral-400">
      14 requests
    </span>
  </div>
</div>

<div class="mb-1 text-xs text-neutral-500">Recent requests</div>
<div class="flex items-center gap-2">
  <span class="text-xs text-neutral-400" data-time="2026-09-01T09:00:00Z">just now</span>
</div>

<h2 class="text-xl font-medium flex items-center space-x-2">
  <span>Included usage</span>
  <span
    class="text-xs font-normal px-2 py-0.5 rounded-full bg-neutral-100 text-neutral-600 capitalize"
    >pro</span
  >
</h2>

<div class="mb-1 text-xs text-neutral-500">Balance remaining</div>
<div class="text-2xl font-medium leading-tight">$0</div>
"""


class ParseSettingsTests(unittest.TestCase):
    def test_usage_window_models_and_balance_are_extracted(self):
        data = api.parse_settings(SETTINGS_HTML)

        self.assertEqual(data.plan, "pro")
        self.assertIsNotNone(data.monthly)
        self.assertEqual(data.monthly.label, "Monthly usage")
        self.assertEqual(data.monthly.used, 0.03)
        self.assertEqual(data.monthly.total, 60.0)
        self.assertAlmostEqual(data.monthly.percent, 0.05)
        self.assertEqual(data.monthly.reset_at.isoformat(), "2026-10-01T00:38:02+00:00")
        self.assertEqual(
            [(model.name, model.requests) for model in data.monthly.models],
            [("glm-5.3-flash", 14)],
        )
        self.assertEqual(data.balance, 0.0)
        self.assertEqual(data.balance_text, "$0")
        self.assertIsNone(data.error)

    def test_the_reset_time_is_not_taken_from_the_recent_requests_list(self):
        data = api.parse_settings(SETTINGS_HTML)

        self.assertEqual(data.monthly.reset_at.isoformat(), "2026-10-01T00:38:02+00:00")

    def test_a_login_page_yields_an_error(self):
        data = api.parse_settings("<html>Sign in to continue</html>")

        self.assertIsNone(data.monthly)
        self.assertIsNone(data.balance)
        self.assertIn("no usage data", data.error)

    def test_a_missing_balance_is_ignored(self):
        html = SETTINGS_HTML.replace("Balance remaining", "Balance gone")

        data = api.parse_settings(html)

        self.assertIsNone(data.balance)
        self.assertIsNone(data.error)

    def test_a_missing_plan_is_ignored(self):
        html = SETTINGS_HTML.replace('capitalize"\n    >pro</span', 'capitalize"\n    ></span')

        data = api.parse_settings(html)

        self.assertIsNone(data.plan)

    def test_money_parsing(self):
        self.assertEqual(api._parse_money("$12.50"), 12.5)
        self.assertEqual(api._parse_money("$0"), 0.0)
        self.assertIsNone(api._parse_money("n/a"))
        self.assertIsNone(api._parse_money(""))


class FetchSettingsTests(unittest.TestCase):
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
            return api.fetch_settings("aid-value", "session-value"), session

    def page(self, status=200, text="", location=None):
        return Mock(
            status_code=status, text=text, headers={"location": location} if location else {}
        )

    def test_the_settings_page_is_fetched_with_both_cookies(self):
        data, session = self.fetch(self.page(text=SETTINGS_HTML))

        self.assertAlmostEqual(data.monthly.percent, 0.05)
        self.assertEqual(session.get.call_count, 1)
        self.assertEqual(session.get.call_args.args[0], api.SETTINGS_PAGE)

    def test_a_redirect_means_the_session_expired(self):
        with self.assertRaises(api.AuthExpiredError):
            self.fetch(self.page(302, location="/signin"))

    def test_rejected_requests_are_auth_errors(self):
        with self.assertRaises(api.AuthExpiredError):
            self.fetch(self.page(403))

    def test_other_statuses_are_fetch_errors(self):
        with self.assertRaises(api.FetchError) as caught:
            self.fetch(self.page(500))

        self.assertIn("500", str(caught.exception))

    def test_unparseable_html_is_a_fetch_error_and_is_dumped(self):
        with self.assertRaises(api.FetchError) as caught:
            self.fetch(self.page(text="<html>nothing useful</html>"))

        self.assertIn("ollama-last-fetch.html", str(caught.exception))

    def test_a_missing_cookie_pair_is_refused_before_any_request(self):
        with self.assertRaises(api.AuthExpiredError):
            api.fetch_settings("", "session-value")
        with self.assertRaises(api.AuthExpiredError):
            api.fetch_settings("aid-value", "")


class SessionCookieTests(unittest.TestCase):
    def test_both_cookies_are_set_on_the_session(self):
        session = api._session("aid-value", "session-value")

        self.assertEqual(session.cookies.get("aid"), "aid-value")
        self.assertEqual(session.cookies.get("__Secure-session"), "session-value")


if __name__ == "__main__":
    unittest.main()
