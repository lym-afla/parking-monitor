import logging
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from command_service import ChannelHealth, StatsSnapshot, StatusSnapshot
from config import RuntimeConfig
from telegram_bot import TelegramHandlers, build_application, is_authorized, main


AUTHORIZED_USER_ID = 404346140
AUTHORIZED_CHAT_ID = 404346140


def runtime_config():
    return RuntimeConfig(
        telegram_bot_token="123456:test-token",
        telegram_chat_id=AUTHORIZED_CHAT_ID,
        telegram_authorized_user_id=AUTHORIZED_USER_ID,
        discord_bot_token="test-discord-token",
        discord_application_id=1,
        discord_guild_id=2,
        discord_channel_id=3,
        discord_authorized_user_id=4,
    )


def telegram_update(user_id=AUTHORIZED_USER_ID, chat_id=AUTHORIZED_CHAT_ID):
    return SimpleNamespace(
        effective_user=SimpleNamespace(id=user_id),
        effective_chat=SimpleNamespace(id=chat_id),
        message=SimpleNamespace(reply_text=AsyncMock()),
        callback_query=None,
    )


def callback_update(data, user_id=AUTHORIZED_USER_ID, chat_id=AUTHORIZED_CHAT_ID):
    return SimpleNamespace(
        effective_user=SimpleNamespace(id=user_id),
        effective_chat=SimpleNamespace(id=chat_id),
        message=None,
        callback_query=SimpleNamespace(
            data=data,
            answer=AsyncMock(),
            edit_message_text=AsyncMock(),
        ),
    )


