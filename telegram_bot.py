"""Private Telegram command front end for the parking monitor."""

import logging
from pathlib import Path

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import Application, CallbackQueryHandler, CommandHandler, ContextTypes

from command_service import ChannelHealth, CommandService, StatsSnapshot, StatusSnapshot
from config import STATE_FILE, RuntimeConfig, load_config
from notification_store import NotificationStore


LOGGER = logging.getLogger("parking_telegram_bot")
DATABASE_PATH = Path(__file__).with_name("notifications.sqlite3")


def is_authorized(update: Update, config: RuntimeConfig) -> bool:
    """Return whether an update is from the one allowed user in the allowed chat."""
    user = update.effective_user
    chat = update.effective_chat
    return bool(
        user
        and chat
        and user.id == config.telegram_authorized_user_id
        and chat.id == config.telegram_chat_id
    )


def create_main_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("📊 Status", callback_data="status"),
                InlineKeyboardButton("📈 Statistics", callback_data="stats"),
            ],
            [
                InlineKeyboardButton("⚙️ Set Interval", callback_data="interval_menu"),
                InlineKeyboardButton("🔄 Refresh", callback_data="refresh"),
            ],
            [InlineKeyboardButton("⚡ Quick Intervals", callback_data="quick_intervals")],
        ]
    )


def create_interval_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("1 min", callback_data="set_interval_60"),
                InlineKeyboardButton("2 min", callback_data="set_interval_120"),
                InlineKeyboardButton("5 min", callback_data="set_interval_300"),
            ],
            [
                InlineKeyboardButton("10 min", callback_data="set_interval_600"),
                InlineKeyboardButton("15 min", callback_data="set_interval_900"),
                InlineKeyboardButton("30 min", callback_data="set_interval_1800"),
            ],
            [InlineKeyboardButton("⬅️ Back", callback_data="back_main")],
        ]
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
        if minutes:
            return f"{hours}h {minutes}m"
        return f"{hours} hour{'s' if hours != 1 else ''}"
    days = seconds // 86400
    hours = (seconds % 86400) // 3600
    if hours:
        return f"{days}d {hours}h"
    return f"{days} day{'s' if days != 1 else ''}"


def _health_line(channel: str, health: ChannelHealth) -> str:
    last_delivery = health.last_delivered_at or "Never"
    return (
        f"*{channel.title()}:* last {last_delivery}; "
        f"pending {health.pending_count}, retrying {health.retrying_count}, "
        f"failed {health.failed_count}"
    )


def _health_lines(health_by_channel) -> str:
    if not health_by_channel:
        return "*Notifications:* No delivery history"
    return "\n".join(
        _health_line(channel, health)
        for channel, health in sorted(health_by_channel.items())
    )


def format_status_message(status: StatusSnapshot) -> str:
    availability = "✅ Available" if status.parking_available else "❌ Occupied"
    return (
        "🅿️ *Parking Monitor Status*\n\n"
        f"📊 *Current Status:* {availability}\n"
        f"🕐 *Last Check:* {status.last_check or 'Never'}\n"
        f"🔄 *Polling Mode:* {status.polling_mode}\n"
        f"⏱️ *Active Interval:* {format_interval(status.effective_interval_seconds)}\n"
        f"⚙️ *Normal Interval:* {format_interval(status.normal_interval_seconds)}\n\n"
        f"{_health_lines(status.channel_health)}"
    )


def format_stats_message(stats: StatsSnapshot) -> str:
    return (
        "📊 *Parking Monitor Statistics*\n\n"
        f"🔍 *Total Checks:* {stats.checks:,}\n"
        f"🎯 *Availability Hits:* {stats.hits:,}\n"
        f"📈 *Hit Rate:* {stats.success_rate_percent:.1f}%\n"
        f"🕐 *Last Check:* {stats.last_check or 'Never'}\n\n"
        f"{_health_lines(stats.channel_health)}"
    )


def _welcome_message() -> str:
    return (
        "🤖 *Parking Monitor Bot*\n\n"
        "I monitor Moscow parking availability and alert you when spots "
        "become available!\n\n"
        "Use the buttons below or these commands:\n"
        "/status - Check current status\n"
        "/stats - View statistics\n"
        "/interval <seconds> - View or set the normal check interval"
    )


