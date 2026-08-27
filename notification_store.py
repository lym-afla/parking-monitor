"""Durable SQLite-backed notification event and delivery state."""

import json
import os
import re
import sqlite3
import tempfile
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping

from command_service import ChannelHealth


SUPPORTED_CHANNELS = ("telegram", "discord")
CLAIM_TIMEOUT = timedelta(minutes=5)
RETRY_DELAYS_SECONDS = (30, 120, 600, 1800, 3600)
MAX_STORED_ERROR_LENGTH = 500


@dataclass(frozen=True)
class DeliveryClaim:
    """One exclusively claimed delivery and the event data needed to send it."""

    event_id: int
    channel: str
    event_type: str
    payload: Mapping[str, Any]
    source_check: int
    attempt_count: int
    claim_token: str
    claim_expires_at: str


@dataclass(frozen=True)
class DeliveryRecord:
    """Persisted state for one event/channel delivery pair."""

    event_id: int
    channel: str
    status: str
    attempt_count: int
    next_attempt_at: str | None
    last_attempt_at: str | None
    delivered_at: str | None
    last_error: str | None
    claim_expires_at: str | None


class NotificationStore:
    """Own notification events and independent per-channel delivery lifecycle."""

    def __init__(
        self, db_path: str | Path, secrets: Iterable[str] = ()
    ):
        self._db_path = Path(db_path)
        self._secrets = tuple(secret for secret in secrets if secret)
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize_schema()

    def create_event(
        self,
        event_type: str,
        payload: Mapping[str, Any],
        source_check: int,
        channels: Iterable[str],
        event_key: str | None = None,
    ) -> int:
        """Create an event and its delivery rows, or return its idempotent ID."""
        normalized_channels = tuple(sorted(set(channels)))
        for channel in normalized_channels:
            self._validate_channel(channel)
        payload_json = json.dumps(
            payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True
        )
        created_at = _utc_iso(datetime.now(timezone.utc))

        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            if event_key is not None:
                existing = connection.execute(
                    "SELECT id FROM notification_events WHERE event_key = ?",
                    (event_key,),
                ).fetchone()
                if existing is not None:
                    connection.commit()
                    return int(existing["id"])

            cursor = connection.execute(
                """
                INSERT INTO notification_events (
                    event_key, event_type, created_at, payload_json, source_check
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (event_key, event_type, created_at, payload_json, source_check),
            )
            event_id = int(cursor.lastrowid)
            connection.executemany(
                """
                INSERT INTO notification_deliveries (
                    event_id, channel, status, attempt_count, next_attempt_at
                ) VALUES (?, ?, 'pending', 0, ?)
                """,
                [
                    (event_id, channel, created_at)
                    for channel in normalized_channels
                ],
            )
            connection.commit()
            return event_id
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()

    def claim_due(self, channel: str, now: datetime) -> DeliveryClaim | None:
        """Atomically claim the oldest due or expired delivery for a channel."""
        self._validate_channel(channel)
        now_iso = _utc_iso(now)
        expires_at = _utc_iso(now + CLAIM_TIMEOUT)
        claim_token = uuid.uuid4().hex

        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                """
                SELECT
                    d.event_id,
                    d.channel,
                    d.status,
                    d.claimed_from_status,
                    d.attempt_count,
                    e.event_type,
                    e.payload_json,
                    e.source_check
                FROM notification_deliveries AS d
                JOIN notification_events AS e ON e.id = d.event_id
                WHERE d.channel = ?
                  AND (
                    (
                      d.status IN ('pending', 'retry')
                      AND d.next_attempt_at <= ?
                    )
                    OR (
                      d.status = 'claimed'
                      AND d.claim_expires_at <= ?
                    )
                  )
                ORDER BY
                    COALESCE(d.next_attempt_at, d.claim_expires_at),
                    d.event_id
                LIMIT 1
                """,
                (channel, now_iso, now_iso),
            ).fetchone()
            if row is None:
                connection.commit()
                return None

            claimed_from_status = (
                row["claimed_from_status"]
                if row["status"] == "claimed"
                else row["status"]
            )
            attempt_count = int(row["attempt_count"]) + 1
            connection.execute(
                """
                UPDATE notification_deliveries
                SET status = 'claimed',
                    attempt_count = ?,
                    claim_token = ?,
                    claim_expires_at = ?,
                    claimed_from_status = ?
                WHERE event_id = ? AND channel = ?
                """,
                (
                    attempt_count,
                    claim_token,
                    expires_at,
                    claimed_from_status,
                    row["event_id"],
                    channel,
                ),
            )
            connection.commit()
            return DeliveryClaim(
                event_id=int(row["event_id"]),
                channel=channel,
                event_type=row["event_type"],
                payload=json.loads(row["payload_json"]),
                source_check=int(row["source_check"]),
                attempt_count=attempt_count,
                claim_token=claim_token,
                claim_expires_at=expires_at,
            )
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()

    def mark_delivered(self, claim: DeliveryClaim, now: datetime) -> None:
        """Complete an active claim successfully and make it terminal."""
        now_iso = _utc_iso(now)
        self._finish_claim(
            claim,
            """
            UPDATE notification_deliveries
            SET status = 'delivered',
                next_attempt_at = NULL,
                last_attempt_at = ?,
                delivered_at = ?,
                last_error = NULL,
                claim_token = NULL,
                claim_expires_at = NULL,
                claimed_from_status = NULL
            WHERE event_id = ? AND channel = ?
              AND status = 'claimed' AND claim_token = ?
            """,
            (now_iso, now_iso, claim.event_id, claim.channel, claim.claim_token),
        )

    def mark_retry(
        self, claim: DeliveryClaim, error: object, now: datetime
    ) -> None:
        """Return an active claim to retry with bounded exponential backoff."""
        now_iso = _utc_iso(now)
        delay_index = min(claim.attempt_count - 1, len(RETRY_DELAYS_SECONDS) - 1)
        next_attempt_at = _utc_iso(
            now + timedelta(seconds=RETRY_DELAYS_SECONDS[delay_index])
        )
        self._finish_claim(
            claim,
            """
            UPDATE notification_deliveries
            SET status = 'retry',
                next_attempt_at = ?,
                last_attempt_at = ?,
                delivered_at = NULL,
                last_error = ?,
                claim_token = NULL,
                claim_expires_at = NULL,
                claimed_from_status = NULL
            WHERE event_id = ? AND channel = ?
              AND status = 'claimed' AND claim_token = ?
            """,
            (
                next_attempt_at,
                now_iso,
                sanitize_error(error, secrets=self._secrets),
                claim.event_id,
                claim.channel,
                claim.claim_token,
            ),
        )

    def mark_failed(
        self, claim: DeliveryClaim, error: object, now: datetime
    ) -> None:
        """Complete an active claim with a terminal configuration failure."""
        now_iso = _utc_iso(now)
        self._finish_claim(
            claim,
            """
            UPDATE notification_deliveries
            SET status = 'failed',
                next_attempt_at = NULL,
                last_attempt_at = ?,
                delivered_at = NULL,
                last_error = ?,
                claim_token = NULL,
                claim_expires_at = NULL,
                claimed_from_status = NULL
            WHERE event_id = ? AND channel = ?
              AND status = 'claimed' AND claim_token = ?
            """,
            (
                now_iso,
                sanitize_error(error, secrets=self._secrets),
                claim.event_id,
                claim.channel,
                claim.claim_token,
            ),
        )

    def health_summary(self) -> dict[str, ChannelHealth]:
        """Return delivery counts and latest success for both known channels."""
        connection = self._connect()
        try:
            rows = connection.execute(
                """
                SELECT
                    channel,
                    MAX(delivered_at) AS last_delivered_at,
                    SUM(CASE
                        WHEN status = 'pending'
                          OR (status = 'claimed' AND claimed_from_status = 'pending')
                        THEN 1 ELSE 0 END) AS pending_count,
                    SUM(CASE
                        WHEN status = 'retry'
                          OR (status = 'claimed' AND claimed_from_status = 'retry')
                        THEN 1 ELSE 0 END) AS retrying_count,
                    SUM(CASE WHEN status = 'failed' THEN 1 ELSE 0 END)
                        AS failed_count
                FROM notification_deliveries
                GROUP BY channel
                """
            ).fetchall()
        finally:
            connection.close()

        by_channel = {row["channel"]: row for row in rows}
        return {
            channel: ChannelHealth(
                last_delivered_at=(
                    by_channel[channel]["last_delivered_at"]
                    if channel in by_channel
                    else None
                ),
                pending_count=(
                    int(by_channel[channel]["pending_count"])
                    if channel in by_channel
                    else 0
                ),
                retrying_count=(
                    int(by_channel[channel]["retrying_count"])
                    if channel in by_channel
                    else 0
                ),
                failed_count=(
                    int(by_channel[channel]["failed_count"])
                    if channel in by_channel
                    else 0
                ),
            )
            for channel in SUPPORTED_CHANNELS
        }

    def migrate_legacy_alert(self, state_path: str | Path) -> int | None:
        """Move a legacy Boolean alert into one idempotent durable event."""
        state_path = Path(state_path)
        try:
            with state_path.open("r", encoding="utf-8") as state_file:
                state = json.load(state_file)
        except FileNotFoundError:
            return None
        if not isinstance(state, dict):
            raise ValueError("State file must contain a JSON object")
        if state.get("alert") is not True:
            return None

        last_check = state.get("last_check")
        event_id = self.create_event(
            "parking_available",
            {"legacy": True, "last_check": last_check},
            int(state.get("checks", 0)),
            SUPPORTED_CHANNELS,
            event_key=f"legacy-alert:{last_check}",
        )
        state["alert"] = False
        _atomic_write_json(state_path, state)
        return event_id

    def deliveries_for(self, event_id: int) -> list[DeliveryRecord]:
        """Return delivery records for tests, operations, and diagnostics."""
        connection = self._connect()
        try:
            rows = connection.execute(
                """
                SELECT event_id, channel, status, attempt_count, next_attempt_at,
                       last_attempt_at, delivered_at, last_error, claim_expires_at
                FROM notification_deliveries
                WHERE event_id = ?
                ORDER BY channel
                """,
                (event_id,),
            ).fetchall()
            return [_delivery_record(row) for row in rows]
        finally:
            connection.close()

    def delivery(self, event_id: int, channel: str) -> DeliveryRecord:
        """Return one delivery record."""
        self._validate_channel(channel)
        connection = self._connect()
        try:
            row = connection.execute(
                """
                SELECT event_id, channel, status, attempt_count, next_attempt_at,
                       last_attempt_at, delivered_at, last_error, claim_expires_at
                FROM notification_deliveries
                WHERE event_id = ? AND channel = ?
                """,
                (event_id, channel),
            ).fetchone()
        finally:
            connection.close()
        if row is None:
            raise KeyError((event_id, channel))
        return _delivery_record(row)

    def event_count(self) -> int:
        """Return the persisted event count."""
        connection = self._connect()
        try:
            return int(
                connection.execute(
                    "SELECT COUNT(*) FROM notification_events"
                ).fetchone()[0]
            )
        finally:
            connection.close()

    def _finish_claim(
        self, claim: DeliveryClaim, statement: str, parameters: tuple[Any, ...]
    ) -> None:
        self._validate_channel(claim.channel)
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            cursor = connection.execute(statement, parameters)
            if cursor.rowcount != 1:
                connection.rollback()
                raise ValueError("Delivery claim is no longer active")
            connection.commit()
        except BaseException:
            if connection.in_transaction:
                connection.rollback()
            raise
        finally:
            connection.close()

    def _initialize_schema(self) -> None:
        connection = self._connect()
        try:
            connection.execute("PRAGMA journal_mode = WAL")
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS notification_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    event_key TEXT UNIQUE,
                    event_type TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    source_check INTEGER NOT NULL
                );

                CREATE TABLE IF NOT EXISTS notification_deliveries (
                    event_id INTEGER NOT NULL,
                    channel TEXT NOT NULL CHECK (channel IN ('telegram', 'discord')),
                    status TEXT NOT NULL CHECK (
                        status IN ('pending', 'retry', 'claimed', 'delivered', 'failed')
                    ),
                    attempt_count INTEGER NOT NULL DEFAULT 0,
                    next_attempt_at TEXT,
                    last_attempt_at TEXT,
                    delivered_at TEXT,
                    last_error TEXT,
                    claim_token TEXT,
                    claim_expires_at TEXT,
                    claimed_from_status TEXT CHECK (
                        claimed_from_status IS NULL
                        OR claimed_from_status IN ('pending', 'retry')
                    ),
                    PRIMARY KEY (event_id, channel),
                    FOREIGN KEY (event_id) REFERENCES notification_events(id)
                        ON DELETE CASCADE
                );

                CREATE INDEX IF NOT EXISTS notification_deliveries_due
                    ON notification_deliveries (
                        channel, status, next_attempt_at, claim_expires_at, event_id
                    );
                """
            )
        finally:
            connection.close()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self._db_path, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 5000")
        return connection

    @staticmethod
    def _validate_channel(channel: str) -> None:
        if channel not in SUPPORTED_CHANNELS:
            raise ValueError(f"Unsupported channel: {channel!r}")


