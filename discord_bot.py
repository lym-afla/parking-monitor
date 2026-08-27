"""Private Discord command front end for the parking monitor."""

import logging
from pathlib import Path
from typing import Awaitable, Callable

import discord
from discord import app_commands
from discord.ext import commands

from command_service import ChannelHealth, CommandService, StatsSnapshot, StatusSnapshot
from config import (
    STATE_FILE,
    DATABASE_FILE,
    DiscordCommandConfig,
    RuntimeConfig,
    load_discord_command_config,
)
from notification_store import NotificationStore


LOGGER = logging.getLogger("parking_discord_bot")
DATABASE_PATH = Path(DATABASE_FILE)
GENERIC_FAILURE_MESSAGE = "Unable to complete this request right now."


def is_authorized(
    interaction: discord.Interaction,
    config: RuntimeConfig | DiscordCommandConfig,
) -> bool:
    """Return whether an interaction matches every configured Discord boundary."""
    user = getattr(interaction, "user", None)
    return bool(
        interaction.guild_id == config.discord_guild_id
        and interaction.channel_id == config.discord_channel_id
        and user
        and user.id == config.discord_authorized_user_id
    )


def format_interval(seconds: int) -> str:
    if seconds < 60:
        return f"{seconds} seconds"
    if seconds < 3600:
        minutes = seconds // 60
        return f"{minutes} minute{'s' if minutes != 1 else ''}"
    if seconds < 86400:
        hours = seconds // 3600
        minutes = (seconds % 3600) // 60
        return (
            f"{hours}h {minutes}m"
            if minutes
            else f"{hours} hour{'s' if hours != 1 else ''}"
        )
    days = seconds // 86400
    hours = (seconds % 86400) // 3600
    return f"{days}d {hours}h" if hours else f"{days} day{'s' if days != 1 else ''}"


def _health_line(channel: str, health: ChannelHealth) -> str:
    return (
        f"{channel.title()}: {health.state}; "
        f"last {health.last_delivered_at or 'Never'}; "
        f"delivered {health.delivered_count}, "
        f"pending {health.pending_count}, retrying {health.retrying_count}, "
        f"failed {health.failed_count}"
    )


def _health_lines(health_by_channel) -> str:
    if not health_by_channel:
        return "Notifications: No delivery history"
    return "\n".join(
        _health_line(channel, health)
        for channel, health in sorted(health_by_channel.items())
    )


def format_status_message(status: StatusSnapshot) -> str:
    availability = "Available" if status.parking_available else "Occupied"
    return (
        "**Parking Monitor Status**\n\n"
        f"Current Status: {availability}\n"
        f"Last Check: {status.last_check or 'Never'}\n"
        f"Next Expected Check: {status.next_expected_check or 'Unknown'}\n"
        f"Polling Mode: {status.polling_mode}\n"
        f"Active Interval: {format_interval(status.effective_interval_seconds)}\n"
        f"Normal Interval: {format_interval(status.normal_interval_seconds)}\n\n"
        f"{_health_lines(status.channel_health)}"
    )


def format_stats_message(stats: StatsSnapshot) -> str:
    return (
        "**Parking Monitor Statistics**\n\n"
        f"Total Checks: {stats.checks:,}\n"
        f"Availability Hits: {stats.hits:,}\n"
        f"Hit Rate: {stats.success_rate_percent:.1f}%\n"
        f"Last Check: {stats.last_check or 'Never'}\n\n"
        f"{_health_lines(stats.channel_health)}"
    )


