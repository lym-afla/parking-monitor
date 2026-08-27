import os
from dataclasses import dataclass
from typing import Mapping


URL = "https://parking.mos.ru/parking/barrier/subscribe/"

CHECK_INTERVAL_SECONDS = 1800  # 10 minutes

TARGET_REGION_TEXT = "Западный административный округ"
TARGET_ADDRESS_TEXT = "улица Поклонная, дом 11А"

STATE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "state.json")


@dataclass(frozen=True, repr=False)
class RuntimeConfig:
    telegram_bot_token: str
    telegram_chat_id: int
    telegram_authorized_user_id: int
    discord_bot_token: str
    discord_application_id: int
    discord_guild_id: int
    discord_channel_id: int
    discord_authorized_user_id: int

    def __repr__(self):
        return "RuntimeConfig(tokens=<redacted>, destinations=configured)"


_REQUIRED_RUNTIME_VARIABLES = (
    "TELEGRAM_BOT_TOKEN",
    "TELEGRAM_CHAT_ID",
    "TELEGRAM_AUTHORIZED_USER_ID",
    "DISCORD_BOT_TOKEN",
    "DISCORD_APPLICATION_ID",
    "DISCORD_GUILD_ID",
    "DISCORD_CHANNEL_ID",
    "DISCORD_AUTHORIZED_USER_ID",
)


def _required_integer(env: Mapping[str, str], name: str) -> int:
    try:
        return int(env[name])
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError(f"Invalid numeric environment variable: {name}") from exc


def load_config(env: Mapping[str, str] = os.environ) -> RuntimeConfig:
    """Load validated runtime notification settings from an environment mapping."""
    missing = [name for name in _REQUIRED_RUNTIME_VARIABLES if not env.get(name)]
    if missing:
        raise ValueError(f"Missing required environment variable: {missing[0]}")

    return RuntimeConfig(
        telegram_bot_token=env["TELEGRAM_BOT_TOKEN"],
        telegram_chat_id=_required_integer(env, "TELEGRAM_CHAT_ID"),
        telegram_authorized_user_id=_required_integer(env, "TELEGRAM_AUTHORIZED_USER_ID"),
        discord_bot_token=env["DISCORD_BOT_TOKEN"],
        discord_application_id=_required_integer(env, "DISCORD_APPLICATION_ID"),
        discord_guild_id=_required_integer(env, "DISCORD_GUILD_ID"),
        discord_channel_id=_required_integer(env, "DISCORD_CHANNEL_ID"),
        discord_authorized_user_id=_required_integer(env, "DISCORD_AUTHORIZED_USER_ID"),
    )
