import time
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from tests._support import FakeUi, install_keyring_stub

install_keyring_stub()

from llm_meter.config import Config
from llm_meter.providers import build_providers
from llm_meter.providers.base import State
from llm_meter.providers.codex import api as codex_api
from llm_meter.providers.codex.provider import CodexProvider
from llm_meter.providers.opencode import api as opencode_api
from llm_meter.providers.opencode.provider import OpenCodeProvider, Loaded


def in_hours(hours: float) -> datetime:
    return datetime.now(timezone.utc) + timedelta(hours=hours)


def codex_usage(**overrides) -> codex_api.UsageData:
    data = codex_api.UsageData(
        windows=[
            codex_api.UsageWindow(used_percent=12, reset_at=in_hours(3), window_seconds=5 * 3600),
            codex_api.UsageWindow(
                used_percent=85, reset_at=in_hours(77), window_seconds=7 * 24 * 3600
            ),
        ],
        plan_type="plus",
    )
    for key, value in overrides.items():
        setattr(data, key, value)
    return data


def console(**overrides) -> opencode_api.ConsoleData:
    data = opencode_api.ConsoleData(
        go=opencode_api.GoUsage(
            rolling={"status": "ok", "usagePercent": 53, "resetInSec": 3660},
            weekly={"status": "ok", "usagePercent": 20, "resetInSec": 108_000},
            monthly={"status": "ok", "usagePercent": 10, "resetInSec": 400_000},
        ),
        zen=opencode_api.ZenBilling(
            balance=16.1308929, monthly_limit=20, monthly_usage=3.87, reload_enabled=False
        ),
    )
    for key, value in overrides.items():
        setattr(data, key, value)
    return data


class RegistryTests(unittest.TestCase):
    def test_providers_are_built_in_display_order(self):
        providers = build_providers(Config(), FakeUi())

        self.assertEqual([provider.id for provider in providers], ["codex", "opencode"])
        self.assertTrue(all(provider.enabled for provider in providers))

    def test_a_saved_order_is_honoured(self):
        cfg = Config(provider_order=["opencode", "codex"])

        providers = build_providers(cfg, FakeUi())

        self.assertEqual([provider.id for provider in providers], ["opencode", "codex"])

    def test_a_disabled_service_reports_itself_as_such(self):
        cfg = Config()
        cfg.set_provider_enabled("codex", False)

        providers = {provider.id: provider for provider in build_providers(cfg, FakeUi())}

        self.assertFalse(providers["codex"].enabled)
        self.assertTrue(providers["opencode"].enabled)


