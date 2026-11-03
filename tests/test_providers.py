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
from llm_meter.providers.cursor import api as cursor_api
from llm_meter.providers.cursor.provider import CursorProvider, Loaded as CursorLoaded
from llm_meter.providers.ollama import api as ollama_api
from llm_meter.providers.ollama.provider import OllamaProvider
from llm_meter.providers.openrouter import api as openrouter_api
from llm_meter.providers.openrouter.provider import (
    Loaded as OpenRouterLoaded,
    OpenRouterProvider,
)
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


def go_usage(**overrides) -> opencode_api.GoUsage:
    data = opencode_api.GoUsage(
        rolling=opencode_api.UsageWindow(53, "ok", in_hours(1.025)),
        weekly=opencode_api.UsageWindow(20, "ok", in_hours(30)),
        monthly=opencode_api.UsageWindow(10, "ok", in_hours(111)),
    )
    for key, value in overrides.items():
        setattr(data, key, value)
    return data


class RegistryTests(unittest.TestCase):
    def test_providers_are_built_in_display_order(self):
        providers = build_providers(Config(), FakeUi())

        self.assertEqual(
            [provider.id for provider in providers],
            ["codex", "opencode", "cursor", "ollama", "openrouter"],
        )
        self.assertTrue(all(provider.enabled for provider in providers))

    def test_a_saved_order_is_honoured(self):
        cfg = Config(provider_order=["opencode", "codex"])

        providers = build_providers(cfg, FakeUi())

        self.assertEqual(
            [provider.id for provider in providers],
            ["opencode", "codex", "cursor", "ollama", "openrouter"],
        )

    def test_a_disabled_service_reports_itself_as_such(self):
        cfg = Config()
        cfg.set_provider_enabled("codex", False)

        providers = {provider.id: provider for provider in build_providers(cfg, FakeUi())}

        self.assertFalse(providers["codex"].enabled)
        self.assertTrue(providers["opencode"].enabled)
        self.assertTrue(providers["cursor"].enabled)


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

    def test_the_gauge_follows_the_shortest_window(self):
        snapshot = self.provider.render(codex_usage())

        # The fastest-moving allowance gates the tray needle: the 5h window.
        self.assertEqual(snapshot.gauge_percent, 12)
        self.assertEqual(snapshot.badge, "plus")
        self.assertEqual(snapshot.sections[0].url, codex_api.USAGE_PAGE)

    def test_each_window_shows_its_remaining_share_and_countdown(self):
        metrics = self.provider.render(codex_usage()).sections[0].metrics

        self.assertEqual([metric.label for metric in metrics], ["5h", "Weekly"])
        # The fuel gauge reads what is left: 85% used leaves 15%.
        self.assertEqual(metrics[1].value, "15% / 100%")
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
        self.provider = OpenCodeProvider(self.cfg, self.ui)

    def render(self, data=None):
        return self.provider.render(Loaded(go=data or go_usage()))

    def test_used_percentages_drive_the_metrics_and_shortest_window_gauge(self):
        snapshot = self.render()
        self.assertEqual(len(snapshot.sections), 1)
        metrics = snapshot.sections[0].metrics
        self.assertEqual([metric.percent for metric in metrics], [53, 20, 10])
        self.assertEqual([metric.value for metric in metrics],
                         ["53.0% used", "20.0% used", "10.0% used"])
        self.assertEqual(snapshot.gauge_percent, 53)

    def test_countdowns_use_absolute_resets_and_shrink_without_a_fetch(self):
        now = datetime(2026, 11, 3, 12, tzinfo=timezone.utc)
        data = Loaded(go=go_usage(
            rolling=opencode_api.UsageWindow(1.25, "ok", now + timedelta(hours=1)),
        ))
        with patch("llm_meter.providers.opencode.provider.datetime") as clock:
            clock.now.return_value = now
            initial = self.provider.render(data).sections[0].metrics[0]
            clock.now.return_value = now + timedelta(minutes=30)
            later = self.provider.render(data).sections[0].metrics[0]
            clock.now.return_value = now + timedelta(hours=2)
            expired = self.provider.render(data).sections[0].metrics[0]
        self.assertEqual(initial.detail, "resets in 1h")
        self.assertEqual(later.detail, "resets in 30m")
        self.assertEqual(expired.detail, "resets in <1m")

    def test_rate_limited_below_100_percent_is_visible_without_faking_usage(self):
        metric = self.render(go_usage(
            rolling=opencode_api.UsageWindow(33, "rate-limited", in_hours(1)),
        )).sections[0].metrics[0]
        self.assertEqual(metric.percent, 33)
        self.assertIn("rate-limited", metric.detail)
        self.assertEqual(metric.value, "33.0% used")

    def test_zero_and_full_usage_are_not_inverted(self):
        snapshot = self.render(go_usage(
            rolling=opencode_api.UsageWindow(0, "ok", in_hours(1)),
            monthly=opencode_api.UsageWindow(100, "rate-limited", in_hours(2)),
        ))
        self.assertEqual(snapshot.gauge_percent, 0)
        self.assertEqual(snapshot.sections[0].metrics[0].value, "0.0% used")
        self.assertEqual(snapshot.sections[0].metrics[2].value, "100.0% used")