class DiscordCommands:
    """Authorized Discord handlers backed by the shared command service."""

    def __init__(
        self,
        command_service: CommandService,
        config: RuntimeConfig | DiscordCommandConfig,
    ):
        self._command_service = command_service
        self._config = config

    async def _execute(
        self,
        interaction: discord.Interaction,
        action: str,
        operation: Callable[[], Awaitable[None]],
    ) -> None:
        try:
            if not await self._authorize(interaction, action):
                return
            await operation()
        except Exception as exc:
            await self._handle_error(interaction, action, exc)

    @staticmethod
    async def _handle_error(
        interaction: discord.Interaction, action: str, exc: Exception
    ) -> None:
        LOGGER.error(
            "Discord action failed action=%s error_type=%s",
            action,
            type(exc).__name__,
        )
        if interaction.response.is_done():
            await interaction.followup.send(GENERIC_FAILURE_MESSAGE, ephemeral=True)
        else:
            await interaction.response.send_message(
                GENERIC_FAILURE_MESSAGE, ephemeral=True
            )

    async def _authorize(self, interaction: discord.Interaction, action: str) -> bool:
        if is_authorized(interaction, self._config):
            return True
        user = getattr(interaction, "user", None)
        LOGGER.warning(
            "Rejected Discord action=%s guild_id=%s channel_id=%s user_id=%s",
            action,
            interaction.guild_id,
            interaction.channel_id,
            getattr(user, "id", None),
        )
        await interaction.response.send_message(
            "This interaction is not authorized.", ephemeral=True
        )
        return False

    async def status(self, interaction: discord.Interaction) -> None:
        await self._execute(
            interaction, "status", lambda: self._send_status(interaction)
        )

    async def _send_status(self, interaction: discord.Interaction) -> None:
        await interaction.response.send_message(
            format_status_message(self._command_service.status()), ephemeral=True
        )

    async def stats(self, interaction: discord.Interaction) -> None:
        await self._execute(interaction, "stats", lambda: self._send_stats(interaction))

    async def _send_stats(self, interaction: discord.Interaction) -> None:
        await interaction.response.send_message(
            format_stats_message(self._command_service.stats()), ephemeral=True
        )

    async def interval(
        self, interaction: discord.Interaction, minutes: int | None = None
    ) -> None:
        await self._execute(
            interaction,
            "interval",
            lambda: self._send_interval(interaction, minutes),
        )

    async def _send_interval(
        self, interaction: discord.Interaction, minutes: int | None
    ) -> None:
        if minutes is None:
            status = self._command_service.status()
            message = (
                "**Normal Check Interval**\n\n"
                f"Normal interval: {format_interval(status.normal_interval_seconds)}\n"
                f"Active interval: {format_interval(status.effective_interval_seconds)} "
                f"({status.polling_mode})\n\n"
                "Use `/interval minutes:<whole number>` to change it."
            )
        else:
            try:
                status = self._command_service.set_normal_interval(minutes * 60)
            except (TypeError, ValueError):
                message = "Invalid interval. Enter a whole number from 1 through 1440 minutes."
            else:
                message = (
                    "**Interval Updated**\n\n"
                    "Normal check interval set to "
                    f"{format_interval(status.normal_interval_seconds)}."
                )
        await interaction.response.send_message(message, ephemeral=True)


class ParkingControlsView(discord.ui.View):
    """Persistent handlers for controls attached to visible availability alerts."""

    def __init__(self, command_handlers: DiscordCommands):
        super().__init__(timeout=None)
        self._command_handlers = command_handlers

    @discord.ui.button(
        label="Status",
        style=discord.ButtonStyle.secondary,
        custom_id="parking:status",
    )
    async def status_button(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        await self._command_handlers.status(interaction)

    @discord.ui.button(
        label="Stats",
        style=discord.ButtonStyle.secondary,
        custom_id="parking:stats",
    )
    async def stats_button(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        await self._command_handlers.stats(interaction)


class ParkingDiscordBot(commands.Bot):
    """Discord client with only guild discovery and one guild command tree."""

    def __init__(
        self,
        config: RuntimeConfig | DiscordCommandConfig,
        command_service: CommandService,
    ):
        intents = discord.Intents.none()
        intents.guilds = True
        super().__init__(
            command_prefix=commands.when_mentioned,
            intents=intents,
            application_id=config.discord_application_id,
        )
        self._command_guild = discord.Object(id=config.discord_guild_id)
        self.command_handlers = DiscordCommands(command_service, config)
        self._register_command(
            "status", "Show parking and delivery status", self.command_handlers.status
        )
        self._register_command(
            "stats", "Show parking monitor statistics", self.command_handlers.stats
        )
        self._register_command(
            "interval",
            "View or change the normal polling interval",
            self.command_handlers.interval,
        )
        self.add_view(ParkingControlsView(self.command_handlers))

    def _register_command(self, name: str, description: str, callback) -> None:
        command = app_commands.Command(
            name=name, description=description, callback=callback
        )
        command.default_permissions = discord.Permissions.none()
        self.tree.add_command(command, guild=self._command_guild)

    async def setup_hook(self) -> None:
        await self.tree.sync(guild=self._command_guild)


def build_bot(
    config: RuntimeConfig | DiscordCommandConfig,
    command_service: CommandService,
) -> ParkingDiscordBot:
    return ParkingDiscordBot(config, command_service)


def main() -> int:
    logging.basicConfig(level=logging.INFO)
    logging.getLogger("discord.http").setLevel(logging.ERROR)
    try:
        config = load_discord_command_config()
        if config is None:
            LOGGER.warning("Discord command service disabled: configuration absent")
            return 0
        store = NotificationStore(
            DATABASE_PATH,
            secrets=(config.discord_bot_token,),
        )
        command_service = CommandService(
            STATE_FILE, health_provider=store.health_summary
        )
        bot = build_bot(config, command_service)
        LOGGER.info("Discord bot starting")
        bot.run(config.discord_bot_token, log_handler=None)
        return 0
    except Exception as exc:
        LOGGER.error(
            "Discord startup failed error_class=%s",
            type(exc).__name__,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
