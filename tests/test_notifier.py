import json
import logging
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock, patch

import httpx

from config import RuntimeConfig
from notification_store import NotificationStore, sanitize_error
from notifier import (
    DeliveryResult,
    DiscordAdapter,
    HealthReporter,
    Notifier,
    TelegramAdapter,
    classify_http_failure,
    configure_logging,
    initialize_channels,
    run_cycle,
)


NOW = datetime(2030, 8, 27, 12, 0, tzinfo=timezone.utc)
TOKEN = "123456789:abcdefghijklmnopqrstuvwxyzABCDE"
WEBHOOK_LIKE_URL = "https://discord.com/api/webhooks/123456/very-secret"


def runtime_config() -> RuntimeConfig:
    return RuntimeConfig(
        telegram_bot_token=TOKEN,
        telegram_chat_id=404346140,
        telegram_authorized_user_id=404346140,
        discord_bot_token="discord-secret-token",
        discord_application_id=1542514080810664018,
        discord_guild_id=1476852384826392628,
        discord_channel_id=1542511880659017792,
        discord_authorized_user_id=1138419941926776893,
    )


class SuccessfulAdapter:
    async def send(self, claim):
        return DeliveryResult("delivered")


class FailingAdapter:
    def __init__(self, error: str, status: str = "retry"):
        self._result = DeliveryResult(status, error)

    async def send(self, claim):
        return self._result


class NotifierWorkerTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.store = NotificationStore(
            Path(self.temporary_directory.name) / "notifications.sqlite3"
        )

    def tearDown(self):
        self.temporary_directory.cleanup()

    async def test_one_channel_failure_does_not_block_other_channel(self):
        event_id = self.store.create_event(
            "parking_available", {"test": False}, 42, ("telegram", "discord")
        )
        worker = Notifier(
            self.store,
            {
                "telegram": FailingAdapter("timeout"),
                "discord": SuccessfulAdapter(),
            },
        )

        attempted = await worker.run_once(NOW)

        self.assertEqual(attempted, 2)
        self.assertEqual(self.store.delivery(event_id, "telegram").status, "retry")
        self.assertEqual(
            self.store.delivery(event_id, "discord").status, "delivered"
        )

    async def test_worker_attempts_at_most_one_due_delivery_per_channel(self):
        first_event = self.store.create_event(
            "parking_available", {"test": False}, 1, ("telegram", "discord")
        )
        second_event = self.store.create_event(
            "parking_available", {"test": False}, 2, ("telegram", "discord")
        )
        worker = Notifier(
            self.store,
            {"telegram": SuccessfulAdapter(), "discord": SuccessfulAdapter()},
        )

        attempted = await worker.run_once(NOW)

        self.assertEqual(attempted, 2)
        for channel in ("telegram", "discord"):
            self.assertEqual(
                self.store.delivery(first_event, channel).status, "delivered"
            )
            self.assertEqual(
                self.store.delivery(second_event, channel).status, "pending"
            )

    async def test_unexpected_adapter_error_retries_only_that_channel(self):
        class RaisingAdapter:
            async def send(self, claim):
                raise RuntimeError("connection reset")

        event_id = self.store.create_event(
            "parking_available", {}, 3, ("telegram", "discord")
        )
        worker = Notifier(
            self.store,
            {"telegram": RaisingAdapter(), "discord": SuccessfulAdapter()},
        )

        await worker.run_once(NOW)

        telegram = self.store.delivery(event_id, "telegram")
        self.assertEqual(telegram.status, "retry")
        self.assertEqual(telegram.last_error, "connection reset")
        self.assertEqual(self.store.delivery(event_id, "discord").status, "delivered")

    async def test_unexpected_token_bearing_adapter_error_is_sanitized(self):
        secret = "123456789:unexpectedAdapterExceptionSecretABCDE"
        store = NotificationStore(
            Path(self.temporary_directory.name) / "secret-aware.sqlite3",
            secrets=(secret,),
        )

        class RaisingAdapter:
            async def send(self, claim):
                raise RuntimeError(f"adapter failed with {secret}")

        event_id = store.create_event("parking_available", {}, 4, ("telegram",))
        worker = Notifier(store, {"telegram": RaisingAdapter()})

        with self.assertLogs("parking_notifier", level="INFO") as captured:
            await worker.run_once(NOW)

        record = store.delivery(event_id, "telegram")
        self.assertEqual(record.status, "retry")
        self.assertNotIn(secret, record.last_error)
        self.assertNotIn(secret, "\n".join(captured.output))

    async def test_retry_backoff_starts_after_network_completion(self):
        completed_at = NOW + timedelta(seconds=45)
        event_id = self.store.create_event(
            "parking_available", {}, 5, ("telegram",)
        )
        worker = Notifier(
            self.store,
            {"telegram": FailingAdapter("timeout")},
            now_provider=lambda: completed_at,
        )

        await worker.run_once(NOW)

        record = self.store.delivery(event_id, "telegram")
        self.assertEqual(
            datetime.fromisoformat(record.next_attempt_at),
            completed_at + timedelta(seconds=30),
        )

    async def test_idle_cycle_sleeps_five_seconds_and_emits_initial_health(self):
        worker = Notifier(self.store, {})
        reporter = HealthReporter()
        sleep = AsyncMock()

        with self.assertLogs("parking_notifier", level="INFO") as captured:
            attempted = await run_cycle(
                worker,
                self.store,
                reporter,
                now=NOW,
                sleep=sleep,
            )

        self.assertEqual(attempted, 0)
        sleep.assert_awaited_once_with(5)
        output = "\n".join(captured.output)
        self.assertIn("health summary", output)
        self.assertIn("channel=telegram", output)

    def test_health_summary_is_repeated_after_five_minutes_only(self):
        reporter = HealthReporter()

        with self.assertLogs("parking_notifier", level="INFO") as first:
            reporter.report_if_due(self.store, NOW)
        with self.assertNoLogs("parking_notifier", level="INFO"):
            reporter.report_if_due(self.store, NOW + timedelta(seconds=299))
        with self.assertLogs("parking_notifier", level="INFO") as second:
            reporter.report_if_due(self.store, NOW + timedelta(seconds=300))

        self.assertIn("health summary", "\n".join(first.output))
        self.assertIn("health summary", "\n".join(second.output))


class NotifierStartupTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.store = NotificationStore(
            Path(self.temporary_directory.name) / "notifications.sqlite3"
        )

    def tearDown(self):
        self.temporary_directory.cleanup()

    def test_configured_channel_requeues_failed_while_missing_sibling_is_disabled(self):
        event_id = self.store.create_event(
            "parking_available", {}, 6, ("telegram", "discord")
        )
        self.store.mark_failed(
            self.store.claim_due("telegram", NOW), "unauthorized", NOW
        )
        self.store.mark_failed(
            self.store.claim_due("discord", NOW), "forbidden", NOW
        )

        initialize_channels(self.store, {"telegram"}, NOW + timedelta(days=1))

        health = self.store.health_summary()
        self.assertEqual(health["telegram"].state, "retrying")
        self.assertEqual(health["discord"].state, "disabled")
        self.assertEqual(self.store.delivery(event_id, "telegram").status, "retry")
        self.assertEqual(self.store.delivery(event_id, "discord").status, "failed")


class HttpBoundaryTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.store = NotificationStore(
            Path(self.temporary_directory.name) / "notifications.sqlite3"
        )

    def tearDown(self):
        self.temporary_directory.cleanup()

    async def test_telegram_uses_stable_status_and_stats_callbacks(self):
        requests = []

        async def handler(request: httpx.Request) -> httpx.Response:
            requests.append(request)
            return httpx.Response(200, json={"ok": True})

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            self.store.create_event("parking_available", {}, 7, ("telegram",))
            claim = self.store.claim_due("telegram", NOW)
            result = await TelegramAdapter(runtime_config(), client).send(claim)

        self.assertEqual(result, DeliveryResult("delivered"))
        self.assertEqual(len(requests), 1)
        self.assertTrue(requests[0].url.path.endswith(f"/bot{TOKEN}/sendMessage"))
        payload = json.loads(requests[0].content)
        self.assertEqual(payload["chat_id"], 404346140)
        callbacks = [
            button["callback_data"]
            for row in payload["reply_markup"]["inline_keyboard"]
            for button in row
        ]
        self.assertEqual(callbacks, ["status", "stats"])

    async def test_discord_uses_bot_auth_and_stable_component_ids(self):
        requests = []

        async def handler(request: httpx.Request) -> httpx.Response:
            requests.append(request)
            return httpx.Response(201, json={"id": "message-id"})

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            self.store.create_event("parking_available", {}, 8, ("discord",))
            claim = self.store.claim_due("discord", NOW)
            result = await DiscordAdapter(runtime_config(), client).send(claim)

        self.assertEqual(result, DeliveryResult("delivered"))
        request = requests[0]
        self.assertEqual(request.url.path, "/api/v10/channels/1542511880659017792/messages")
        self.assertEqual(request.headers["Authorization"], "Bot discord-secret-token")
        payload = json.loads(request.content)
        custom_ids = [
            component["custom_id"]
            for row in payload["components"]
            for component in row["components"]
        ]
        self.assertEqual(custom_ids, ["parking:status", "parking:stats"])
        self.assertEqual(len(payload["embeds"]), 1)

    async def test_network_errors_are_retryable_without_exposing_request_url(self):
        async def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("connect failed", request=request)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            self.store.create_event("parking_available", {}, 9, ("telegram",))
            claim = self.store.claim_due("telegram", NOW)
            result = await TelegramAdapter(runtime_config(), client).send(claim)

        self.assertEqual(result.status, "retry")
        self.assertNotIn(TOKEN, result.error)
        self.assertNotIn("https://", result.error)

    async def test_read_timeout_is_retryable(self):
        async def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ReadTimeout("read timed out", request=request)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            self.store.create_event("parking_available", {}, 10, ("telegram",))
            claim = self.store.claim_due("telegram", NOW)
            result = await TelegramAdapter(runtime_config(), client).send(claim)

        self.assertEqual(result.status, "retry")