class OpenCodeKeyTests(unittest.TestCase):
    KEY = "oc_sk_" + "k" * 40

    def setUp(self):
        self.ui = FakeUi()
        self.provider = OpenCodeProvider(Config(), self.ui)
        self.saved = []
        for patcher in (
            patch("llm_meter.providers.opencode.provider.auth.load_key", return_value=None),
            patch("llm_meter.providers.opencode.provider.auth.save_key", side_effect=self.saved.append),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)

    def entry(self, label):
        return next(item for item in self.provider.menu() if item.label == label)

    def adopt(self):
        self.ui.answer = self.KEY
        self.provider.primary_action().run()

    def test_pasting_a_bearer_header_authenticates_and_requests_a_refresh(self):
        self.ui.clipboard = "Authorization: Bearer " + self.KEY
        self.entry("Paste API key from clipboard").run()
        self.assertEqual(self.saved, [self.KEY])
        self.assertEqual(self.ui.refresh_requests, ["opencode"])
        self.assertTrue(self.provider.is_authenticated())

    def test_a_cookie_pair_does_not_authenticate_or_store_a_key(self):
        self.ui.clipboard = "auth=" + "k" * 40 + ";"
        self.entry("Paste API key from clipboard").run()
        self.assertEqual(self.saved, [])
        self.assertFalse(self.provider.is_authenticated())

    def test_cancelling_key_replacement_keeps_the_existing_login(self):
        self.adopt()
        self.ui.answer = None
        self.provider.primary_action().run()
        self.assertEqual(self.provider.api_key, self.KEY)
        self.assertEqual(self.saved, [self.KEY])

    def test_a_rejected_key_discards_the_snapshot_and_signs_out(self):
        self.adopt()
        self.provider.snapshot = self.provider.render(Loaded(go=go_usage()))
        with patch("llm_meter.providers.opencode.provider.auth.delete_key"), patch(
            "llm_meter.providers.opencode.provider.api.fetch_usage",
            side_effect=opencode_api.AuthExpiredError("gone"),
        ):
            self.provider.refresh()
        self.assertIs(self.provider.state, State.SIGNED_OUT)
        self.assertIsNone(self.provider.snapshot)
        self.assertFalse(self.provider.is_authenticated())
        self.assertTrue(self.ui.notified)

    def test_forbidden_network_and_bad_data_keep_the_key_and_last_reading(self):
        import requests
        for error in (opencode_api.ScopeError("forbidden"), requests.Timeout("timeout"),
                      opencode_api.ParseError("bad payload"), opencode_api.FetchError("HTTP 500")):
            with self.subTest(error=error):
                self.adopt()
                previous = self.provider.render(Loaded(go=go_usage()))
                self.provider.snapshot = previous
                with patch("llm_meter.providers.opencode.provider.auth.delete_key") as delete, patch(
                    "llm_meter.providers.opencode.provider.api.fetch_usage", side_effect=error,
                ):
                    self.provider.refresh()
                self.assertIs(self.provider.state, State.ERROR)
                self.assertTrue(self.provider.is_authenticated())
                self.assertIs(self.provider.snapshot, previous)
                delete.assert_not_called()

    def test_replacing_a_key_discards_the_other_accounts_cached_reading(self):
        self.adopt()
        self.provider.data = Loaded(go=go_usage())
        self.provider.snapshot = self.provider.render(self.provider.data)
        self.ui.answer = "oc_sk_" + "n" * 40
        self.provider.primary_action().run()
        self.assertIsNone(self.provider.snapshot)
        self.assertIsNone(self.provider.data)
        self.assertEqual(self.provider.api_key, self.ui.answer)


