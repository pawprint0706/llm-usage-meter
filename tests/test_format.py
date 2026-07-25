import os
import unittest
from datetime import datetime, timedelta, timezone

from llm_meter import format as fmt


class DurationTests(unittest.TestCase):
    def test_sub_minute_is_not_reported_as_zero(self):
        self.assertEqual(fmt.duration(45), "<1m")

    def test_minutes_hours_and_days(self):
        self.assertEqual(fmt.duration(20 * 60), "20m")
        self.assertEqual(fmt.duration(3 * 3600 + 39 * 60), "3h 39m")
        self.assertEqual(fmt.duration(5 * 3600), "5h")
        self.assertEqual(fmt.duration(3 * 86400 + 5 * 3600), "3d 5h")
        self.assertEqual(fmt.duration(2 * 86400), "2d")

    def test_negative_is_clamped(self):
        self.assertEqual(fmt.duration(-90), "<1m")

    def test_korean_units(self):
        os.environ["LLM_METER_LANG"] = "ko"
        try:
            self.assertEqual(fmt.duration(3 * 3600 + 39 * 60), "3시간 39분")
            self.assertEqual(fmt.duration(3 * 86400 + 5 * 3600), "3일 5시간")
        finally:
            os.environ["LLM_METER_LANG"] = "en"


class TimestampTests(unittest.TestCase):
    def test_english_timestamp_is_local_wall_clock(self):
        moment = datetime(2026, 8, 13, 2, 25, tzinfo=timezone.utc)
        expected = moment.astimezone().strftime("%Y-%m-%d %H:%M")
        self.assertEqual(fmt.timestamp(moment), expected)

    def test_korean_timestamp_uses_am_pm_words(self):
        os.environ["LLM_METER_LANG"] = "ko"
        try:
            local = datetime.now().astimezone().tzinfo
            morning = datetime(2026, 8, 13, 9, 5, tzinfo=local)
            evening = morning + timedelta(hours=8)
            self.assertEqual(fmt.timestamp(morning), "2026. 8. 13. 오전 9:05")
            self.assertEqual(fmt.timestamp(evening), "2026. 8. 13. 오후 5:05")
        finally:
            os.environ["LLM_METER_LANG"] = "en"

    def test_clock_from_epoch(self):
        moment = datetime(2026, 7, 26, 2, 30, 45).astimezone()
        self.assertEqual(fmt.clock(moment.timestamp()), "07-26 02:30:45")


class MoneyTests(unittest.TestCase):
    def test_money_keeps_cents_and_groups_thousands(self):
        self.assertEqual(fmt.money(5.7), "$5.70")
        self.assertEqual(fmt.money(1234.5), "$1,234.50")

    def test_compact_drops_cents_only_for_round_amounts(self):
        self.assertEqual(fmt.money_compact(30), "$30")
        self.assertEqual(fmt.money_compact(12.0), "$12")
        self.assertEqual(fmt.money_compact(16.13), "$16.13")

    def test_percent_is_rounded_to_whole_numbers(self):
        self.assertEqual(fmt.percent(84.6), "85%")


if __name__ == "__main__":
    unittest.main()