def status_snapshot():
    return StatusSnapshot(
        last_check="2026-08-27T11:30:00+03:00",
        parking_available=False,
        normal_interval_seconds=1800,
        effective_interval_seconds=300,
        polling_mode="month-end",
        next_expected_check="2026-08-27T11:35:00+03:00",
        channel_health={
            "telegram": ChannelHealth(
                last_delivered_at="2026-08-27T11:00:00+03:00",
                delivered_count=4,
            ),
            "discord": ChannelHealth(state="disabled", pending_count=1),
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


class TelegramAuthorizationTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.config = runtime_config()
        self.command_service = MagicMock()
        self.command_service.status.return_value = status_snapshot()
        self.command_service.stats.return_value = stats_snapshot()
        self.command_service.set_normal_interval.return_value = status_snapshot()
        self.handlers = TelegramHandlers(self.command_service, self.config)

    def test_authorization_requires_exact_user_and_chat(self):
        self.assertTrue(is_authorized(telegram_update(), self.config))
        self.assertFalse(
            is_authorized(telegram_update(user_id=999), self.config)
        )
        self.assertFalse(
            is_authorized(telegram_update(chat_id=999), self.config)
        )

    async def test_unauthorized_command_does_not_read_state_or_reply(self):
        update = telegram_update(user_id=999, chat_id=999)

        await self.handlers.status(update, SimpleNamespace(args=[]))

        self.command_service.status.assert_not_called()
        update.message.reply_text.assert_not_awaited()

    async def test_authorized_command_uses_shared_status(self):
        update = telegram_update()

        await self.handlers.status(update, SimpleNamespace(args=[]))

        self.command_service.status.assert_called_once_with()
        update.message.reply_text.assert_awaited_once()
        message = update.message.reply_text.await_args.args[0]
        self.assertIn("Next Expected Check", message)
        self.assertIn("2026-08-27T11:35:00+03:00", message)
        self.assertIn("delivered 4", message)
        self.assertIn("disabled", message.lower())

    async def test_unauthorized_callback_is_acknowledged_without_state_or_data(self):
        update = callback_update("stats", user_id=999)

        await self.handlers.callback(update, SimpleNamespace(args=[]))

        update.callback_query.answer.assert_awaited_once_with()
        update.callback_query.edit_message_text.assert_not_awaited()
        self.command_service.stats.assert_not_called()

    async def test_authorized_status_and_stats_callbacks_use_shared_service(self):
        status_update = callback_update("status")
        stats_update = callback_update("stats")

        await self.handlers.callback(status_update, SimpleNamespace(args=[]))
        await self.handlers.callback(stats_update, SimpleNamespace(args=[]))

        self.command_service.status.assert_called_once_with()
        self.command_service.stats.assert_called_once_with()
        status_update.callback_query.edit_message_text.assert_awaited_once()
        stats_update.callback_query.edit_message_text.assert_awaited_once()

    async def test_interval_command_reads_and_updates_through_shared_service(self):
        view_update = telegram_update()
        set_update = telegram_update()

        await self.handlers.interval(view_update, SimpleNamespace(args=[]))
        await self.handlers.interval(set_update, SimpleNamespace(args=["900"]))

        self.command_service.status.assert_called_once_with()
        self.command_service.set_normal_interval.assert_called_once_with(900)
        view_update.message.reply_text.assert_awaited_once()
        set_update.message.reply_text.assert_awaited_once()

    async def test_unauthorized_start_is_silent(self):
        update = telegram_update(chat_id=999)

        await self.handlers.start(update, SimpleNamespace(args=[]))

        update.message.reply_text.assert_not_awaited()


class TelegramStartupTests(unittest.TestCase):
    def _run_main_with(self, application):
        with (
            patch(
                "telegram_bot.load_telegram_command_config",
                return_value=runtime_config(),
            ),
            patch("telegram_bot.NotificationStore") as store_type,
            patch("telegram_bot.CommandService"),
            patch("telegram_bot.build_application", return_value=application),
        ):
            store_type.return_value.health_summary = MagicMock()
            return main()

    def test_startup_uses_only_polling_bootstrap_to_delete_stale_webhook(self):
        built_application = build_application(runtime_config(), MagicMock())
        self.assertIsNone(built_application.post_init)

        application = SimpleNamespace(
            bot=SimpleNamespace(delete_webhook=AsyncMock()),
            run_polling=MagicMock(),
        )
        self._run_main_with(application)

        application.run_polling.assert_called_once_with(drop_pending_updates=True)
        application.bot.delete_webhook.assert_not_called()

    def test_startup_suppresses_info_request_url_logging(self):
        logger_names = (
            "httpx",
            "httpcore",
            "telegram.request",
            "telegram.request.HTTPXRequest",
        )
        loggers = [logging.getLogger(name) for name in logger_names]
        previous_levels = [logger.level for logger in loggers]
        previous_root_level = logging.getLogger().level
        self.addCleanup(logging.getLogger().setLevel, previous_root_level)
        for logger, previous_level in zip(loggers, previous_levels):
            self.addCleanup(logger.setLevel, previous_level)
            logger.setLevel(logging.NOTSET)
        logging.getLogger().setLevel(logging.INFO)

        application = SimpleNamespace(run_polling=MagicMock())
        self._run_main_with(application)

        for logger in loggers:
            with self.subTest(logger=logger.name):
                self.assertFalse(logger.isEnabledFor(logging.INFO))

    def test_startup_exception_logs_only_class_and_generic_message(self):
        secret = "123456789:startupExceptionMustNeverReachLogsABCDE"
        application = SimpleNamespace(
            run_polling=MagicMock(side_effect=RuntimeError(secret))
        )

        with self.assertLogs("parking_telegram_bot", level="ERROR") as captured:
            exit_code = self._run_main_with(application)

        output = "\n".join(captured.output)
        self.assertEqual(exit_code, 1)
        self.assertIn("RuntimeError", output)
        self.assertIn("startup failed", output.lower())
        self.assertNotIn(secret, output)

    def test_missing_telegram_configuration_disables_only_this_service(self):
        with (
            patch("telegram_bot.load_telegram_command_config", return_value=None),
            patch("telegram_bot.NotificationStore") as store_type,
        ):
            exit_code = main()

        self.assertEqual(exit_code, 0)
        store_type.assert_not_called()


if __name__ == "__main__":
    unittest.main()