def ollama_cloud(**overrides) -> ollama_api.CloudData:
    data = ollama_api.CloudData(
        balance=ollama_api.BalanceData(
            windows=[ollama_api.UsageWindow("monthly", 27.5, in_hours(96), 72.5, 100)],
            purchased=25,
        ),
        history=ollama_api.UsageHistory(20, [ollama_api.ModelUsage("glm-5.3-flash", 12)], 8),
    )
    for key, value in overrides.items():
        setattr(data, key, value)
    return data


class OllamaRenderTests(unittest.TestCase):
    def setUp(self):
        self.provider = OllamaProvider(Config(), FakeUi())

    def test_included_and_purchased_balances_are_distinct(self):
        snapshot = self.provider.render(ollama_cloud())
        included = snapshot.sections[0].metrics[0]
        purchased = snapshot.sections[1].metrics[0]
        self.assertEqual(included.value, "$72.50 of $100")
        self.assertEqual(purchased.value, "$25.00")
        self.assertEqual(included.percent, 27.5)
        self.assertEqual(snapshot.gauge_percent, 27.5)
        self.assertIsNone(snapshot.badge)  # The API does not report a plan name.

    def test_legacy_limits_show_remaining_but_send_used_percentage_to_the_gauge(self):
        balance = ollama_api.BalanceData([
            ollama_api.UsageWindow("session", 25, in_hours(1)),
            ollama_api.UsageWindow("weekly", 60, in_hours(48)),
        ], 0)
        snapshot = self.provider.render(ollama_cloud(balance=balance))
        self.assertEqual([metric.percent for metric in snapshot.sections[0].metrics], [25, 60])
        self.assertEqual([metric.value for metric in snapshot.sections[0].metrics],
                         ["75.0% remaining", "40.0% remaining"])
        self.assertEqual(snapshot.gauge_percent, 25)
        self.assertTrue(snapshot.sections[1].metrics[0].muted)

    def test_unknown_included_allowance_does_not_invent_a_free_usage_percentage(self):
        balance = ollama_api.BalanceData([], 0, "unknown included shape")
        snapshot = self.provider.render(ollama_cloud(balance=balance))
        self.assertEqual(snapshot.sections[0].metrics, [])
        self.assertIsNotNone(snapshot.sections[0].note)
        self.assertIsNone(snapshot.gauge_percent)
        self.assertEqual(snapshot.sections[1].metrics[0].value, "$0.00")

    def test_zero_allowance_has_no_gauge(self):
        balance = ollama_api.BalanceData([
            ollama_api.UsageWindow("monthly", None, in_hours(1), 0, 0),
        ], 0)
        snapshot = self.provider.render(ollama_cloud(balance=balance))
        self.assertIsNone(snapshot.gauge_percent)
        self.assertIsNone(snapshot.sections[0].metrics[0].percent)

    def test_history_is_labeled_as_rolling_30_days_and_keeps_the_remainder(self):
        section = self.provider.render(ollama_cloud()).sections[2]
        self.assertEqual(section.title, "Requests in the last 30 days")
        self.assertEqual([metric.value for metric in section.metrics], ["20 req", "12 req", "8 req"])
        self.assertEqual(section.metrics[-1].label, "Other models")

    def test_failed_history_does_not_remove_the_balance_sections(self):
        snapshot = self.provider.render(ollama_cloud(history=None, history_error="HTTP 503"))
        self.assertEqual(snapshot.sections[1].metrics[0].value, "$25.00")
        self.assertEqual(snapshot.sections[2].metrics, [])
        self.assertIsNotNone(snapshot.sections[2].note)

    def test_countdowns_are_recomputed_without_a_network_fetch(self):
        now = datetime(2026, 11, 3, 12, tzinfo=timezone.utc)
        balance = ollama_api.BalanceData([
            ollama_api.UsageWindow("monthly", 25, now + timedelta(hours=1), 75, 100),
        ], 0)
        data = ollama_cloud(balance=balance)
        with patch("llm_meter.providers.ollama.provider.datetime") as clock:
            clock.now.return_value = now
            first = self.provider.render(data).sections[0].metrics[0]
            clock.now.return_value = now + timedelta(minutes=30)
            later = self.provider.render(data).sections[0].metrics[0]
        self.assertEqual(first.detail, "resets in 1h")
        self.assertEqual(later.detail, "resets in 30m")


