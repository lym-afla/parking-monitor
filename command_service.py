"""Platform-neutral read and update operations for parking commands."""

from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Callable, Mapping

from config import CHECK_INTERVAL_SECONDS
from monitor import get_polling_schedule
from state_store import mutate_json_state, read_json_state


MINIMUM_INTERVAL_SECONDS = 60
MAXIMUM_INTERVAL_SECONDS = 86400


@dataclass(frozen=True)
class ChannelHealth:
    """Delivery health for one notification channel."""

    state: str = "healthy"
    last_delivered_at: str | None = None
    delivered_count: int = 0
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
    next_expected_check: str | None
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
        if type(seconds) is not int:
            raise ValueError("Interval must be an integer number of seconds")
        if not MINIMUM_INTERVAL_SECONDS <= seconds <= MAXIMUM_INTERVAL_SECONDS:
            raise ValueError(
                f"Interval must be between {MINIMUM_INTERVAL_SECONDS} and "
                f"{MAXIMUM_INTERVAL_SECONDS} seconds"
            )

        state, _ = mutate_json_state(
            self._state_path,
            lambda current: current.__setitem__("interval", seconds),
            defaults={"interval": CHECK_INTERVAL_SECONDS},
        )
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
            next_expected_check=self._next_expected_check(
                state.get("last_check"), normal_interval
            ),
            channel_health=self._channel_health(),
        )

    def _read_state(self) -> dict[str, object]:
        return read_json_state(
            self._state_path,
            defaults={"interval": CHECK_INTERVAL_SECONDS},
        )

    @staticmethod
    def _next_expected_check(
        last_check: object, normal_interval: int
    ) -> str | None:
        if not isinstance(last_check, str):
            return None
        try:
            last_check_at = datetime.fromisoformat(last_check)
        except ValueError:
            return None
        interval, _ = get_polling_schedule(
            last_check_at,
            normal_interval=normal_interval,
        )
        return (last_check_at + timedelta(seconds=interval)).isoformat()

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
            state=str(health.get("state", "healthy")),
            last_delivered_at=health.get("last_delivered_at"),
            delivered_count=int(health.get("delivered_count", 0)),
            pending_count=int(health.get("pending_count", 0)),
            retrying_count=int(health.get("retrying_count", 0)),
            failed_count=int(health.get("failed_count", 0)),
        )
