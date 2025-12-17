import json
import asyncio
from datetime import datetime
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes
from config import *

# ---------- State helpers ----------

def load_state():
    try:
        with open(STATE_FILE, "r") as f:
            state = json.load(f)
            # Ensure interval has a default value if not present
            if "interval" not in state:
                state["interval"] = CHECK_INTERVAL_SECONDS
            return state
    except:
        return {"interval": CHECK_INTERVAL_SECONDS}

def save_state(s):
    with open(STATE_FILE, "w") as f:
        json.dump(s, f)

# ---------- Helper functions ----------

def create_main_keyboard():
    """Create the main inline keyboard"""
    keyboard = [
        [
            InlineKeyboardButton("📊 Status", callback_data="status"),
            InlineKeyboardButton("📈 Statistics", callback_data="stats"),
        ],
        [
            InlineKeyboardButton("⚙️ Set Interval", callback_data="interval_menu"),
            InlineKeyboardButton("🔄 Refresh", callback_data="refresh"),
        ],
        [
            InlineKeyboardButton("⚡ Quick Intervals", callback_data="quick_intervals"),
        ]
    ]
    return InlineKeyboardMarkup(keyboard)

def create_interval_keyboard():
    """Create interval selection keyboard"""
    keyboard = [
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
        [
            InlineKeyboardButton("⬅️ Back", callback_data="back_main"),
        ]
    ]
    return InlineKeyboardMarkup(keyboard)

def format_interval(seconds):
    """Format interval in seconds to human-readable format"""
    if seconds < 60:
        return f"{seconds} seconds"
    elif seconds < 3600:
        minutes = seconds // 60
        return f"{minutes} minute{'s' if minutes != 1 else ''}"
    elif seconds < 86400:
        hours = seconds // 3600
        remaining_minutes = (seconds % 3600) // 60
        if remaining_minutes == 0:
            return f"{hours} hour{'s' if hours != 1 else ''}"
        else:
            return f"{hours}h {remaining_minutes}m"
    else:
        days = seconds // 86400
        remaining_hours = (seconds % 86400) // 3600
        if remaining_hours == 0:
            return f"{days} day{'s' if days != 1 else ''}"
        else:
            return f"{days}d {remaining_hours}h"

def format_datetime(iso_string):
    """Format ISO datetime string to friendly format"""
    if iso_string == 'Never' or not iso_string:
        return 'Never'

    try:
        from datetime import datetime
        dt = datetime.fromisoformat(iso_string.replace('Z', '+00:00'))

        # Format: "Jan 17, 2024 at 2:30 PM"
        friendly_format = dt.strftime("%b %d, %Y at %I:%M %p")
        return friendly_format
    except:
        return iso_string

def format_relative_time(iso_string):
    """Format ISO datetime string to relative time"""
    if iso_string == 'Never' or not iso_string:
        return 'Never'

    try:
        from datetime import datetime
        dt = datetime.fromisoformat(iso_string.replace('Z', '+00:00'))
        now = datetime.now(dt.tzinfo)

        diff = now - dt

        if diff.total_seconds() < 60:
            return "Just now"
        elif diff.total_seconds() < 3600:
            minutes = int(diff.total_seconds() / 60)
            return f"{minutes} minute{'s' if minutes != 1 else ''} ago"
        elif diff.total_seconds() < 86400:
            hours = int(diff.total_seconds() / 3600)
            return f"{hours} hour{'s' if hours != 1 else ''} ago"
        else:
            days = diff.days
            return f"{days} day{'s' if days != 1 else ''} ago"
    except:
        return iso_string

def format_status_message(state):
    """Format status message"""
    last_enabled = state.get('last_enabled', False)
    status_icon = "✅ Available" if last_enabled else "❌ Occupied"
    last_check = state.get('last_check', 'Never')
    interval = state.get('interval', 60)

    # Calculate success rate
    checks = state.get('checks', 0)
    hits = state.get('hits', 0)
    success_rate = (hits / checks * 100) if checks > 0 else 0

    # Format last check time
    last_check_friendly = format_datetime(last_check)
    last_check_relative = format_relative_time(last_check)

    message = (
        f"🅿️ *Parking Monitor Status*\n\n"
        f"📊 *Current Status:* {status_icon}\n"
        f"🕐 *Last Check:* {last_check_friendly}\n"
        f"⏰ *{last_check_relative}*\n"
        f"⏱️ *Check Interval:* {format_interval(interval)}\n"
        f"📈 *Success Rate:* {success_rate:.1f}% ({hits}/{checks})\n"
    )

    return message

def format_stats_message(state):
    """Format statistics message"""
    checks = state.get('checks', 0)
    hits = state.get('hits', 0)
    interval = state.get('interval', 60)

    # Calculate additional stats
    success_rate = (hits / checks * 100) if checks > 0 else 0
    uptime_hours = checks * interval / 3600  # Approximate hours of monitoring

    # Format uptime nicely
    if uptime_hours < 1:
        uptime_minutes = int(uptime_hours * 60)
        uptime_str = f"{uptime_minutes} minute{'s' if uptime_minutes != 1 else ''}"
    elif uptime_hours < 24:
        uptime_str = f"{uptime_hours:.1f} hour{'s' if uptime_hours != 1 else ''}"
    else:
        days = int(uptime_hours / 24)
        remaining_hours = uptime_hours % 24
        if remaining_hours == 0:
            uptime_str = f"{days} day{'s' if days != 1 else ''}"
        else:
            uptime_str = f"{days}d {remaining_hours:.0f}h"

    message = (
        f"📊 *Parking Monitor Statistics*\n\n"
        f"🔍 *Total Checks:* {checks:,}\n"
        f"🎯 *Successful Alerts:* {hits:,}\n"
        f"📈 *Success Rate:* {success_rate:.1f}%\n"
        f"⏱️ *Check Interval:* {format_interval(interval)}\n"
        f"⏰ *Monitoring Uptime:* {uptime_str}\n"
    )

    return message