class OllamaKeyTests(unittest.TestCase):
    KEY = "ollama-key." + "k" * 32

    def setUp(self):
        self.ui = FakeUi()
        self.provider = OllamaProvider(Config(), self.ui)
        self.saved = []
        for patcher in (
            patch("llm_meter.providers.ollama.provider.auth.load_key", return_value=None),
            patch("llm_meter.providers.ollama.provider.auth.save_key", side_effect=self.saved.append),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)

    def adopt(self):
        self.ui.answer = self.KEY
        self.provider.primary_action().run()

    def test_pasted_bearer_key_authenticates_and_requests_a_refresh(self):
        self.ui.clipboard = "Authorization: Bearer " + self.KEY
        entry = next(item for item in self.provider.menu() if item.label == "Paste API key from clipboard")
        entry.run()
        self.assertEqual(self.saved, [self.KEY])
        self.assertEqual(self.ui.refresh_requests, ["ollama"])
        self.assertTrue(self.provider.is_authenticated())

    def test_cancelling_replacement_keeps_the_existing_key(self):
        self.adopt()
        self.ui.answer = None
        self.provider.primary_action().run()
        self.assertEqual(self.provider.api_key, self.KEY)
        self.assertEqual(self.saved, [self.KEY])

    def test_401_signs_out_and_discards_the_snapshot(self):
        self.adopt()
        self.provider.snapshot = self.provider.render(ollama_cloud())
        with patch("llm_meter.providers.ollama.provider.auth.delete_key"), patch(
            "llm_meter.providers.ollama.provider.api.fetch_cloud",
            side_effect=ollama_api.AuthExpiredError("rejected"),
        ):
            self.provider.refresh()
        self.assertIs(self.provider.state, State.SIGNED_OUT)
        self.assertIsNone(self.provider.snapshot)
        self.assertFalse(self.provider.is_authenticated())

    def test_non_auth_errors_keep_the_key_and_previous_reading(self):
        import requests
        for error in (ollama_api.ScopeError("suspended"), ollama_api.RateLimitError(60),
                      ollama_api.FetchError("503"), ollama_api.ParseError("bad"),
                      requests.Timeout("timeout")):
            with self.subTest(error=error):
                self.adopt()
                previous = self.provider.render(ollama_cloud())
                self.provider.snapshot = previous
                with patch("llm_meter.providers.ollama.provider.auth.delete_key") as delete, patch(
                    "llm_meter.providers.ollama.provider.api.fetch_cloud", side_effect=error,
                ):
                    self.provider.refresh()
                self.assertIs(self.provider.state, State.ERROR)
                self.assertIs(self.provider.snapshot, previous)
                self.assertTrue(self.provider.is_authenticated())
                delete.assert_not_called()

    def test_key_replacement_clears_the_other_accounts_cached_data(self):
        self.adopt()
        self.provider.data = ollama_cloud()
        self.provider.snapshot = self.provider.render(self.provider.data)
        self.ui.answer = "ollama-key." + "n" * 32
        self.provider.primary_action().run()
        self.assertIsNone(self.provider.snapshot)
        self.assertIsNone(self.provider.data)
        self.assertEqual(self.provider.api_key, self.ui.answer)


