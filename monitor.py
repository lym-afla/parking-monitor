import time
import json
from datetime import datetime
from playwright.sync_api import sync_playwright
from config import *

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
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        page.goto(URL, timeout=60000)

        # 1. Ensure "Купить абонемент" tab is active
        tab = page.locator('li[data-tab-name="tab-1"]')
        if not tab.get_attribute("class") or "tabs-nav__item--active" not in tab.get_attribute("class"):
            tab.click()
            page.wait_for_timeout(1000)

        # 2. Open region selector
        page.locator('.select__header').first.click()
        page.get_by_text(
            TARGET_REGION_TEXT,
            exact=True
            ).click()
        page.wait_for_timeout(1000)

        # 3. Open address selector
        page.locator('.select__header', has_text="Выберите адрес парковки").click()
        page.wait_for_timeout(1000)

        # 4. Check target address
        address = page.locator('.select__item', has_text=TARGET_ADDRESS_TEXT)
        enabled = not address.get_attribute("class") or "disabledVar" not in address.get_attribute("class")

        browser.close()
        return enabled

def main():
    state = load_state()

    while True:
        try:
            enabled = check_site()
            state["checks"] += 1
            state["last_check"] = datetime.now().isoformat()

            if enabled and not state["last_enabled"]:
                state["hits"] += 1
                state["alert"] = True # <- signal Telegram bot to send alert

            state["last_enabled"] = enabled
            save_state(state)

        except Exception as e:
            state["error"] = str(e) # <- signal Telegram bot to send error
            save_state(state)

        time.sleep(state.get("interval", CHECK_INTERVAL_SECONDS))

if __name__ == "__main__":
    main()
