"""Deliver durable parking notifications independently to each channel."""

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Mapping, Protocol

import httpx

from config import STATE_FILE, RuntimeConfig, load_config
from notification_store import (
    SUPPORTED_CHANNELS,
    DeliveryClaim,
    NotificationStore,
    sanitize_error,
)


LOGGER = logging.getLogger("parking_notifier")
DATABASE_PATH = Path(__file__).with_name("notifications.sqlite3")
IDLE_SLEEP_SECONDS = 5
HTTP_TIMEOUT = httpx.Timeout(connect=10.0, read=20.0, write=10.0, pool=5.0)


@dataclass(frozen=True)
class DeliveryResult:
    """A channel adapter outcome for the worker to persist."""

    status: str
    error: str | None = None


class DeliveryAdapter(Protocol):
    async def send(self, claim: DeliveryClaim) -> DeliveryResult:
        """Send one claimed notification without changing delivery state."""


def classify_http_failure(status_code: int) -> str:
    """Classify an unsuccessful HTTP status as terminal or retryable."""
    if status_code in (408, 429) or status_code >= 500:
        return "retry"
    return "failed"


def _response_error(response: httpx.Response, secrets: tuple[str, ...]) -> str:
    body = response.text.strip()
    detail = f"HTTP {response.status_code}"
    if body:
        detail = f"{detail}: {body}"
    return sanitize_error(detail, secrets=secrets)


def _notification_text(claim: DeliveryClaim) -> str:
    if claim.event_type == "test_notification" or claim.payload.get("test") is True:
        return (
            "🧪 TEST NOTIFICATION\n\n"
            "This is a parking monitor delivery test."
        )
    return (
        "🚨 PARKING AVAILABLE!\n\n"
        "A parking spot has become available!\n"
        "Use the buttons below to check the current status."
    )


class TelegramAdapter:
    """Send alerts through the Telegram Bot HTTP API."""

    def __init__(self, config: RuntimeConfig, client: httpx.AsyncClient):
        self._config = config
        self._client = client
        self._secrets = (config.telegram_bot_token, config.discord_bot_token)

    async def send(self, claim: DeliveryClaim) -> DeliveryResult:
        url = (
            "https://api.telegram.org/bot"
            f"{self._config.telegram_bot_token}/sendMessage"
        )
        payload = {
            "chat_id": self._config.telegram_chat_id,
            "text": _notification_text(claim),
            "reply_markup": {
                "inline_keyboard": [
                    [
                        {"text": "📊 Check Status", "callback_data": "status"},
                        {"text": "📈 View Stats", "callback_data": "stats"},
                    ]
                ]
            },
        }
        try:
            response = await self._client.post(url, json=payload)
        except httpx.HTTPError as exc:
            return DeliveryResult(
                "retry", sanitize_error(exc, secrets=self._secrets)
            )
        if 200 <= response.status_code < 300:
            return DeliveryResult("delivered")
        return DeliveryResult(
            classify_http_failure(response.status_code),
            _response_error(response, self._secrets),
        )


class DiscordAdapter:
    """Send alerts through the Discord bot-authenticated HTTP API."""

    def __init__(self, config: RuntimeConfig, client: httpx.AsyncClient):
        self._config = config
        self._client = client
        self._secrets = (config.telegram_bot_token, config.discord_bot_token)

    async def send(self, claim: DeliveryClaim) -> DeliveryResult:
        url = (
            "https://discord.com/api/v10/channels/"
            f"{self._config.discord_channel_id}/messages"
        )
        is_test = (
            claim.event_type == "test_notification"
            or claim.payload.get("test") is True
        )
        payload = {
            "embeds": [
                {
                    "title": (
                        "🧪 TEST NOTIFICATION"
                        if is_test
                        else "🚨 PARKING AVAILABLE!"
                    ),
                    "description": (
                        "This is a parking monitor delivery test."
                        if is_test
                        else "A parking spot has become available!"
                    ),
                    "color": 0xF1C40F if is_test else 0x2ECC71,
                }
            ],
            "components": [
                {
                    "type": 1,
                    "components": [
                        {
                            "type": 2,
                            "style": 2,
                            "label": "Status",
                            "custom_id": "parking:status",
                        },
                        {
                            "type": 2,
                            "style": 2,
                            "label": "Stats",
                            "custom_id": "parking:stats",
                        },
                    ],
                }
            ],
        }
        headers = {"Authorization": f"Bot {self._config.discord_bot_token}"}
        try:
            response = await self._client.post(url, headers=headers, json=payload)
        except httpx.HTTPError as exc:
            return DeliveryResult(
                "retry", sanitize_error(exc, secrets=self._secrets)
            )
        if 200 <= response.status_code < 300:
            return DeliveryResult("delivered")
        return DeliveryResult(
            classify_http_failure(response.status_code),
            _response_error(response, self._secrets),
        )


