import os
import re
from dataclasses import dataclass
from typing import Mapping


URL = "https://parking.mos.ru/parking/barrier/subscribe/"

CHECK_INTERVAL_SECONDS = 1800  # 10 minutes

TARGET_REGION_TEXT = "Западный административный округ"
TARGET_ADDRESS_TEXT = "улица Поклонная, дом 11А"

RUNTIME_DATA_DIR = "/var/lib/parking-monitor/data"
STATE_FILE = f"{RUNTIME_DATA_DIR}/state.json"
DATABASE_FILE = f"{RUNTIME_DATA_DIR}/notifications.sqlite3"


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


@dataclass(frozen=True, repr=False)
class TelegramDeliveryConfig:
    telegram_bot_token: str
    telegram_chat_id: int

    def __repr__(self):
        return "TelegramDeliveryConfig(token=<redacted>, destination=configured)"


@dataclass(frozen=True, repr=False)
class DiscordDeliveryConfig:
    discord_bot_token: str
    discord_channel_id: int

    def __repr__(self):
        return "DiscordDeliveryConfig(token=<redacted>, destination=configured)"


@dataclass(frozen=True, repr=False)
class TelegramCommandConfig(TelegramDeliveryConfig):
    telegram_authorized_user_id: int

    def __repr__(self):
        return "TelegramCommandConfig(token=<redacted>, authorization=configured)"


@dataclass(frozen=True, repr=False)
class DiscordCommandConfig(DiscordDeliveryConfig):
    discord_application_id: int
    discord_guild_id: int
    discord_authorized_user_id: int

    def __repr__(self):
        return "DiscordCommandConfig(token=<redacted>, authorization=configured)"


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

_TELEGRAM_TOKEN_PATTERN = re.compile(r"^[0-9]{6,12}:[A-Za-z0-9_-]{30,}$")
_DISCORD_TOKEN_PATTERN = re.compile(
    r"^[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{6,}\.[A-Za-z0-9_-]{20,}$"
)


def _required_integer(env: Mapping[str, str], name: str) -> int:
    try:
        return int(env[name])
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError(f"Invalid numeric environment variable: {name}") from exc


def _optional_channel(env: Mapping[str, str], names: tuple[str, ...]) -> bool:
    """Return whether a channel is present, rejecting incomplete settings."""
    if not any(env.get(name) for name in names):
        return False
    missing = [name for name in names if not env.get(name)]
    if missing:
        raise ValueError(f"Missing required environment variable: {missing[0]}")
    return True


def _validated_token(env: Mapping[str, str], name: str, pattern: re.Pattern[str]) -> str:
    token = env[name]
    if not pattern.fullmatch(token):
        raise ValueError(f"Invalid bot token shape: {name}")
    return token


def load_telegram_delivery_config(
    env: Mapping[str, str] = os.environ,
) -> TelegramDeliveryConfig | None:
    names = ("TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID")
    if not _optional_channel(env, names):
        return None
    return TelegramDeliveryConfig(
        telegram_bot_token=_validated_token(
            env, "TELEGRAM_BOT_TOKEN", _TELEGRAM_TOKEN_PATTERN
        ),
        telegram_chat_id=_required_integer(env, "TELEGRAM_CHAT_ID"),
    )


def load_discord_delivery_config(
    env: Mapping[str, str] = os.environ,
) -> DiscordDeliveryConfig | None:
    names = ("DISCORD_BOT_TOKEN", "DISCORD_CHANNEL_ID")
    if not _optional_channel(env, names):
        return None
    return DiscordDeliveryConfig(
        discord_bot_token=_validated_token(
            env, "DISCORD_BOT_TOKEN", _DISCORD_TOKEN_PATTERN
        ),
        discord_channel_id=_required_integer(env, "DISCORD_CHANNEL_ID"),
    )


def load_telegram_command_config(
    env: Mapping[str, str] = os.environ,
) -> TelegramCommandConfig | None:
    names = (
        "TELEGRAM_BOT_TOKEN",
        "TELEGRAM_CHAT_ID",
        "TELEGRAM_AUTHORIZED_USER_ID",
    )
    if not _optional_channel(env, names):
        return None
    return TelegramCommandConfig(
        telegram_bot_token=_validated_token(
            env, "TELEGRAM_BOT_TOKEN", _TELEGRAM_TOKEN_PATTERN
        ),
        telegram_chat_id=_required_integer(env, "TELEGRAM_CHAT_ID"),
        telegram_authorized_user_id=_required_integer(
            env, "TELEGRAM_AUTHORIZED_USER_ID"
        ),
    )


def load_discord_command_config(
    env: Mapping[str, str] = os.environ,
) -> DiscordCommandConfig | None:
    names = (
        "DISCORD_BOT_TOKEN",
        "DISCORD_APPLICATION_ID",
        "DISCORD_GUILD_ID",
        "DISCORD_CHANNEL_ID",
        "DISCORD_AUTHORIZED_USER_ID",
    )
    if not _optional_channel(env, names):
        return None
    return DiscordCommandConfig(
        discord_bot_token=_validated_token(
            env, "DISCORD_BOT_TOKEN", _DISCORD_TOKEN_PATTERN
        ),
        discord_application_id=_required_integer(env, "DISCORD_APPLICATION_ID"),
        discord_guild_id=_required_integer(env, "DISCORD_GUILD_ID"),
        discord_channel_id=_required_integer(env, "DISCORD_CHANNEL_ID"),
        discord_authorized_user_id=_required_integer(
            env, "DISCORD_AUTHORIZED_USER_ID"
        ),
    )


def load_config(env: Mapping[str, str] = os.environ) -> RuntimeConfig:
    """Load validated runtime notification settings from an environment mapping."""
    missing = [name for name in _REQUIRED_RUNTIME_VARIABLES if not env.get(name)]
    if missing:
        raise ValueError(f"Missing required environment variable: {missing[0]}")

    telegram = load_telegram_command_config(env)
    discord = load_discord_command_config(env)
    if telegram is None or discord is None:  # Guard for type checkers and callers.
        raise ValueError("Missing required notification configuration")
    return RuntimeConfig(
        telegram_bot_token=telegram.telegram_bot_token,
        telegram_chat_id=telegram.telegram_chat_id,
        telegram_authorized_user_id=telegram.telegram_authorized_user_id,
        discord_bot_token=discord.discord_bot_token,
        discord_application_id=discord.discord_application_id,
        discord_guild_id=discord.discord_guild_id,
        discord_channel_id=discord.discord_channel_id,
        discord_authorized_user_id=discord.discord_authorized_user_id,
    )
