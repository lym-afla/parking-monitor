import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from command_service import CommandService


MSK = timezone(timedelta(hours=3), "MSK")
EMPTY_HEALTH = {}


class CommandServiceTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.state_path = Path(self.temporary_directory.name) / "state.json"
        self.state_path.write_text(
            json.dumps(
                {
                    "checks": 12,
                    "hits": 3,
                    "last_check": "2026-08-27T11:30:00+03:00",
                    "last_enabled": False,
                    "interval": 1800,
                    "monitor_metadata": {"version": 1},
                }
            ),
            encoding="utf-8",
        )
        self.service = CommandService(
            self.state_path, health_provider=lambda: EMPTY_HEALTH
        )

    def tearDown(self):
        self.temporary_directory.cleanup()

    def test_status_reports_month_end_override_without_changing_normal_interval(self):
        status = self.service.status(datetime(2026, 8, 27, 12, 0, tzinfo=MSK))

        self.assertEqual(status.polling_mode, "month-end")
        self.assertEqual(status.effective_interval_seconds, 300)
        self.assertEqual(status.normal_interval_seconds, 1800)
        self.assertEqual(
            status.next_expected_check,
            "2026-08-27T11:35:00+03:00",
        )

    def test_set_interval_atomically_updates_normal_interval(self):
        status = self.service.set_normal_interval(900)

        self.assertEqual(status.normal_interval_seconds, 900)
        self.assertEqual(json.loads(self.state_path.read_text(encoding="utf-8"))["interval"], 900)

    def test_set_interval_preserves_unrelated_state_fields(self):
        self.service.set_normal_interval(900)

        state = json.loads(self.state_path.read_text(encoding="utf-8"))
        self.assertEqual(state["checks"], 12)
        self.assertEqual(state["monitor_metadata"], {"version": 1})

    def test_rejects_interval_outside_allowed_range(self):
        with self.assertRaisesRegex(ValueError, "60.*86400"):
            self.service.set_normal_interval(30)

    def test_rejects_fractional_interval(self):
        with self.assertRaisesRegex(ValueError, "integer"):
            self.service.set_normal_interval(60.5)

    def test_rejects_boolean_interval(self):
        with self.assertRaisesRegex(ValueError, "integer"):
            self.service.set_normal_interval(True)

    def test_accepts_interval_range_endpoints(self):
        self.assertEqual(
            self.service.set_normal_interval(60).normal_interval_seconds,
            60,
        )
        self.assertEqual(
            self.service.set_normal_interval(86400).normal_interval_seconds,
            86400,
        )

    def test_stats_reports_monitor_totals_and_channel_health(self):
        health = {
            "telegram": {
                "last_delivered_at": "2026-08-27T11:00:00+03:00",
                "state": "retrying",
                "delivered_count": 7,
                "pending_count": 1,
                "retrying_count": 2,
                "failed_count": 3,
            }
        }
        service = CommandService(self.state_path, health_provider=lambda: health)

        stats = service.stats()

        self.assertEqual((stats.checks, stats.hits), (12, 3))
        self.assertEqual(stats.channel_health["telegram"].pending_count, 1)
        self.assertEqual(stats.channel_health["telegram"].failed_count, 3)
        self.assertEqual(stats.channel_health["telegram"].delivered_count, 7)
        self.assertEqual(stats.channel_health["telegram"].state, "retrying")


if __name__ == "__main__":
    unittest.main()