def openrouter_credits(**overrides) -> openrouter_api.CreditsData:
    data = openrouter_api.CreditsData(total_credits=100.5, total_usage=25.75)
    for key, value in overrides.items():
        setattr(data, key, value)
    return data


class OpenRouterRenderTests(unittest.TestCase):
    def setUp(self):
        self.ui = FakeUi()
        self.provider = OpenRouterProvider(Config(), self.ui)

    def render(self, credits=None):
        return self.provider.render(
            OpenRouterLoaded(credits=credits or openrouter_credits())
        )

    def test_credits_are_a_single_section(self):
        snapshot = self.render()

        # The metric row names itself "Credits", so the section carries no title.
        self.assertEqual([section.title for section in snapshot.sections], [""])
        self.assertEqual(snapshot.sections[0].url, openrouter_api.CREDITS_PAGE)
        self.assertIsNone(snapshot.badge)

    def test_the_section_shows_current_and_total_purchased_balances(self):
        metrics = self.render().sections[0].metrics

        self.assertEqual([metric.label for metric in metrics], ["Credits", "Total purchased"])
        self.assertEqual(metrics[0].value, "$74.75")
        self.assertFalse(metrics[0].muted)
        self.assertTrue(metrics[0].featured)
        self.assertIsNone(metrics[0].percent)
        self.assertEqual(metrics[1].value, "$100.50")
        self.assertFalse(metrics[1].featured)

    def test_openrouter_has_no_tray_gauge_value(self):
        snapshot = self.render()

        self.assertIsNone(snapshot.gauge_percent)

    def test_a_drained_balance_is_muted(self):
        credits = openrouter_credits(total_credits=25.75, total_usage=25.75)

        metrics = self.render(credits).sections[0].metrics

        self.assertEqual(metrics[0].value, "$0.00")
        self.assertTrue(metrics[0].muted)
        self.assertIsNone(metrics[0].percent)

    def test_usage_over_the_purchased_credits_still_displays_zero(self):
        credits = openrouter_credits(total_credits=10, total_usage=12)

        metrics = self.render(credits).sections[0].metrics

        self.assertEqual(metrics[0].value, "$0.00")
        self.assertIsNone(metrics[0].percent)
        self.assertIsNone(self.render(credits).gauge_percent)

    def test_no_purchased_credit_displays_zero_balance(self):
        credits = openrouter_credits(total_credits=0, total_usage=0)

        snapshot = self.render(credits)

        metrics = snapshot.sections[0].metrics
        self.assertEqual(metrics[0].value, "$0.00")
        self.assertTrue(metrics[0].muted)
        self.assertIsNone(metrics[0].percent)
        self.assertIsNone(snapshot.gauge_percent)

    def test_korean_label_reads_as_credits(self):
        with patch.dict("os.environ", {"LLM_METER_LANG": "ko"}):
            metrics = self.render().sections[0].metrics

            self.assertEqual([metric.label for metric in metrics], ["크레딧", "누적 충전"])
            self.assertEqual(metrics[0].value, "$74.75")