class CodexRenderTests(unittest.TestCase):
    def setUp(self):
        self.ui = FakeUi()
        self.provider = CodexProvider(Config(), self.ui)

    def test_sections_cover_usage_credits_and_resets(self):
        snapshot = self.provider.render(codex_usage())

        self.assertEqual(
            [section.title for section in snapshot.sections],
            ["Plan usage", "Credits", "Usage limit resets: 0"],
        )

    def test_the_gauge_follows_the_longest_window(self):
        snapshot = self.provider.render(codex_usage())

        self.assertEqual(snapshot.gauge_percent, 85)
        self.assertEqual(snapshot.badge, "plus")
        self.assertEqual(snapshot.sections[0].url, codex_api.USAGE_PAGE)

    def test_each_window_shows_its_percentage_and_countdown(self):
        metrics = self.provider.render(codex_usage()).sections[0].metrics

        self.assertEqual([metric.label for metric in metrics], ["5h", "Weekly"])
        self.assertEqual(metrics[1].value, "85%")
        self.assertEqual(metrics[1].percent, 85)
        self.assertEqual(metrics[1].detail, "resets in 3d 4h")

    def test_no_credits_reads_as_a_muted_zero_balance(self):
        section = self.provider.render(codex_usage()).sections[1]

        self.assertEqual(section.metrics[0].value, "$0.00")
        self.assertTrue(section.metrics[0].muted)
        self.assertIsNone(section.note)

    def test_a_purchased_balance_shows_the_message_estimates(self):
        credits = codex_api.CreditBalance(
            balance=12.5,
            has_credits=True,
            approx_local_messages=(40, 120),
            approx_cloud_messages=(10, 30),
        )

        section = self.provider.render(codex_usage(credits=credits)).sections[1]

        self.assertEqual(section.metrics[0].value, "$12.50")
        self.assertFalse(section.metrics[0].muted)
        self.assertEqual(section.metrics[0].detail, "~40-120 local · ~10-30 cloud")

    def test_unlimited_credits_and_an_overage_stop(self):
        credits = codex_api.CreditBalance(unlimited=True, overage_limit_reached=True)

        section = self.provider.render(codex_usage(credits=credits)).sections[1]

        self.assertEqual(section.metrics[0].value, "Unlimited")
        self.assertEqual(section.note, "Overage limit reached")

    def test_reset_credits_list_only_the_usable_ones(self):
        data = codex_usage(
            reset_credits=[
                codex_api.ResetCredit("1", "full_reset", "available", "Full reset", None, in_hours(48)),
                codex_api.ResetCredit("2", "full_reset", "used", "Full reset", None, None),
            ],
            available_reset_count=1,
        )

        section = self.provider.render(data).sections[2]

        self.assertEqual(section.title, "Usage limit resets: 1")
        self.assertEqual(len(section.metrics), 1)
        self.assertTrue(section.metrics[0].detail.startswith("Available · expires "))
        self.assertIn("(in 1d 23h)", section.metrics[0].detail)

    def test_an_empty_reset_list_explains_itself(self):
        section = self.provider.render(codex_usage()).sections[2]

        self.assertEqual(section.metrics, [])
        self.assertEqual(section.empty_text, "No reset credits")

    def test_a_reset_lookup_failure_is_shown_as_a_note(self):
        section = self.provider.render(codex_usage(reset_credits_error="HTTP 500")).sections[2]

        self.assertEqual(section.note, "Could not load")

    def test_the_tooltip_lists_every_window(self):
        snapshot = self.provider.render(codex_usage())

        self.assertEqual(snapshot.tooltip, "Codex 5h 12% · Weekly 85%")

    def test_the_tooltip_mentions_credits_when_there_are_any(self):
        credits = codex_api.CreditBalance(balance=12.5, has_credits=True)

        snapshot = self.provider.render(codex_usage(credits=credits))

        self.assertTrue(snapshot.tooltip.endswith("· $12.50 credits"))


class CodexMenuTests(unittest.TestCase):
    def setUp(self):
        self.ui = FakeUi()
        self.provider = CodexProvider(Config(), self.ui)

    def run_entry(self, label: str) -> None:
        with patch.object(self.provider, "is_authenticated", return_value=False):
            entry = next(item for item in self.provider.menu() if item.label == label)
        entry.run()

    def test_the_usage_and_analytics_pages_are_reachable(self):
        self.run_entry("Open usage page")
        self.run_entry("Open analytics page")

        self.assertEqual(self.ui.opened, [codex_api.USAGE_PAGE, codex_api.ANALYTICS_PAGE])

    def test_a_signed_out_card_offers_signing_in(self):
        with patch.object(self.provider, "is_authenticated", return_value=False):
            labels = [entry.label for entry in self.provider.menu()]

        self.assertIn("Sign in with OpenAI...", labels)
        self.assertNotIn("Sign out", labels)
        self.assertEqual(self.provider.primary_action().label, "Sign in with OpenAI...")

    def test_a_signed_in_card_offers_signing_out(self):
        with patch.object(self.provider, "is_authenticated", return_value=True):
            labels = [entry.label for entry in self.provider.menu()]

        self.assertIn("Sign out", labels)
        self.assertNotIn("Sign in with OpenAI...", labels)


