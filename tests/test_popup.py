"""Widget-level tests. Qt runs on the offscreen platform, so no display is needed."""

import os
import time
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QRect
from PySide6.QtGui import QColor, QGuiApplication
from PySide6.QtWidgets import QApplication, QLabel, QPushButton

from tests._support import FakeUi, install_keyring_stub
from tests.test_providers import codex_usage, console

install_keyring_stub()

from llm_meter.config import Config
from llm_meter.providers import build_providers
from llm_meter.providers.base import State
from llm_meter.providers.opencode.provider import Loaded
from llm_meter.ui import glyphs
from llm_meter.ui.popup import PopupWindow
from llm_meter.ui.widgets import ProviderCard

_qt_app = None


def setUpModule():
    global _qt_app
    _qt_app = QApplication.instance() or QApplication([])


class FakeHost:
    """The slice of MeterApp the popup talks to."""

    def __init__(self, providers):
        self.providers = providers
        self.opened: list[str] = []
        self.quit_calls = 0
        self.refresh_calls = 0

    def open_url(self, url: str) -> None:
        self.opened.append(url)

    def run_entry(self, entry) -> None:
        entry.run()

    def refresh_all(self) -> None:
        self.refresh_calls += 1

    def show_settings_menu(self, _anchor) -> None:
        pass

    def note_popup_hidden(self) -> None:
        pass

    def quit(self) -> None:
        self.quit_calls += 1


class PopupTestCase(unittest.TestCase):
    def setUp(self):
        self.ui = FakeUi()
        self.cfg = Config()
        self.providers = build_providers(self.cfg, self.ui)
        self.host = FakeHost(self.providers)
        self.popup = PopupWindow(self.host)
        self.addCleanup(self.popup.deleteLater)

    def make_ready(self):
        codex, opencode = self.providers
        codex.data = codex_usage()
        codex.snapshot = codex.render(codex.data)
        opencode.data = Loaded(console=console(), monotonic=time.monotonic())
        opencode.snapshot = opencode.render(opencode.data)
        for provider in self.providers:
            provider.state = State.READY
            provider.fetched_at = time.time()

    def labels(self) -> list[str]:
        return [label.text() for label in self.popup.findChildren(QLabel)]

    def cards(self) -> list[ProviderCard]:
        return self.popup.findChildren(ProviderCard)


class ContentTests(PopupTestCase):
    def test_one_card_per_enabled_service(self):
        self.make_ready()

        self.popup.rebuild()

        self.assertEqual(len(self.cards()), 2)
        self.assertIn("Codex", self.labels())
        self.assertIn("OpenCode", self.labels())

    def test_a_disabled_service_is_left_out(self):
        self.make_ready()
        self.cfg.set_provider_enabled("codex", False)

        self.popup.rebuild()

        self.assertEqual(len(self.cards()), 1)
        self.assertNotIn("Codex", self.labels())

    def test_every_section_is_rendered(self):
        self.make_ready()

        self.popup.rebuild()

        for title in ("Plan usage", "Credits", "Zen credits", "Go plan usage"):
            self.assertIn(title, self.labels())

    def test_a_signed_out_service_offers_its_sign_in_action(self):
        self.popup.rebuild()

        buttons = [button.text() for button in self.popup.findChildren(QPushButton)]
        self.assertIn("Sign in with OpenAI...", buttons)
        self.assertIn("Enter session key...", buttons)

    def test_an_error_is_shown_on_the_card(self):
        self.make_ready()
        self.providers[0].state = State.ERROR
        self.providers[0].message = "Network error · will retry"

        self.popup.rebuild()

        self.assertIn("Network error · will retry", self.labels())

    def test_turning_every_service_off_explains_the_empty_panel(self):
        for provider in self.providers:
            self.cfg.set_provider_enabled(provider.id, False)

        self.popup.rebuild()

        self.assertIn("Enable at least one service in settings", self.labels())

    def test_section_titles_are_plain_labels(self):
        """Page links live in the ⋯ menu; titles must not be clickable."""
        self.make_ready()
        self.popup.rebuild()

        title = next(
            label for label in self.popup.findChildren(QLabel) if label.text() == "Plan usage"
        )

        self.assertFalse(hasattr(title, "clicked"))
        self.assertNotIn("↗", self.labels())


