import unittest
from datetime import datetime, timedelta, timezone

from monitor import get_polling_schedule


MOSCOW = timezone(timedelta(hours=3), "MSK")


class PollingScheduleTests(unittest.TestCase):
    def test_uses_five_minutes_at_start_of_month_end_window(self):
        interval, mode = get_polling_schedule(
            datetime(2026, 8, 25, 0, 0, tzinfo=MOSCOW)
        )

        self.assertEqual((interval, mode), (300, "month-end"))

    def test_returns_to_normal_at_end_of_month_end_window(self):
        interval, mode = get_polling_schedule(
            datetime(2026, 8, 29, 0, 0, tzinfo=MOSCOW)
        )

        self.assertEqual((interval, mode), (1800, "normal"))

    def test_caps_normal_sleep_at_start_of_month_end_window(self):
        interval, mode = get_polling_schedule(
            datetime(2026, 8, 24, 23, 40, 0, tzinfo=MOSCOW)
        )

        self.assertEqual((interval, mode), (1200, "normal"))

    def test_calculates_february_window_in_a_leap_year(self):
        interval, mode = get_polling_schedule(
            datetime(2028, 2, 23, 12, 0, tzinfo=MOSCOW)
        )

        self.assertEqual((interval, mode), (300, "month-end"))


if __name__ == "__main__":
    unittest.main()