class OpenRouterSessionTests(unittest.TestCase):
    KEY = "sk-or-v1-" + "k" * 25

    def setUp(self):
        self.ui = FakeUi()
        self.provider = OpenRouterProvider(Config(), self.ui)
        self.saved: list[str] = []
        # Nothing here may touch the real credential store, and the card must
        # start signed out however the developer's keychain looks.
        for patcher in (
            patch("llm_meter.providers.openrouter.provider.auth.load_key", return_value=None),
            patch(
                "llm_meter.providers.openrouter.provider.auth.save_key",
                side_effect=self.saved.append,
            ),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)

    def entry(self, label: str):
        return next(item for item in self.provider.menu() if item.label == label)

    def test_a_signed_out_card_offers_both_ways_to_add_a_key(self):
        labels = [entry.label for entry in self.provider.menu()]

        self.assertIn("Paste API key from clipboard", labels)
        self.assertIn("Enter API key...", labels)
        self.assertNotIn("Sign out", labels)

    def test_a_signed_in_card_hides_the_key_entries(self):
        self.ui.answer = self.KEY
        self.provider.primary_action().run()

        labels = [entry.label for entry in self.provider.menu()]

        self.assertNotIn("Paste API key from clipboard", labels)
        self.assertNotIn("Enter API key...", labels)
        self.assertIn("Sign out", labels)

    def test_pasting_a_bearer_header_stores_the_key_and_refreshes(self):
        self.ui.clipboard = "Bearer " + self.KEY

        self.entry("Paste API key from clipboard").run()

        self.assertEqual(self.saved, [self.KEY])
        self.assertEqual(self.ui.refresh_requests, ["openrouter"])
        self.assertTrue(self.provider.is_authenticated())

    def test_clipboard_junk_is_refused(self):
        self.ui.clipboard = "hello world"

        self.entry("Paste API key from clipboard").run()

        self.assertEqual(self.saved, [])
        self.assertEqual(
            self.provider.message, "The clipboard does not look like an API key"
        )

    def test_the_prompt_explains_where_to_find_the_key(self):
        self.ui.answer = self.KEY

        self.provider.primary_action().run()

        title, prompt, secret = self.ui.prompts[0]
        self.assertEqual(title, "OpenRouter API key")
        self.assertIn("settings/keys", prompt)
        self.assertTrue(secret)
        self.assertEqual(self.saved, [self.KEY])

    def test_cancelling_the_prompt_changes_nothing(self):
        self.ui.answer = None

        self.provider.primary_action().run()

        self.assertEqual(self.saved, [])
        self.assertEqual(self.provider.message, "")

    def test_a_rejected_key_signs_the_card_out(self):
        self.ui.answer = self.KEY
        self.provider.primary_action().run()

        with patch(
            "llm_meter.providers.openrouter.provider.auth.delete_key"
        ) as delete, patch(
            "llm_meter.providers.openrouter.provider.api.fetch_credits",
            side_effect=openrouter_api.AuthExpiredError("gone"),
        ):
            self.provider.refresh()

        delete.assert_called_once()
        self.assertIs(self.provider.state, State.SIGNED_OUT)
        self.assertTrue(self.ui.notified)

    def test_a_key_without_credit_access_is_kept_and_reported(self):
        self.ui.answer = self.KEY
        self.provider.primary_action().run()

        with patch(
            "llm_meter.providers.openrouter.provider.auth.delete_key"
        ) as delete, patch(
            "llm_meter.providers.openrouter.provider.api.fetch_credits",
            side_effect=openrouter_api.ScopeError("HTTP 403"),
        ):
            self.provider.refresh()

        delete.assert_not_called()
        self.assertIs(self.provider.state, State.ERROR)
        self.assertIn("cannot read credits", self.provider.message)
        self.assertTrue(self.provider.is_authenticated())

    def test_the_pages_open(self):
        self.entry("Open credits page").run()
        self.entry("Open activity page").run()
        self.entry("Open logs page").run()

        self.assertEqual(
            self.ui.opened,
            [openrouter_api.CREDITS_PAGE, openrouter_api.ACTIVITY_PAGE, openrouter_api.LOGS_PAGE],
        )


