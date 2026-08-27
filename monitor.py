import time
import json
import sys
import calendar
from datetime import datetime, time as datetime_time, timedelta, timezone
from playwright.sync_api import sync_playwright
from config import *

MOSCOW_TIMEZONE = timezone(timedelta(hours=3), "MSK")
MONTH_END_INTERVAL_SECONDS = 300


def get_polling_schedule(now=None, normal_interval=CHECK_INTERVAL_SECONDS):
    """Return the polling interval and mode for the supplied Moscow time."""
    if now is None:
        now = datetime.now(MOSCOW_TIMEZONE)
    elif now.tzinfo is None:
        now = now.replace(tzinfo=MOSCOW_TIMEZONE)
    else:
        now = now.astimezone(MOSCOW_TIMEZONE)

    last_day_number = calendar.monthrange(now.year, now.month)[1]
    month_end = datetime(
        now.year, now.month, last_day_number, tzinfo=MOSCOW_TIMEZONE
    )
    high_frequency_start = datetime.combine(
        (month_end - timedelta(days=6)).date(),
        datetime_time.min,
        tzinfo=MOSCOW_TIMEZONE,
    )
    high_frequency_end = datetime.combine(
        (month_end - timedelta(days=2)).date(),
        datetime_time.min,
        tzinfo=MOSCOW_TIMEZONE,
    )

    if high_frequency_start <= now < high_frequency_end:
        return MONTH_END_INTERVAL_SECONDS, "month-end"
    if now < high_frequency_start:
        seconds_until_start = int((high_frequency_start - now).total_seconds())
        return min(normal_interval, seconds_until_start), "normal"
    return normal_interval, "normal"

def log(message):
    """Print log message with timestamp"""
    timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    print(f"[{timestamp}] {message}", flush=True)

def load_state():
    try:
        with open(STATE_FILE, "r") as f:
            state = json.load(f)
            # Ensure interval has a default value if not present
            if "interval" not in state:
                state["interval"] = CHECK_INTERVAL_SECONDS
            return state
    except:
        return {"checks": 0, "hits": 0, "last_enabled": False, "interval": CHECK_INTERVAL_SECONDS}

def save_state(state):
    with open(STATE_FILE, "w") as f:
        json.dump(state, f)

def check_site():
    log("Starting website check...")
    with sync_playwright() as p:
        log("Launching browser...")
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        
        log(f"Navigating to {URL}")
        page.goto(URL, timeout=60000)

        # 1. Ensure "Купить абонемент" tab is active
        tab = page.locator('li[data-tab-name="tab-1"]')
        if not tab.get_attribute("class") or "tabs-nav__item--active" not in tab.get_attribute("class"):
            log("Activating subscription tab...")
            tab.click()
            page.wait_for_timeout(1000)

        # 2. Open region selector
        log("Selecting region...")
        page.locator('.select__header').first.click()
        page.get_by_text(
            TARGET_REGION_TEXT,
            exact=True
            ).click()
        page.wait_for_timeout(1000)

        # 3. Open address selector
        log("Opening address selector...")
        page.locator('.select__header', has_text="Выберите адрес парковки").click()
        page.wait_for_timeout(1000)

        # 4. Check target address
        log("Checking target address availability...")
        address = page.locator('.select__item', has_text=TARGET_ADDRESS_TEXT)
        enabled = not address.get_attribute("class") or "disabledVar" not in address.get_attribute("class")

        browser.close()
        log(f"Check complete. Parking available: {enabled}")
        return enabled

def main():
    log("=== Parking Monitor Service Started ===")
    log(f"State file: {STATE_FILE}")
    log(f"Target: {TARGET_ADDRESS_TEXT}, {TARGET_REGION_TEXT}")

    initial_state = load_state()
    log(f"Loaded state: checks={initial_state.get('checks', 0)}, hits={initial_state.get('hits', 0)}, interval={initial_state.get('interval', CHECK_INTERVAL_SECONDS)}s")

    while True:
        try:
            # Reload state to pick up interval changes from Telegram bot
            state = load_state()

            enabled = check_site()
            state["checks"] += 1
            state["last_check"] = datetime.now().isoformat()

            if enabled and not state["last_enabled"]:
                log("🚨 PARKING BECAME AVAILABLE! Setting alert flag.")
                state["hits"] += 1
                state["alert"] = True

            state["last_enabled"] = enabled
            state["error"] = None
            save_state(state)


            interval, polling_mode = get_polling_schedule(
                normal_interval=state.get("interval", CHECK_INTERVAL_SECONDS)
            )
            log(
                f"Check #{state['checks']} complete. Polling mode: {polling_mode}. "
                f"Next check in {interval} seconds."
            )

        except Exception as e:
            log(f"ERROR: {str(e)}")
            import traceback
            traceback.print_exc()
            # Reload state to ensure we don't overwrite interval changes
            state = load_state()
            state["error"] = str(e)
            save_state(state)
            interval, polling_mode = get_polling_schedule(
                normal_interval=state.get("interval", CHECK_INTERVAL_SECONDS)
            )
            log(
                f"Polling mode after error: {polling_mode}. "
                f"Next check in {interval} seconds."
            )

        time.sleep(interval)

if __name__ == "__main__":
    main()