class OpenCodeRenderTests(unittest.TestCase):
    def setUp(self):
        self.ui = FakeUi()
        self.cfg = Config()
        self.cfg.provider("opencode")["workspace_id"] = "wrk_1"
        self.provider = OpenCodeProvider(self.cfg, self.ui)

    def render(self, data=None):
        return self.provider.render(Loaded(console=data or console(), monotonic=time.monotonic()))

    def test_go_usage_and_zen_credits_are_separate_sections(self):
        snapshot = self.render()

        self.assertEqual(
            [section.title for section in snapshot.sections], ["Go plan usage", "Zen credits"]
        )
        self.assertEqual(snapshot.sections[0].url, opencode_api.go_page("wrk_1"))
        self.assertEqual(snapshot.sections[1].url, opencode_api.zen_page("wrk_1"))

    def test_each_period_is_priced_against_its_limit(self):
        metrics = self.render().sections[0].metrics

        self.assertEqual([metric.label for metric in metrics], ["5h", "Week", "Month"])
        self.assertEqual(metrics[0].value, "$6.36 / $12")
        self.assertEqual(metrics[1].value, "$6.00 / $30")
        self.assertEqual(metrics[2].value, "$6.00 / $60")
        self.assertEqual(metrics[0].detail, "resets in 1h")

    def test_configured_limits_replace_the_defaults(self):
        self.cfg.provider("opencode")["limits"] = {"rolling": 20}

        metrics = self.render().sections[0].metrics

        self.assertEqual(metrics[0].value, "$10.60 / $20")
        self.assertEqual(metrics[1].value, "$6.00 / $30")

    def test_a_period_without_a_percentage_shows_its_status(self):
        data = console(
            go=opencode_api.GoUsage(
                rolling={"status": "error", "message": "x"}, weekly={}, monthly={}
            )
        )

        metrics = self.render(data).sections[0].metrics

        self.assertEqual(metrics[0].value, "error")
        self.assertTrue(metrics[0].muted)
        self.assertEqual(metrics[1].value, "n/a")

    def test_a_balance_billed_workspace_says_so(self):
        data = console(
            go=opencode_api.GoUsage(
                rolling={"status": "ok", "usagePercent": 0},
                weekly={},
                monthly={},
                use_balance=True,
            )
        )

        self.assertEqual(
            self.render(data).sections[0].note, "This workspace bills against Zen credits"
        )

    def test_zen_shows_the_balance_and_the_monthly_spend(self):
        metrics = self.render().sections[1].metrics

        self.assertEqual(metrics[0].value, "$16.13")
        self.assertEqual(metrics[1].label, "This month")
        self.assertEqual(metrics[1].value, "$3.87 / $20")
        self.assertAlmostEqual(metrics[1].percent, 19.35)
        self.assertEqual(metrics[2].value, "Off")

    def test_auto_reload_reports_its_trigger(self):
        data = console(
            zen=opencode_api.ZenBilling(
                balance=4.0, reload_enabled=True, reload_amount=20, reload_trigger=5
            )
        )

        metric = self.render(data).sections[1].metrics[-1]

        self.assertEqual(metric.label, "Auto-reload")
        self.assertEqual(metric.value, "$20")
        self.assertEqual(metric.detail, "when below $5")

    def test_a_missing_billing_record_is_explained(self):
        section = self.render(console(zen=None)).sections[1]

        self.assertEqual(section.metrics, [])
        self.assertEqual(section.note, "Could not read the balance")

    def test_the_gauge_follows_the_busiest_period(self):
        snapshot = self.render()

        self.assertEqual(snapshot.gauge_percent, 53)

    def test_the_tooltip_covers_all_periods_and_the_balance(self):
        snapshot = self.render()

        self.assertEqual(
            snapshot.tooltip, "OpenCode Go 5h 53% · Week 20% · Month 10% · Zen $16.13"
        )

    def test_countdowns_shrink_as_time_passes(self):
        loaded = Loaded(console=console(), monotonic=time.monotonic() - 1800)

        metrics = self.provider.render(loaded).sections[0].metrics

        self.assertEqual(metrics[0].detail, "resets in 30m")