def cursor_usage(**overrides) -> cursor_api.UsageData:
    data = cursor_api.parse_usage_summary(
        {
            "billingCycleStart": "2026-07-23T01:15:44.000Z",
            "billingCycleEnd": "2026-08-23T01:15:44.000Z",
            "membershipType": "pro",
            "isUnlimited": False,
            "autoModelSelectedDisplayMessage": "You've used 20% of your included total usage",
            "individualUsage": {
                "plan": {
                    "enabled": True,
                    "used": 2000,
                    "limit": 2000,
                    "remaining": 0,
                    "breakdown": {"included": 2000, "bonus": 4981, "total": 6981},
                    "autoPercentUsed": 8.3,
                    "apiPercentUsed": 99.5,
                    "totalPercentUsed": 20.2,
                },
                "onDemand": {"enabled": False, "used": 0, "limit": None, "remaining": None},
            },
        }
    )
    data.plan_name = "Pro"
    data.plan_price = "$20/mo"
    data.no_usage_based_allowed = True
    for key, value in overrides.items():
        setattr(data, key, value)
    return data


class CursorRenderTests(unittest.TestCase):
    def setUp(self):
        self.ui = FakeUi()
        self.provider = CursorProvider(Config(), self.ui)
        patcher = patch(
            "llm_meter.providers.cursor.provider.auth.resolve_session",
            return_value=None,
        )
        patcher.start()
        self.addCleanup(patcher.stop)

    def render(self, usage: cursor_api.UsageData | None = None):
        loaded = CursorLoaded(usage=usage or cursor_usage(), monotonic=time.monotonic())
        return self.provider.render(loaded)

    def test_sections_cover_usage_and_spending(self):
        titles = [section.title for section in self.render().sections]

        self.assertEqual(titles, ["Plan usage", "Spending"])

    def test_plan_usage_shows_total_auto_and_api(self):
        metrics = self.render().sections[0].metrics

        self.assertEqual(metrics[0].label, "Total")
        # The fuel gauge reads what is left: 20.2% used leaves ~80%.
        self.assertEqual(metrics[0].value, "80% / 100%")
        self.assertAlmostEqual(metrics[0].percent, 20.2)
        self.assertIn("resets in", metrics[0].detail)
        self.assertEqual(metrics[1].label, "Auto")
        self.assertEqual(metrics[2].label, "API")
        self.assertEqual(metrics[2].value, "0% / 100%")
        self.assertIsNone(self.render().sections[0].note)

    def test_spending_shows_included_bonus_and_on_demand(self):
        metrics = self.render().sections[1].metrics

        self.assertEqual(metrics[0].label, "Included")
        self.assertEqual(metrics[0].value, "$0.00 / $20")
        self.assertAlmostEqual(metrics[0].percent, 100.0)
        self.assertEqual(metrics[1].label, "Bonus")
        self.assertEqual(metrics[1].value, "$49.81")
        self.assertEqual(metrics[2].label, "On-demand")
        self.assertEqual(metrics[2].value, "Off")
        self.assertEqual(len(metrics), 3)

    def test_badge_and_gauge_follow_the_plan(self):
        snapshot = self.render()

        self.assertEqual(snapshot.badge, "Pro")
        self.assertAlmostEqual(snapshot.gauge_percent, 20.2)

    def test_on_demand_spend_is_shown_when_enabled(self):
        usage = cursor_usage()
        usage.no_usage_based_allowed = False
        usage.on_demand = cursor_api.OnDemandUsage(
            enabled=True, used_cents=1234, limit_cents=5000
        )

        metric = self.render(usage).sections[1].metrics[2]

        self.assertEqual(metric.label, "On-demand")
        self.assertEqual(metric.value, "$37.66 / $50")
        self.assertAlmostEqual(metric.percent, 24.68)

    def test_menu_opens_usage_and_spending_pages(self):
        labels = {entry.label: entry for entry in self.provider.menu()}

        labels["Open usage page"].run()
        labels["Open spending page"].run()

        self.assertEqual(self.ui.opened, [cursor_api.USAGE_PAGE, cursor_api.SPENDING_PAGE])


if __name__ == "__main__":
    unittest.main()
