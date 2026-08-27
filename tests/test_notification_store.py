import json
import os
import secrets
import sqlite3
import stat
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

from notification_store import CLAIM_TIMEOUT, NotificationStore


NOW = datetime(2030, 1, 2, 3, 4, 5, tzinfo=timezone.utc)


class NotificationStoreTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temporary_directory.name) / "notifications.sqlite3"
        self.store = NotificationStore(self.db_path)

    def tearDown(self):
        self.temporary_directory.cleanup()

    @unittest.skipIf(os.name == "nt", "POSIX file modes are required")
    def test_database_is_created_with_shared_runtime_mode(self):
        shared_path = Path(self.temporary_directory.name) / "shared.sqlite3"
        shared_path.touch(mode=0o640)
        os.chmod(shared_path, 0o640)
        NotificationStore(shared_path)

        self.assertEqual(stat.S_IMODE(shared_path.stat().st_mode), 0o660)

    def create_dual_channel_event(self, event_key=None):
        return self.store.create_event(
            "parking_available",
            {"available": True},
            42,
            ("telegram", "discord"),
            event_key=event_key,
        )

    def test_event_creates_one_delivery_per_channel(self):
        event_id = self.create_dual_channel_event()

        rows = self.store.deliveries_for(event_id)

        self.assertEqual(
            [(row.channel, row.status) for row in rows],
            [("discord", "pending"), ("telegram", "pending")],
        )

    def test_event_key_makes_creation_idempotent(self):
        first_id = self.create_dual_channel_event(event_key="check:42")
        second_id = self.create_dual_channel_event(event_key="check:42")

        self.assertEqual(second_id, first_id)
        self.assertEqual(self.store.event_count(), 1)
        self.assertEqual(len(self.store.deliveries_for(first_id)), 2)

    def test_events_without_a_key_are_distinct(self):
        first_id = self.create_dual_channel_event()
        second_id = self.create_dual_channel_event()

        self.assertNotEqual(second_id, first_id)
        self.assertEqual(self.store.event_count(), 2)

    def test_invalid_channel_rolls_back_the_event(self):
        with self.assertRaisesRegex(ValueError, "Unsupported channel"):
            self.store.create_event(
                "parking_available", {}, 42, ("telegram", "email")
            )

        self.assertEqual(self.store.event_count(), 0)

    def test_claim_contains_event_payload_and_attempt_number(self):
        event_id = self.create_dual_channel_event()

        claim = self.store.claim_due("telegram", NOW)

        self.assertEqual(claim.event_id, event_id)
        self.assertEqual(claim.channel, "telegram")
        self.assertEqual(claim.event_type, "parking_available")
        self.assertEqual(claim.payload, {"available": True})
        self.assertEqual(claim.source_check, 42)
        self.assertEqual(claim.attempt_count, 1)

    def test_claim_is_exclusive_across_store_instances(self):
        self.create_dual_channel_event()
        competing_store = NotificationStore(self.db_path)

        first = self.store.claim_due("telegram", NOW)
        second = competing_store.claim_due("telegram", NOW)

        self.assertIsNotNone(first)
        self.assertIsNone(second)

    def test_synchronized_claim_contention_has_exactly_one_winner(self):
        event_id = self.create_dual_channel_event()
        barrier = threading.Barrier(8)

        def contend():
            store = NotificationStore(self.db_path)
            barrier.wait(timeout=5)
            return store.claim_due("telegram", NOW)

        with ThreadPoolExecutor(max_workers=8) as executor:
            claims = list(executor.map(lambda _: contend(), range(8)))

        winners = [claim for claim in claims if claim is not None]
        self.assertEqual(len(winners), 1)
        self.assertEqual(winners[0].event_id, event_id)

    def test_successful_channel_is_not_claimed_again_when_other_channel_retries(self):
        self.create_dual_channel_event()
        telegram = self.store.claim_due("telegram", NOW)
        self.store.mark_delivered(telegram, NOW)
        discord = self.store.claim_due("discord", NOW)
        self.store.mark_retry(discord, "timeout", NOW)

        self.assertIsNone(
            self.store.claim_due("telegram", NOW + timedelta(days=1))
        )

    def test_retry_delays_are_30_120_600_1800_then_3600_seconds(self):
        self.create_dual_channel_event()
        observed = []
        attempt_time = NOW

        for _ in range(6):
            claim = self.store.claim_due("discord", attempt_time)
            self.store.mark_retry(claim, "temporary outage", attempt_time)
            delivery = self.store.delivery(claim.event_id, "discord")
            next_attempt = datetime.fromisoformat(delivery.next_attempt_at)
            observed.append(int((next_attempt - attempt_time).total_seconds()))
            attempt_time = next_attempt

        self.assertEqual(observed, [30, 120, 600, 1800, 3600, 3600])

    def test_retry_is_not_due_before_its_scheduled_time(self):
        self.create_dual_channel_event()
        claim = self.store.claim_due("discord", NOW)
        self.store.mark_retry(claim, "timeout", NOW)

        self.assertIsNone(
            self.store.claim_due("discord", NOW + timedelta(seconds=29))
        )
        self.assertIsNotNone(
            self.store.claim_due("discord", NOW + timedelta(seconds=30))
        )

    def test_restart_releases_expired_claim_without_duplicate_delivery(self):
        self.create_dual_channel_event()
        first_claim = self.store.claim_due("discord", NOW)
        reopened = NotificationStore(self.db_path)

        recovered_claim = reopened.claim_due("discord", NOW + CLAIM_TIMEOUT)

        self.assertIsNotNone(recovered_claim)
        self.assertEqual(recovered_claim.event_id, first_claim.event_id)
        self.assertEqual(recovered_claim.attempt_count, 2)

    def test_delivered_row_never_becomes_eligible_after_claim_timeout(self):
        self.create_dual_channel_event()
        claim = self.store.claim_due("discord", NOW)
        self.store.mark_delivered(claim, NOW)

        self.assertIsNone(
            self.store.claim_due("discord", NOW + CLAIM_TIMEOUT + timedelta(days=1))
        )

    def test_stale_claim_cannot_overwrite_a_recovered_claim(self):
        self.create_dual_channel_event()
        stale_claim = self.store.claim_due("discord", NOW)
        recovered_claim = self.store.claim_due("discord", NOW + CLAIM_TIMEOUT)

        with self.assertRaisesRegex(ValueError, "no longer active"):
            self.store.mark_delivered(stale_claim, NOW + CLAIM_TIMEOUT)

        self.store.mark_delivered(recovered_claim, NOW + CLAIM_TIMEOUT)
        self.assertEqual(
            self.store.delivery(recovered_claim.event_id, "discord").status,
            "delivered",
        )

    def test_failed_delivery_is_terminal(self):
        self.create_dual_channel_event()
        claim = self.store.claim_due("discord", NOW)
        self.store.mark_failed(claim, "forbidden", NOW)

        self.assertIsNone(
            self.store.claim_due("discord", NOW + timedelta(days=30))
        )

    def test_requeue_failed_recovers_only_selected_channel_immediately(self):
        event_id = self.create_dual_channel_event()
        self.store.mark_failed(
            self.store.claim_due("telegram", NOW), "unauthorized", NOW
        )
        self.store.mark_failed(
            self.store.claim_due("discord", NOW), "forbidden", NOW
        )

        requeued = self.store.requeue_failed(
            "telegram", NOW + timedelta(days=1)
        )

        self.assertEqual(requeued, 1)
        self.assertIsNotNone(
            self.store.claim_due("telegram", NOW + timedelta(days=1))
        )
        self.assertIsNone(
            self.store.claim_due("discord", NOW + timedelta(days=1))
        )
        self.assertEqual(self.store.delivery(event_id, "discord").status, "failed")

    def test_disabled_channel_health_is_persisted_across_store_reopen(self):
        self.store.set_channel_enabled("telegram", False, NOW)

        reopened = NotificationStore(self.db_path)
        health = reopened.health_summary()

        self.assertEqual(health["telegram"].state, "disabled")
        self.assertEqual(health["discord"].state, "healthy")

    def test_disabled_channel_cannot_be_claimed_until_reenabled(self):
        event_id = self.store.create_event(
            "parking_available",
            {"available": True},
            1,
            ("telegram",),
        )
        due_at = NOW + timedelta(days=1)

        self.store.set_channel_enabled("telegram", False, NOW)
        self.assertIsNone(self.store.claim_due("telegram", due_at))
        self.assertEqual(self.store.delivery(event_id, "telegram").status, "pending")

        self.store.set_channel_enabled("telegram", True, due_at)
        claim = self.store.claim_due("telegram", due_at)
        self.assertIsNotNone(claim)
        self.assertEqual(claim.event_id, event_id)

    def test_store_sanitizes_and_bounds_errors(self):
        self.create_dual_channel_event()
        claim = self.store.claim_due("discord", NOW)
        bot_token = self.synthetic_telegram_token()
        webhook_secret = secrets.token_urlsafe(24)
        webhook = f"https://discord.com/api/webhooks/1234/{webhook_secret}"
        raw_error = (
            f"request failed Authorization: Bot {bot_token}\n"
            f"POST {webhook} failed " + "x" * 800
        )

        self.store.mark_retry(claim, raw_error, NOW)
        stored_error = self.store.delivery(claim.event_id, "discord").last_error

        self.assertFalse(
            bot_token in stored_error,
            "A token-shaped value was persisted",
        )
        self.assertFalse(
            webhook_secret in stored_error,
            "A webhook credential was persisted",
        )
        self.assertNotIn("Authorization", stored_error)
        self.assertLessEqual(len(stored_error), 500)

    def test_retry_redacts_a_supplied_discord_token_embedded_alone(self):
        runtime_secret = self.synthetic_discord_token()
        secret_aware_store = NotificationStore(
            self.db_path, secrets=(runtime_secret,)
        )
        self.create_dual_channel_event()
        claim = secret_aware_store.claim_due("discord", NOW)

        secret_aware_store.mark_retry(
            claim, f"Discord rejected bot credential {runtime_secret}", NOW
        )
        stored_error = secret_aware_store.delivery(
            claim.event_id, "discord"
        ).last_error

        self.assertFalse(
            runtime_secret in stored_error,
            "A supplied runtime secret was persisted by mark_retry",
        )
        self.assertIn("[redacted-secret]", stored_error)
        self.assertLessEqual(len(stored_error), 500)

    def test_failed_redacts_a_supplied_secret_in_a_mapping_authorization_header(self):
        runtime_secret = self.synthetic_discord_token()
        secret_aware_store = NotificationStore(
            self.db_path, secrets=(runtime_secret,)
        )
        self.create_dual_channel_event()
        claim = secret_aware_store.claim_due("discord", NOW)
        error = repr(
            {
                "Authorization": f"Bot {runtime_secret}",
                "detail": "forbidden",
            }
        )

        secret_aware_store.mark_failed(claim, error, NOW)
        stored_error = secret_aware_store.delivery(
            claim.event_id, "discord"
        ).last_error

        self.assertFalse(
            runtime_secret in stored_error,
            "A supplied runtime secret was persisted by mark_failed",
        )
        self.assertNotIn("Authorization", stored_error)
        self.assertLessEqual(len(stored_error), 500)

    def test_health_summary_reports_each_channel_independently(self):
        first_event = self.create_dual_channel_event()
        second_event = self.store.create_event(
            "parking_available", {}, 43, ("telegram", "discord")
        )
        self.store.mark_delivered(self.store.claim_due("telegram", NOW), NOW)
        self.store.mark_retry(
            self.store.claim_due("discord", NOW), "timeout", NOW
        )
        self.store.mark_failed(
            self.store.claim_due("telegram", NOW), "forbidden", NOW
        )

        health = self.store.health_summary()

        self.assertEqual(
            asdict(health["telegram"]),
            {
                "state": "failed",
                "last_delivered_at": NOW.isoformat(),
                "delivered_count": 1,
                "pending_count": 0,
                "retrying_count": 0,
                "failed_count": 1,
            },
        )
        self.assertEqual(health["discord"].state, "retrying")
        self.assertEqual(health["discord"].delivered_count, 0)
        self.assertEqual(health["discord"].pending_count, 1)
        self.assertEqual(health["discord"].retrying_count, 1)
        self.assertEqual(health["discord"].failed_count, 0)
        self.assertEqual(
            {row.event_id for row in self.store.deliveries_for(first_event)},
            {first_event},
        )
        self.assertEqual(
            {row.event_id for row in self.store.deliveries_for(second_event)},
            {second_event},
        )

    def test_health_counts_an_active_claim_in_its_previous_state(self):
        self.create_dual_channel_event()
        first_claim = self.store.claim_due("discord", NOW)
        self.store.mark_retry(first_claim, "timeout", NOW)
        self.store.claim_due("discord", NOW + timedelta(seconds=30))

        health = self.store.health_summary()["discord"]

        self.assertEqual(health.pending_count, 0)
        self.assertEqual(health.retrying_count, 1)

    def test_legacy_alert_migration_is_idempotent_and_clears_only_the_flag(self):
        state_path = Path(self.temporary_directory.name) / "state.json"
        original_state = {
            "alert": True,
            "last_check": "2026-08-27T10:00:00+03:00",
            "checks": 42,
            "hits": 1,
            "interval": 1800,
        }
        state_path.write_text(json.dumps(original_state), encoding="utf-8")

        first_id = self.store.migrate_legacy_alert(state_path)
        migrated_state = json.loads(state_path.read_text(encoding="utf-8"))
        migrated_state["alert"] = True
        state_path.write_text(json.dumps(migrated_state), encoding="utf-8")
        second_id = self.store.migrate_legacy_alert(state_path)

        self.assertEqual(second_id, first_id)
        self.assertEqual(self.store.event_count(), 1)
        self.assertEqual(
            [row.channel for row in self.store.deliveries_for(first_id)],
            ["discord", "telegram"],
        )
        final_state = json.loads(state_path.read_text(encoding="utf-8"))
        self.assertIs(final_state["alert"], False)
        self.assertEqual(final_state["checks"], 42)
        self.assertEqual(final_state["interval"], 1800)

    def test_false_legacy_alert_does_not_create_an_event(self):
        state_path = Path(self.temporary_directory.name) / "state.json"
        state_path.write_text(json.dumps({"alert": False}), encoding="utf-8")

        event_id = self.store.migrate_legacy_alert(state_path)

        self.assertIsNone(event_id)
        self.assertEqual(self.store.event_count(), 0)

    def test_schema_uses_wal_foreign_keys_and_unique_event_keys(self):
        self.create_dual_channel_event(event_key="check:42")
        with closing(sqlite3.connect(self.db_path)) as connection:
            journal_mode = connection.execute("PRAGMA journal_mode").fetchone()[0]
            foreign_keys = connection.execute(
                "PRAGMA foreign_key_list(notification_deliveries)"
            ).fetchall()
            indexes = connection.execute(
                "PRAGMA index_list(notification_events)"
            ).fetchall()

        self.assertEqual(journal_mode, "wal")
        self.assertTrue(foreign_keys)
        self.assertTrue(any(index[2] for index in indexes))

    @staticmethod
    def synthetic_discord_token():
        return ".".join(
            (
                secrets.token_urlsafe(18),
                secrets.token_urlsafe(6),
                secrets.token_urlsafe(24),
            )
        )

    @staticmethod
    def synthetic_telegram_token():
        return f"{secrets.randbelow(900_000_000) + 100_000_000}:{secrets.token_urlsafe(24)}"


if __name__ == "__main__":
    unittest.main()