def _delivery_record(row: sqlite3.Row) -> DeliveryRecord:
    return DeliveryRecord(
        event_id=int(row["event_id"]),
        channel=row["channel"],
        status=row["status"],
        attempt_count=int(row["attempt_count"]),
        next_attempt_at=row["next_attempt_at"],
        last_attempt_at=row["last_attempt_at"],
        delivered_at=row["delivered_at"],
        last_error=row["last_error"],
        claim_expires_at=row["claim_expires_at"],
    )


def _utc_iso(value: datetime) -> str:
    if not isinstance(value, datetime):
        raise TypeError("Timestamp must be a datetime")
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("Timestamp must be timezone-aware")
    return value.astimezone(timezone.utc).isoformat()


def sanitize_error(error: object, secrets: Iterable[str] = ()) -> str:
    text = str(error)
    for secret in sorted(set(secrets), key=len, reverse=True):
        if secret:
            text = text.replace(secret, "[redacted-secret]")
    text = re.sub(
        r"(?i)['\"]authorization['\"]\s*:\s*(['\"])[^'\"]*\1\s*,?\s*",
        "[redacted] ",
        text,
    )
    text = re.sub(
        r"(?i)\bauthorization\s*:[^\r\n]*(?:\r?\n)?", "[redacted] ", text
    )
    text = re.sub(
        r"\b\d{6,12}:[A-Za-z0-9_-]{20,}\b", "[redacted-token]", text
    )
    text = re.sub(r"https?://[^\s]+", "[redacted-url]", text, flags=re.IGNORECASE)
    text = " ".join(text.split())
    return text[:MAX_STORED_ERROR_LENGTH]


def _atomic_write_json(path: Path, state: Mapping[str, Any]) -> None:
    file_descriptor, temporary_path = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    try:
        with os.fdopen(file_descriptor, "w", encoding="utf-8") as state_file:
            json.dump(state, state_file, ensure_ascii=False)
            state_file.flush()
            os.fsync(state_file.fileno())
        os.replace(temporary_path, path)
    except BaseException:
        try:
            os.unlink(temporary_path)
        except FileNotFoundError:
            pass
        raise