class SizeTests(PopupTestCase):
    def test_the_panel_grows_to_fit_its_cards(self):
        self.make_ready()

        self.popup.rebuild()

        self.assertGreater(self.popup.height(), 300)

    def test_rebuilding_a_visible_popup_keeps_the_cards_and_the_height(self):
        """Regression: widgets added to a visible layout used to measure as hidden,
        collapsing the panel to its header and footer."""
        self.make_ready()
        self.popup.show_near(QRect(600, 0, 24, 24))
        self.addCleanup(self.popup.hide)
        tall = self.popup.height()

        self.popup.rebuild()

        self.assertEqual(len(self.cards()), 2)
        self.assertEqual(self.popup.height(), tall)

    def test_a_signed_out_panel_is_smaller_than_a_full_one(self):
        self.popup.rebuild()
        empty = self.popup.height()
        self.make_ready()
        self.popup.rebuild()

        self.assertLess(empty, self.popup.height())

    def test_the_panel_never_outgrows_the_screen(self):
        self.make_ready()
        self.popup.rebuild()

        available = QGuiApplication.primaryScreen().availableGeometry()
        self.assertLessEqual(self.popup.height(), available.height())


class PlacementTests(PopupTestCase):
    def test_the_panel_is_centred_under_the_tray_icon(self):
        self.make_ready()
        anchor = QRect(600, 0, 24, 24)

        self.popup.show_near(anchor)
        self.addCleanup(self.popup.hide)

        geometry = self.popup.geometry()
        self.assertAlmostEqual(geometry.center().x(), anchor.center().x(), delta=2)
        self.assertLess(geometry.top(), anchor.bottom() + 16)

    def test_the_panel_stays_on_screen_next_to_a_corner_icon(self):
        self.make_ready()
        available = QGuiApplication.primaryScreen().availableGeometry()

        self.popup.show_near(QRect(available.right() - 12, 0, 24, 24))
        self.addCleanup(self.popup.hide)

        # The window includes a transparent shadow margin, so allow for it.
        self.assertLessEqual(self.popup.geometry().right(), available.right() + 10)

    def test_without_a_tray_rectangle_it_opens_at_the_pointer(self):
        self.make_ready()

        self.popup.show_near(None)
        self.addCleanup(self.popup.hide)

        available = QGuiApplication.primaryScreen().availableGeometry()
        geometry = self.popup.geometry()
        self.assertGreaterEqual(geometry.left(), available.left() - 10)
        self.assertLessEqual(geometry.bottom(), available.bottom() + 10)


class GlyphTests(unittest.TestCase):
    def ink(self, pixmap) -> int:
        """Total opacity, so a dimmed dial counts for less than a lit one."""
        image = pixmap.toImage()
        return sum(
            image.pixelColor(x, y).alpha()
            for x in range(image.width())
            for y in range(image.height())
        )

    def test_the_gauge_is_drawn_at_every_tray_size(self):
        for size in (16, 22, 44):
            with self.subTest(size=size):
                pixmap = glyphs.gauge_pixmap(size, 85, QColor("#000000"))
                self.assertEqual(pixmap.size().width(), size)
                self.assertGreater(self.ink(pixmap), size)

    def test_the_needle_moves_with_the_percentage(self):
        low = glyphs.gauge_pixmap(44, 5, QColor("#000000")).toImage()
        high = glyphs.gauge_pixmap(44, 95, QColor("#000000")).toImage()

        self.assertNotEqual(low, high)

    def test_a_known_percentage_is_drawn_more_boldly_than_an_unknown_one(self):
        known = self.ink(glyphs.gauge_pixmap(44, 100, QColor("#000000")))
        unknown = self.ink(glyphs.gauge_pixmap(44, None, QColor("#000000")))

        self.assertGreater(known, unknown)

    def test_each_service_has_its_own_mark(self):
        codex = glyphs.provider_pixmap("codex", 18, QColor("#000000")).toImage()
        opencode = glyphs.provider_pixmap("opencode", 18, QColor("#000000")).toImage()

        self.assertFalse(codex.isNull())
        self.assertNotEqual(codex, opencode)


if __name__ == "__main__":
    unittest.main()