class Notifier:
    """Claim and record at most one due delivery for each channel per pass."""

    def __init__(
        self,
        store: NotificationStore,
        adapters: Mapping[str, DeliveryAdapter],
    ):
        self._store = store
        self._adapters = dict(adapters)

    async def run_once(self, now: datetime) -> int:
        attempted = 0
        for channel in SUPPORTED_CHANNELS:
            try:
                claim = self._store.claim_due(channel, now)
            except Exception as exc:
                LOGGER.error(
                    "delivery claim failed channel=%s error_class=%s",
                    channel,
                    type(exc).__name__,
                )
                continue
            if claim is None:
                continue

            attempted += 1
            try:
                result = await self._adapters[channel].send(claim)
            except Exception as exc:
                result = DeliveryResult("retry", str(exc))

            try:
                if result.status == "delivered":
                    self._store.mark_delivered(claim, now)
                elif result.status == "failed":
                    self._store.mark_failed(claim, result.error or "delivery failed", now)
                elif result.status == "retry":
                    self._store.mark_retry(claim, result.error or "delivery retry", now)
                else:
                    raise ValueError(f"Unsupported delivery result: {result.status!r}")
                record = self._store.delivery(claim.event_id, channel)
                LOGGER.info(
                    "delivery event_id=%s channel=%s attempt=%s result=%s next_retry=%s",
                    claim.event_id,
                    channel,
                    claim.attempt_count,
                    record.status,
                    record.next_attempt_at,
                )
            except Exception as exc:
                LOGGER.error(
                    "delivery result persistence failed event_id=%s channel=%s "
                    "attempt=%s error_class=%s",
                    claim.event_id,
                    channel,
                    claim.attempt_count,
                    type(exc).__name__,
                )
        return attempted


def _health_state(health) -> str:
    if health.failed_count:
        return "failed"
    if health.retrying_count:
        return "retrying"
    if health.pending_count:
        return "pending"
    return "healthy"


def _log_health_transitions(
    store: NotificationStore, previous: dict[str, str]
) -> dict[str, str]:
    current = {
        channel: _health_state(health)
        for channel, health in store.health_summary().items()
    }
    for channel, state in current.items():
        old_state = previous.get(channel)
        if state != old_state:
            transition = "recovered" if state == "healthy" and old_state else state
            LOGGER.info(
                "delivery health channel=%s state=%s previous=%s",
                channel,
                transition,
                old_state or "unknown",
            )
    return current


async def run_forever(config: RuntimeConfig) -> None:
    """Initialize migration and continuously drain due channel deliveries."""
    secrets = (config.telegram_bot_token, config.discord_bot_token)
    store = NotificationStore(DATABASE_PATH, secrets=secrets)
    migrated_event = store.migrate_legacy_alert(STATE_FILE)
    if migrated_event is not None:
        LOGGER.info("migrated legacy alert event_id=%s", migrated_event)

    previous_health: dict[str, str] = {}
    async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
        worker = Notifier(
            store,
            {
                "telegram": TelegramAdapter(config, client),
                "discord": DiscordAdapter(config, client),
            },
        )
        while True:
            attempted = await worker.run_once(datetime.now(timezone.utc))
            previous_health = _log_health_transitions(store, previous_health)
            if attempted == 0:
                await asyncio.sleep(IDLE_SLEEP_SECONDS)


def configure_logging() -> None:
    """Enable notifier logs without exposing token-bearing HTTP request URLs."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)


def main() -> None:
    configure_logging()
    asyncio.run(run_forever(load_config()))


if __name__ == "__main__":
    main()