class OpenCodeSessionTests(unittest.TestCase):
    def setUp(self):
        self.ui = FakeUi()
        self.provider = OpenCodeProvider(Config(), self.ui)
        self.saved: list[str] = []
        # Nothing here may touch the real credential store or config file, and the
        # card must start signed out however the developer's keychain looks.
        for patcher in (
            patch(
                "llm_meter.providers.opencode.provider.auth.load_session_key",
                return_value=None,
            ),
            patch(
                "llm_meter.providers.opencode.provider.auth.save_session_key",
                side_effect=self.saved.append,
            ),
            patch("llm_meter.providers.opencode.provider.config_module.save_config"),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)

    def entry(self, label: str):
        return next(item for item in self.provider.menu() if item.label == label)

    def test_a_signed_out_card_offers_both_ways_to_add_a_key(self):
        labels = [entry.label for entry in self.provider.menu()]

        self.assertIn("Paste session key from clipboard", labels)
        self.assertIn("Enter session key...", labels)
        self.assertNotIn("Sign out", labels)

    def test_a_signed_in_card_hides_the_session_key_entries(self):
        self.ui.answer = "s" * 40
        self.provider.primary_action().run()

        labels = [entry.label for entry in self.provider.menu()]

        self.assertNotIn("Paste session key from clipboard", labels)
        self.assertNotIn("Enter session key...", labels)
        self.assertIn("Sign out", labels)

    def test_pasting_a_cookie_value_stores_the_key_and_refreshes(self):
        self.ui.clipboard = "auth=" + "k" * 40 + ";"

        self.entry("Paste session key from clipboard").run()

        self.assertEqual(self.saved, ["k" * 40])
        self.assertEqual(self.ui.refresh_requests, ["opencode"])
        self.assertTrue(self.provider.is_authenticated())

    def test_clipboard_junk_is_refused(self):
        self.ui.clipboard = "hello world"

        self.entry("Paste session key from clipboard").run()

        self.assertEqual(self.saved, [])
        self.assertEqual(self.provider.message, "The clipboard does not look like a session key")

    def test_the_prompt_explains_where_to_find_the_cookie(self):
        self.ui.answer = "s" * 40

        self.provider.primary_action().run()

        title, prompt, secret = self.ui.prompts[0]
        self.assertEqual(title, "OpenCode session key")
        self.assertIn("DevTools", prompt)
        self.assertTrue(secret)
        self.assertEqual(self.saved, ["s" * 40])

    def test_cancelling_the_prompt_changes_nothing(self):
        self.ui.answer = None

        self.provider.primary_action().run()

        self.assertEqual(self.saved, [])
        self.assertEqual(self.provider.message, "")

    def test_a_rejected_session_key_signs_the_card_out(self):
        self.ui.answer = "s" * 40
        self.provider.primary_action().run()
        self.provider.settings["workspace_id"] = "wrk_1"

        with patch(
            "llm_meter.providers.opencode.provider.auth.delete_session_key"
        ) as delete, patch(
            "llm_meter.providers.opencode.provider.api.fetch_console",
            side_effect=opencode_api.AuthExpiredError("gone"),
        ):
            self.provider.refresh()

        delete.assert_called_once()
        self.assertIs(self.provider.state, State.SIGNED_OUT)
        self.assertTrue(self.ui.notified)

    def test_the_pages_open_even_before_a_workspace_is_known(self):
        self.entry("Open Go usage page").run()
        self.entry("Open Zen credits page").run()
        self.entry("Open stats page").run()

        self.assertEqual(self.ui.opened, [opencode_api.CONSOLE_BASE] * 3)

    def test_the_pages_use_the_workspace_once_it_is_known(self):
        self.provider.settings["workspace_id"] = "wrk_1"

        self.entry("Open Go usage page").run()
        self.entry("Open Zen credits page").run()
        self.entry("Open stats page").run()

        self.assertEqual(
            self.ui.opened,
            [
                opencode_api.go_page("wrk_1"),
                opencode_api.zen_page("wrk_1"),
                opencode_api.stats_page("wrk_1"),
            ],
        )


if __name__ == "__main__":
    unittest.main()