class FailurePolicyTests(unittest.TestCase):
    def test_logging_suppresses_http_client_request_urls(self):
        httpx_logger = logging.getLogger("httpx")
        previous_level = httpx_logger.level
        self.addCleanup(httpx_logger.setLevel, previous_level)
        httpx_logger.setLevel(logging.NOTSET)

        with patch("notifier.logging.basicConfig"):
            configure_logging()

        self.assertEqual(httpx_logger.level, logging.WARNING)

    def test_sanitize_error_removes_tokens_urls_and_authorization_headers(self):
        raw = f"Authorization: Bot {TOKEN} POST {WEBHOOK_LIKE_URL} failed"

        cleaned = sanitize_error(raw, secrets=(TOKEN,))

        self.assertNotIn(TOKEN, cleaned)
        self.assertNotIn("/api/webhooks/", cleaned)
        self.assertNotIn("Authorization", cleaned)

    def test_authentication_error_is_permanent(self):
        self.assertEqual(classify_http_failure(401), "failed")
        self.assertEqual(classify_http_failure(429), "retry")

    def test_other_client_errors_are_permanent(self):
        self.assertEqual(classify_http_failure(400), "failed")
        self.assertEqual(classify_http_failure(404), "failed")
        self.assertEqual(classify_http_failure(408), "retry")
        self.assertEqual(classify_http_failure(503), "retry")


if __name__ == "__main__":
    unittest.main()