class TelegramHandlers:
    """Authorized Telegram handlers backed by the shared command service."""

    def __init__(self, command_service: CommandService, config: RuntimeConfig):
        self._command_service = command_service
        self._config = config

    def _authorize(self, update: Update, action: str) -> bool:
        if is_authorized(update, self._config):
            return True
        user_id = getattr(update.effective_user, "id", None)
        chat_id = getattr(update.effective_chat, "id", None)
        LOGGER.warning(
            "Rejected Telegram action=%s user_id=%s chat_id=%s",
            action,
            user_id,
            chat_id,
        )
        return False

    async def start(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not self._authorize(update, "start"):
            return
        await update.message.reply_text(
            _welcome_message(),
            reply_markup=create_main_keyboard(),
            parse_mode="Markdown",
        )

    async def status(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not self._authorize(update, "status"):
            return
        message = format_status_message(self._command_service.status())
        await update.message.reply_text(
            message,
            reply_markup=create_main_keyboard(),
            parse_mode="Markdown",
        )

    async def stats(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not self._authorize(update, "stats"):
            return
        message = format_stats_message(self._command_service.stats())
        await update.message.reply_text(
            message,
            reply_markup=create_main_keyboard(),
            parse_mode="Markdown",
        )

    async def interval(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not self._authorize(update, "interval"):
            return
        if not context.args:
            status = self._command_service.status()
            message = (
                "⚙️ *Set Normal Check Interval*\n\n"
                f"Current normal interval: {format_interval(status.normal_interval_seconds)}\n"
                f"Active interval: {format_interval(status.effective_interval_seconds)} "
                f"({status.polling_mode})\n\n"
                "Use /interval <seconds> or choose a preset below."
            )
            reply_markup = create_interval_keyboard()
        else:
            try:
                seconds = int(context.args[0])
                status = self._command_service.set_normal_interval(seconds)
            except (TypeError, ValueError):
                await update.message.reply_text(
                    "❌ *Invalid Interval*\n\n"
                    "Enter a whole number from 60 through 86400 seconds.",
                    reply_markup=create_main_keyboard(),
                    parse_mode="Markdown",
                )
                return
            message = (
                "✅ *Interval Updated*\n\n"
                "Normal check interval set to "
                f"{format_interval(status.normal_interval_seconds)}."
            )
            reply_markup = create_main_keyboard()
        await update.message.reply_text(
            message,
            reply_markup=reply_markup,
            parse_mode="Markdown",
        )

    async def callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        query = update.callback_query
        if not self._authorize(update, "callback"):
            await query.answer()
            return

        await query.answer()
        data = query.data
        if data in ("status", "refresh"):
            await self._edit_callback(
                query,
                format_status_message(self._command_service.status()),
                create_main_keyboard(),
            )
        elif data == "stats":
            await self._edit_callback(
                query,
                format_stats_message(self._command_service.stats()),
                create_main_keyboard(),
            )
        elif data in ("interval_menu", "quick_intervals"):
            status = self._command_service.status()
            message = (
                "⚙️ *Set Normal Check Interval*\n\n"
                f"Current normal interval: {format_interval(status.normal_interval_seconds)}\n\n"
                "Choose a preset interval:"
            )
            await self._edit_callback(query, message, create_interval_keyboard())
        elif data.startswith("set_interval_"):
            try:
                seconds = int(data.removeprefix("set_interval_"))
                status = self._command_service.set_normal_interval(seconds)
            except (TypeError, ValueError):
                message = "❌ *Invalid Interval*"
            else:
                message = (
                    "✅ *Interval Updated*\n\n"
                    "Normal check interval set to "
                    f"{format_interval(status.normal_interval_seconds)}."
                )
            await self._edit_callback(query, message, create_main_keyboard())
        elif data == "back_main":
            await self._edit_callback(
                query,
                "🤖 *Parking Monitor Bot*\n\nWhat would you like to do?",
                create_main_keyboard(),
            )

    @staticmethod
    async def _edit_callback(query, message: str, reply_markup) -> None:
        try:
            await query.edit_message_text(
                text=message,
                reply_markup=reply_markup,
                parse_mode="Markdown",
            )
        except Exception as exc:
            if "message is not modified" not in str(exc).lower():
                raise


def build_application(config: RuntimeConfig, command_service: CommandService) -> Application:
    handlers = TelegramHandlers(command_service, config)
    application = Application.builder().token(config.telegram_bot_token).build()
    application.add_handler(CommandHandler("start", handlers.start))
    application.add_handler(CommandHandler("status", handlers.status))
    application.add_handler(CommandHandler("stats", handlers.stats))
    application.add_handler(CommandHandler("interval", handlers.interval))
    application.add_handler(CallbackQueryHandler(handlers.callback))
    return application


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("telegram.request").setLevel(logging.WARNING)
    config = load_config()
    store = NotificationStore(
        DATABASE_PATH,
        secrets=(config.telegram_bot_token, config.discord_bot_token),
    )
    command_service = CommandService(STATE_FILE, health_provider=store.health_summary)
    application = build_application(config, command_service)
    LOGGER.info("Telegram polling starting")
    application.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
