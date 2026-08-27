import logging
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import discord

from command_service import ChannelHealth, StatsSnapshot, StatusSnapshot
from config import RuntimeConfig
from discord_bot import (
    DiscordCommands,
    ParkingControlsView,
    build_bot,
    is_authorized,
    main,
)


GUILD_ID = 1476852384826392628
CHANNEL_ID = 1542511880659017792
AUTHORIZED_USER_ID = 1138419941926776893


def runtime_config():
    return RuntimeConfig(
        telegram_bot_token="123456:test-token",
        telegram_chat_id=404346140,
        telegram_authorized_user_id=404346140,
        discord_bot_token="discord-test-token",
        discord_application_id=1542514080810664018,
        discord_guild_id=GUILD_ID,
        discord_channel_id=CHANNEL_ID,
        discord_authorized_user_id=AUTHORIZED_USER_ID,
    )


def interaction(
    guild_id=GUILD_ID,
    channel_id=CHANNEL_ID,
    user_id=AUTHORIZED_USER_ID,
    response_done=False,
):
    return SimpleNamespace(
        guild_id=guild_id,
        channel_id=channel_id,
        user=SimpleNamespace(id=user_id),
        response=SimpleNamespace(
            is_done=MagicMock(return_value=response_done),
            send_message=AsyncMock(),
        ),
        followup=SimpleNamespace(send=AsyncMock()),
    )


def status_snapshot():
    return StatusSnapshot(
        last_check="2026-08-27T11:30:00+03:00",
        parking_available=False,
        normal_interval_seconds=1800,
        effective_interval_seconds=300,
        polling_mode="month-end",
        channel_health={
            "telegram": ChannelHealth(last_delivered_at="2026-08-27T11:00:00+03:00"),
            "discord": ChannelHealth(pending_count=1),
        },
    )


def stats_snapshot():
    return StatsSnapshot(
        checks=12,
        hits=3,
        success_rate_percent=25.0,
        last_check="2026-08-27T11:30:00+03:00",
        channel_health={"telegram": ChannelHealth(), "discord": ChannelHealth()},
    )


class DiscordAuthorizationTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.config = runtime_config()
        self.command_service = MagicMock()
        self.command_service.status.return_value = status_snapshot()
        self.command_service.stats.return_value = stats_snapshot()
        self.command_service.set_normal_interval.return_value = status_snapshot()
        self.commands = DiscordCommands(self.command_service, self.config)

    def test_authorization_requires_exact_guild_channel_and_user(self):
        self.assertTrue(is_authorized(interaction(), self.config))
        self.assertFalse(is_authorized(interaction(guild_id=999), self.config))
        self.assertFalse(is_authorized(interaction(channel_id=999), self.config))
        self.assertFalse(is_authorized(interaction(user_id=999), self.config))
        self.assertFalse(is_authorized(interaction(guild_id=None), self.config))

    async def test_unauthorized_interval_never_reads_or_mutates_state(self):
        request = interaction(user_id=999)

        await self.commands.interval(request, minutes=15)

        self.command_service.status.assert_not_called()
        self.command_service.set_normal_interval.assert_not_called()
        request.response.send_message.assert_awaited_once_with(
            "This interaction is not authorized.", ephemeral=True
        )

    async def test_status_uses_shared_service_and_ephemeral_response(self):
        request = interaction()

        await self.commands.status(request)

        self.command_service.status.assert_called_once_with()
        request.response.send_message.assert_awaited_once()
        args, kwargs = request.response.send_message.await_args
        self.assertIn("Parking Monitor Status", args[0])
        self.assertTrue(kwargs["ephemeral"])

    async def test_stats_uses_shared_service_and_ephemeral_response(self):
        request = interaction()

        await self.commands.stats(request)

        self.command_service.stats.assert_called_once_with()
        args, kwargs = request.response.send_message.await_args
        self.assertIn("Total Checks: 12", args[0])
        self.assertTrue(kwargs["ephemeral"])

    async def test_interval_views_and_updates_shared_normal_interval(self):
        view_request = interaction()
        set_request = interaction()

        await self.commands.interval(view_request)
        await self.commands.interval(set_request, minutes=15)

        self.command_service.status.assert_called_once_with()
        self.command_service.set_normal_interval.assert_called_once_with(900)
        self.assertTrue(view_request.response.send_message.await_args.kwargs["ephemeral"])
        self.assertTrue(set_request.response.send_message.await_args.kwargs["ephemeral"])

    async def test_invalid_interval_returns_ephemeral_error(self):
        request = interaction()
        self.command_service.set_normal_interval.side_effect = ValueError("outside bounds")

        await self.commands.interval(request, minutes=0)

        args, kwargs = request.response.send_message.await_args
        self.assertIn("Invalid interval", args[0])
        self.assertTrue(kwargs["ephemeral"])

    async def test_backend_exception_before_response_gets_sanitized_ephemeral_error(self):
        secret = "discord-secret-token-in-backend-error"
        request = interaction()
        self.command_service.status.side_effect = RuntimeError(secret)

        with self.assertLogs("parking_discord_bot", level="ERROR") as captured:
            await self.commands.status(request)

        request.response.send_message.assert_awaited_once_with(
            "Unable to complete this request right now.", ephemeral=True
        )
        request.followup.send.assert_not_awaited()
        combined_output = "\n".join(captured.output)
        self.assertIn("RuntimeError", combined_output)
        self.assertNotIn(secret, combined_output)
        self.assertNotIn(secret, str(request.response.send_message.await_args))

    async def test_interval_unexpected_backend_exception_uses_error_boundary(self):
        secret = "interval-state-secret"
        request = interaction()
        self.command_service.set_normal_interval.side_effect = OSError(secret)

        with self.assertLogs("parking_discord_bot", level="ERROR") as captured:
            await self.commands.interval(request, minutes=15)

        request.response.send_message.assert_awaited_once_with(
            "Unable to complete this request right now.", ephemeral=True
        )
        combined_output = "\n".join(captured.output)
        self.assertIn("OSError", combined_output)
        self.assertNotIn(secret, combined_output)


class DiscordPersistentControlsTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.command_service = MagicMock()
        self.command_service.status.return_value = status_snapshot()
        self.command_service.stats.return_value = stats_snapshot()
        self.commands = DiscordCommands(self.command_service, runtime_config())
        self.view = ParkingControlsView(self.commands)

    def test_view_is_persistent_and_uses_notifier_custom_ids(self):
        self.assertIsNone(self.view.timeout)
        self.assertTrue(self.view.is_persistent())
        self.assertEqual(
            [item.custom_id for item in self.view.children],
            ["parking:status", "parking:stats"],
        )

    async def test_status_and_stats_buttons_use_shared_ephemeral_handlers(self):
        status_request = interaction()
        stats_request = interaction()
        controls = {item.custom_id: item for item in self.view.children}

        await controls["parking:status"].callback(status_request)
        await controls["parking:stats"].callback(stats_request)

        self.command_service.status.assert_called_once_with()
        self.command_service.stats.assert_called_once_with()
        self.assertTrue(status_request.response.send_message.await_args.kwargs["ephemeral"])
        self.assertTrue(stats_request.response.send_message.await_args.kwargs["ephemeral"])

    async def test_unauthorized_button_does_not_read_state(self):
        request = interaction(channel_id=999)
        status_button = next(
            item for item in self.view.children if item.custom_id == "parking:status"
        )

        await status_button.callback(request)

        self.command_service.status.assert_not_called()
        request.response.send_message.assert_awaited_once_with(
            "This interaction is not authorized.", ephemeral=True
        )

    async def test_backend_exception_after_response_uses_sanitized_ephemeral_followup(self):
        secret = "state-payload-secret-from-backend"
        request = interaction(response_done=True)
        self.command_service.stats.side_effect = LookupError(secret)
        stats_button = next(
            item for item in self.view.children if item.custom_id == "parking:stats"
        )

        with self.assertLogs("parking_discord_bot", level="ERROR") as captured:
            await stats_button.callback(request)

        request.response.send_message.assert_not_awaited()
        request.followup.send.assert_awaited_once_with(
            "Unable to complete this request right now.", ephemeral=True
        )
        combined_output = "\n".join(captured.output)
        self.assertIn("LookupError", combined_output)
        self.assertNotIn(secret, combined_output)
        self.assertNotIn(secret, str(request.followup.send.await_args))


class DiscordBotConfigurationTests(unittest.IsolatedAsyncioTestCase):
    async def test_bot_has_minimal_intents_private_guild_commands_and_view(self):
        bot = build_bot(runtime_config(), MagicMock())
        self.addAsyncCleanup(bot.close)

        self.assertTrue(bot.intents.guilds)
        self.assertFalse(bot.intents.members)
        self.assertFalse(bot.intents.presences)
        self.assertFalse(bot.intents.message_content)
        guild = discord.Object(id=GUILD_ID)
        guild_commands = bot.tree.get_commands(guild=guild)
        self.assertEqual(
            {command.name for command in guild_commands},
            {"status", "stats", "interval"},
        )
        self.assertEqual(bot.tree.get_commands(), [])
        for command in guild_commands:
            with self.subTest(command=command.name):
                self.assertEqual(command.default_permissions.value, 0)
        self.assertEqual(len(bot.persistent_views), 1)

    async def test_setup_hook_syncs_only_the_configured_guild(self):
        bot = build_bot(runtime_config(), MagicMock())
        self.addAsyncCleanup(bot.close)
        bot.tree.sync = AsyncMock(return_value=[])

        await bot.setup_hook()

        bot.tree.sync.assert_awaited_once()
        self.assertEqual(bot.tree.sync.await_args.kwargs["guild"].id, GUILD_ID)


class DiscordStartupTests(unittest.TestCase):
    def test_main_uses_runtime_token_without_enabling_discord_http_url_logs(self):
        bot = SimpleNamespace(run=MagicMock())
        discord_http_logger = logging.getLogger("discord.http")
        previous_level = discord_http_logger.level
        self.addCleanup(discord_http_logger.setLevel, previous_level)
        discord_http_logger.setLevel(logging.NOTSET)

        with (
            patch("discord_bot.load_config", return_value=runtime_config()),
            patch("discord_bot.NotificationStore") as store_type,
            patch("discord_bot.CommandService"),
            patch("discord_bot.build_bot", return_value=bot),
        ):
            store_type.return_value.health_summary = MagicMock()
            main()

        bot.run.assert_called_once_with("discord-test-token", log_handler=None)
        self.assertFalse(discord_http_logger.isEnabledFor(logging.WARNING))


if __name__ == "__main__":
    unittest.main()
