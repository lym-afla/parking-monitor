"""Platform-neutral read and update operations for parking commands."""

import json
import os
import tempfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable, Mapping

from config import CHECK_INTERVAL_SECONDS
from monitor import get_polling_schedule


MINIMUM_INTERVAL_SECONDS = 60
MAXIMUM_INTERVAL_SECONDS = 86400


@dataclass(frozen=True)
class ChannelHealth:
    """Delivery health for one notification channel."""

    last_delivered_at: str | None = None
    pending_count: int = 0
    retrying_count: int = 0
    failed_count: int = 0


@dataclass(frozen=True)
class StatusSnapshot:
    """State needed to render a current parking-monitor status response."""

    last_check: str | None
    parking_available: bool
    normal_interval_seconds: int
    effective_interval_seconds: int
    polling_mode: str
    channel_health: Mapping[str, ChannelHealth]


@dataclass(frozen=True)
class StatsSnapshot:
    """State needed to render monitoring and notification statistics."""

    checks: int
    hits: int
    success_rate_percent: float
    last_check: str | None
    channel_health: Mapping[str, ChannelHealth]


class CommandService:
    """Expose one command API shared by Telegram and Discord front ends."""

    def __init__(
        self,
        state_path: str | Path,
        health_provider: Callable[[], Mapping[str, ChannelHealth | Mapping[str, object]]],
    ):
        self._state_path = Path(state_path)
        self._health_provider = health_provider

    def status(self, now: datetime | None = None) -> StatusSnapshot:
        return self._status_from_state(self._read_state(), now)

    def stats(self) -> StatsSnapshot:
        state = self._read_state()
        checks = int(state.get("checks", 0))
        hits = int(state.get("hits", 0))
        return StatsSnapshot(
            checks=checks,
            hits=hits,
            success_rate_percent=(hits / checks * 100) if checks else 0.0,
            last_check=state.get("last_check"),
            channel_health=self._channel_health(),
        )

    def set_normal_interval(self, seconds: int) -> StatusSnapshot:
        if not MINIMUM_INTERVAL_SECONDS <= seconds <= MAXIMUM_INTERVAL_SECONDS:
            raise ValueError(
                f"Interval must be between {MINIMUM_INTERVAL_SECONDS} and "
                f"{MAXIMUM_INTERVAL_SECONDS} seconds"
            )

        state = self._read_state()
        state["interval"] = seconds
        self._atomic_write_state(state)
        return self._status_from_state(state, now=None)

    def _status_from_state(
        self, state: Mapping[str, object], now: datetime | None
    ) -> StatusSnapshot:
        normal_interval = int(state.get("interval", CHECK_INTERVAL_SECONDS))
        effective_interval, polling_mode = get_polling_schedule(
            now, normal_interval=normal_interval
        )
        return StatusSnapshot(
            last_check=state.get("last_check"),
            parking_available=bool(state.get("last_enabled", False)),
            normal_interval_seconds=normal_interval,
            effective_interval_seconds=effective_interval,
            polling_mode=polling_mode,
            channel_health=self._channel_health(),
        )

    def _read_state(self) -> dict[str, object]:
        try:
            with self._state_path.open("r", encoding="utf-8") as state_file:
                state = json.load(state_file)
        except FileNotFoundError:
            return {"interval": CHECK_INTERVAL_SECONDS}
        if not isinstance(state, dict):
            raise ValueError("State file must contain a JSON object")
        state.setdefault("interval", CHECK_INTERVAL_SECONDS)
        return state

    def _atomic_write_state(self, state: Mapping[str, object]) -> None:
        self._state_path.parent.mkdir(parents=True, exist_ok=True)
        file_descriptor, temporary_path = tempfile.mkstemp(
            prefix=f".{self._state_path.name}.",
            suffix=".tmp",
            dir=self._state_path.parent,
        )
        try:
            with os.fdopen(file_descriptor, "w", encoding="utf-8") as state_file:
                json.dump(state, state_file)
                state_file.flush()
                os.fsync(state_file.fileno())
            os.replace(temporary_path, self._state_path)
        except BaseException:
            try:
                os.unlink(temporary_path)
            except FileNotFoundError:
                pass
            raise

    def _channel_health(self) -> dict[str, ChannelHealth]:
        return {
            channel: self._coerce_channel_health(health)
            for channel, health in self._health_provider().items()
        }

    @staticmethod
    def _coerce_channel_health(
        health: ChannelHealth | Mapping[str, object],
    ) -> ChannelHealth:
        if isinstance(health, ChannelHealth):
            return health
        return ChannelHealth(
            last_delivered_at=health.get("last_delivered_at"),
            pending_count=int(health.get("pending_count", 0)),
            retrying_count=int(health.get("retrying_count", 0)),
            failed_count=int(health.get("failed_count", 0)),
        )