# ---------- Command handlers ----------

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /start command"""
    welcome_message = (
        "🤖 *Parking Monitor Bot*\n\n"
        "I monitor Moscow parking availability and alert you when spots become available!\n\n"
        "Use the buttons below or these commands:\n"
        "/status - Check current status\n"
        "/stats - View statistics\n"
        "/interval <seconds> - Set check interval"
    )

    await update.message.reply_text(
        welcome_message,
        reply_markup=create_main_keyboard(),
        parse_mode="Markdown"
    )

async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /status command"""
    s = load_state()
    message = format_status_message(s)
    await update.message.reply_text(
        message,
        reply_markup=create_main_keyboard(),
        parse_mode="Markdown"
    )

async def stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /stats command"""
    s = load_state()
    message = format_stats_message(s)
    await update.message.reply_text(
        message,
        reply_markup=create_main_keyboard(),
        parse_mode="Markdown"
    )

async def interval_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /interval command"""
    if not context.args:
        await update.message.reply_text(
            "⚙️ *Set Check Interval*\n\n"
            "Please specify the interval in seconds (minimum 60):\n"
            "/interval <seconds>\n\n"
            "Or use the ⚡ Quick Intervals button for preset options.",
            reply_markup=create_main_keyboard(),
            parse_mode="Markdown"
        )
        return

    try:
        new_interval = max(60, int(context.args[0]))
        s = load_state()
        s["interval"] = new_interval
        save_state(s)

        await update.message.reply_text(
            f"✅ *Interval Updated*\n\n"
            f"Check interval set to {format_interval(new_interval)}",
            reply_markup=create_main_keyboard(),
            parse_mode="Markdown"
        )
    except:
        await update.message.reply_text(
            "❌ *Invalid Number*\n\n"
            "Please enter a valid number of seconds (minimum 60).",
            reply_markup=create_main_keyboard(),
            parse_mode="Markdown"
        )

# ---------- Callback query handlers ----------

async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle inline button presses"""
    query = update.callback_query
    await query.answer()  # Acknowledge the button press

    data = query.data
    s = load_state()

    if data == "status":
        message = format_status_message(s)
        await query.edit_message_text(
            text=message,
            reply_markup=create_main_keyboard(),
            parse_mode="Markdown"
        )

    elif data == "stats":
        message = format_stats_message(s)
        await query.edit_message_text(
            text=message,
            reply_markup=create_main_keyboard(),
            parse_mode="Markdown"
        )

    elif data == "interval_menu":
        current_interval = s.get('interval', 60)
        message = (
            f"⚙️ *Set Check Interval*\n\n"
            f"Current interval: {format_interval(current_interval)}\n\n"
            f"Choose a preset interval:"
        )
        await query.edit_message_text(
            text=message,
            reply_markup=create_interval_keyboard(),
            parse_mode="Markdown"
        )

    elif data == "quick_intervals":
        message = (
            "⚡ *Quick Interval Settings*\n\n"
            "Choose from common intervals or use custom settings:"
        )
        await query.edit_message_text(
            text=message,
            reply_markup=create_interval_keyboard(),
            parse_mode="Markdown"
        )

    elif data.startswith("set_interval_"):
        # Extract interval from callback data
        interval = int(data.split("_")[2])
        s["interval"] = interval
        save_state(s)

        message = (
            f"✅ *Interval Updated*\n\n"
            f"Check interval set to {format_interval(interval)}"
        )
        await query.edit_message_text(
            text=message,
            reply_markup=create_main_keyboard(),
            parse_mode="Markdown"
        )

    elif data == "refresh":
        # Just refresh current status
        message = format_status_message(s)
        await query.edit_message_text(
            text=message,
            reply_markup=create_main_keyboard(),
            parse_mode="Markdown"
        )

    elif data == "back_main":
        message = (
            "🤖 *Parking Monitor Bot*\n\n"
            "What would you like to do?"
        )
        await query.edit_message_text(
            text=message,
            reply_markup=create_main_keyboard(),
            parse_mode="Markdown"
        )

# ---------- Background alert task ----------

async def alert_loop(app: Application):
    while True:
        s = load_state()
        if s.get("alert"):
            # Send alert with inline keyboard
            alert_keyboard = InlineKeyboardMarkup([
                [
                    InlineKeyboardButton("📊 Check Status", callback_data="status"),
                    InlineKeyboardButton("📈 View Stats", callback_data="stats"),
                ]
            ])

            await app.bot.send_message(
                chat_id=TELEGRAM_CHAT_ID,
                text=(
                    "🚨 *PARKING AVAILABLE!*\n\n"
                    "A parking spot has become available!\n"
                    "Click below to check current status:"
                ),
                reply_markup=alert_keyboard,
                parse_mode="Markdown"
            )

            s["alert"] = False
            save_state(s)

        await asyncio.sleep(5)

# ---------- App startup hook ----------

async def on_startup(app: Application):
    app.create_task(alert_loop(app))

# ---------- Main ----------

def main():
    app = Application.builder().token(TELEGRAM_BOT_TOKEN).build()

    # Command handlers
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("status", status_command))
    app.add_handler(CommandHandler("stats", stats_command))
    app.add_handler(CommandHandler("interval", interval_command))

    # Callback query handler for inline buttons
    app.add_handler(CallbackQueryHandler(button_callback))

    app.post_init = on_startup  # <-- IMPORTANT

    app.run_polling()

if __name__ == "__main__":
    main()
