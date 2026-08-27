import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from monitor import apply_check_result
from notification_store import NotificationStore


class MonitorEventTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary_directory.cleanup)
        self.store = NotificationStore(
            Path(self.temporary_directory.name) / "notifications.sqlite3"
        )

    def test_false_to_true_creates_one_dual_channel_event(self):
        state = {"last_enabled": False, "checks": 41, "hits": 3}

        updated = apply_check_result(state, enabled=True, store=self.store)

        self.assertEqual(self.store.event_count(), 1)
        self.assertEqual(
            {row.channel for row in self.store.deliveries_for(1)},
            {"telegram", "discord"},
        )
        claim = self.store.claim_due("telegram", datetime.now(timezone.utc))
        self.assertEqual(claim.event_type, "parking_available")
        self.assertEqual(claim.payload, {"available": True})
        self.assertEqual(claim.source_check, 42)
        self.assertEqual(updated["checks"], 42)
        self.assertEqual(updated["hits"], 4)
        self.assertIs(updated["last_enabled"], True)
        self.assertNotIn("alert", updated)

    def test_repeated_true_does_not_create_duplicate_event(self):
        state = {"last_enabled": True, "checks": 42, "hits": 1}

        updated = apply_check_result(state, enabled=True, store=self.store)

        self.assertEqual(self.store.event_count(), 0)
        self.assertEqual(updated["checks"], 43)
        self.assertEqual(updated["hits"], 1)
        self.assertIs(updated["last_enabled"], True)

    def test_retry_of_the_same_transition_reuses_the_durable_event(self):
        saved_state = {"last_enabled": False, "checks": 41, "hits": 3}

        apply_check_result(saved_state.copy(), enabled=True, store=self.store)
        apply_check_result(saved_state.copy(), enabled=True, store=self.store)

        self.assertEqual(self.store.event_count(), 1)

    def test_later_transition_check_creates_a_distinct_event(self):
        apply_check_result(
            {"last_enabled": False, "checks": 41, "hits": 0},
            enabled=True,
            store=self.store,
        )

        apply_check_result(
            {"last_enabled": False, "checks": 42, "hits": 1},
            enabled=True,
            store=self.store,
        )

        self.assertEqual(self.store.event_count(), 2)

    def test_event_failure_does_not_mark_transition_complete(self):
        state = {"last_enabled": False, "checks": 41, "hits": 3}

        with self.assertRaisesRegex(RuntimeError, "database unavailable"):
            apply_check_result(
                state,
                enabled=True,
                store=_FailingStore(),
            )

        self.assertIs(state["last_enabled"], False)
        self.assertEqual(state["hits"], 3)


class _FailingStore:
    def create_event(self, *args, **kwargs):
        raise RuntimeError("database unavailable")


if __name__ == "__main__":
    unittest.main()
