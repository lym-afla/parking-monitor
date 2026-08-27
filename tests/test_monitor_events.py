import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import monitor
import state_store
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

    def test_store_initialization_retries_before_scraping(self):
        saved_states = []
        site_checks = []
        sleeps = []
        factory_calls = []

        def load_saved_state():
            if saved_states:
                return saved_states[-1].copy()
            return {
                "last_enabled": False,
                "checks": 0,
                "hits": 0,
                "interval": 1800,
            }

        def save_in_memory(state):
            saved_states.append(state.copy())

        def record_error_in_memory(message):
            state = load_saved_state()
            state["error"] = message
            save_in_memory(state)
            return state

        def record_check_in_memory(enabled, store):
            state = load_saved_state()
            became_available = enabled and not state.get("last_enabled", False)
            apply_check_result(state, enabled, store)
            save_in_memory(state)
            return state, became_available

        def initialize_store(_database_path):
            factory_calls.append(True)
            if len(factory_calls) == 1:
                raise RuntimeError("TOKEN-SHOULD-NOT-BE-STORED")
            return self.store

        def check_once_ready():
            site_checks.append(True)
            return False

        def stop_after_second_cycle(interval):
            sleeps.append(interval)
            if len(sleeps) == 2:
                raise _MonitorStopped()

        real_polling_schedule = monitor.get_polling_schedule

        def fixed_polling_schedule(normal_interval):
            return real_polling_schedule(
                datetime(2026, 8, 20, tzinfo=timezone.utc),
                normal_interval=normal_interval,
            )

        with (
            patch.object(monitor, "load_state", side_effect=load_saved_state),
            patch.object(
                monitor,
                "record_error",
                side_effect=record_error_in_memory,
            ),
            patch.object(
                monitor,
                "record_check_result",
                side_effect=record_check_in_memory,
            ),
            patch.object(monitor, "check_site", side_effect=check_once_ready),
            patch.object(
                monitor,
                "get_polling_schedule",
                side_effect=fixed_polling_schedule,
            ),
            patch.object(monitor.time, "sleep", side_effect=stop_after_second_cycle),
            patch.object(monitor, "log"),
            patch("traceback.print_exc"),
        ):
            with self.assertRaises(_MonitorStopped):
                monitor.run_monitor(initialize_store)

        self.assertEqual(len(factory_calls), 2)
        self.assertEqual(len(site_checks), 1)
        self.assertEqual(saved_states[1]["checks"], 1)
        self.assertIsNone(saved_states[1]["error"])
        self.assertIn("initialization failed", saved_states[0]["error"])
        self.assertNotIn("TOKEN-SHOULD-NOT-BE-STORED", saved_states[0]["error"])
        self.assertEqual(sleeps, [1800, 1800])

    def test_atomic_save_preserves_existing_state_at_each_failure_point(self):
        original_state = {
            "last_enabled": False,
            "checks": 41,
            "hits": 3,
            "interval": 1800,
        }
        replacement_state = {**original_state, "checks": 42}
        original_json = json.dumps(original_state)

        for failure_point in ("staging", "write", "replace"):
            with self.subTest(failure_point=failure_point):
                case_directory = (
                    Path(self.temporary_directory.name) / failure_point
                )
                case_directory.mkdir()
                state_path = case_directory / "state.json"
                state_path.write_text(original_json, encoding="utf-8")

                if failure_point == "staging":
                    failure = patch.object(
                        state_store.tempfile,
                        "mkstemp",
                        side_effect=OSError("staging failed"),
                    )
                elif failure_point == "write":
                    failure = patch.object(
                        state_store.json,
                        "dump",
                        side_effect=OSError("write failed"),
                    )
                else:
                    failure = patch.object(
                        state_store.os,
                        "replace",
                        side_effect=OSError("replace failed"),
                    )

                with patch.object(monitor, "STATE_FILE", str(state_path)), failure:
                    with self.assertRaises(OSError):
                        monitor.save_state(replacement_state)

                self.assertEqual(
                    json.loads(state_path.read_text(encoding="utf-8")),
                    original_state,
                )
                self.assertEqual(
                    {path.name for path in case_directory.iterdir()},
                    {"state.json", "state.json.lock"},
                )

    def test_transition_retry_after_state_save_failure_reuses_event(self):
        state_path = Path(self.temporary_directory.name) / "state.json"
        original_state = {
            "last_enabled": False,
            "checks": 41,
            "hits": 3,
            "interval": 1800,
        }
        state_path.write_text(json.dumps(original_state), encoding="utf-8")

        with patch.object(monitor, "STATE_FILE", str(state_path)):
            with patch.object(
                state_store.os,
                "replace",
                side_effect=OSError("replace failed"),
            ):
                with self.assertRaises(OSError):
                    monitor.record_check_result(True, self.store)

            monitor.record_check_result(True, self.store)

        persisted = json.loads(state_path.read_text(encoding="utf-8"))
        self.assertEqual(self.store.event_count(), 1)
        self.assertEqual(persisted["checks"], 42)
        self.assertEqual(persisted["hits"], 4)
        self.assertIs(persisted["last_enabled"], True)


class _FailingStore:
    def create_event(self, *args, **kwargs):
        raise RuntimeError("database unavailable")


class _MonitorStopped(Exception):
    pass


if __name__ == "__main__":
    unittest.main()
