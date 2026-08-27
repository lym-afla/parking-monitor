#!/usr/bin/env bash
# Operator health helper for the four parking-monitor services.

set -euo pipefail

SERVICE_NAME="parking-service"
UNITS=(
    "$SERVICE_NAME-monitor"
    "$SERVICE_NAME-notifier"
    "$SERVICE_NAME-bot"
    "$SERVICE_NAME-discord"
)
VENV_PATH="/opt/parking_monitor/venv"
LOG_PATH="/var/log/parking-monitor"
STATE_PATH="/var/lib/parking-monitor/data/state.json"
DATABASE_PATH="/var/lib/parking-monitor/data/notifications.sqlite3"
PLAYWRIGHT_BROWSERS_PATH="/var/lib/parking-monitor/ms-playwright"
CONFIG_PATH="/etc/parking-monitor"
LOG_FILES=(monitor.log notifier.log telegram.log discord.log)

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
NC='\033[0m'

component_is_intentionally_disabled() {
    case "$1" in
        bot) [ ! -f "$CONFIG_PATH/telegram-bot.env" ] ;;
        discord) [ ! -f "$CONFIG_PATH/discord-bot.env" ] ;;
        *) return 1 ;;
    esac
}

service_health() {
    local component="$1"
    local unit="$SERVICE_NAME-$component"

    if systemctl is-active --quiet "$unit"; then
        echo "✅ $component service is running"
    elif component_is_intentionally_disabled "$component"; then
        echo "⚪ $component service is intentionally disabled (configuration absent)"
    else
        echo "❌ $component service is not running"
        return 1
    fi
}

health_check() {
    echo -e "${BLUE}Parking Monitor Health Check${NC}"
    echo "=================================================="
    local issues=0
    local component

    echo -e "${CYAN}Service status:${NC}"
    for component in monitor notifier bot discord; do
        service_health "$component" || issues=$((issues + 1))
        if ! systemctl is-enabled --quiet "$SERVICE_NAME-$component"; then
            echo "⚠️  $component service is not enabled at boot"
        fi
    done

    echo -e "${CYAN}Trusted runtime:${NC}"
    if [ -x "$VENV_PATH/bin/python" ]; then
        echo "✅ Virtual environment: $($VENV_PATH/bin/python --version)"
    else
        echo "❌ Virtual environment is missing"
        issues=$((issues + 1))
    fi
    if [ -d "$PLAYWRIGHT_BROWSERS_PATH" ] \
        && PLAYWRIGHT_BROWSERS_PATH="$PLAYWRIGHT_BROWSERS_PATH" \
            "$VENV_PATH/bin/python" \
            -c "from playwright.sync_api import sync_playwright" \
            >/dev/null 2>&1; then
        echo "✅ Playwright and the explicit browser path are available"
    else
        echo "❌ Playwright browser installation is unavailable"
        issues=$((issues + 1))
    fi

    echo -e "${CYAN}Runtime data:${NC}"
    if [ -f "$STATE_PATH" ]; then
        if "$VENV_PATH/bin/python" -m json.tool "$STATE_PATH" >/dev/null 2>&1; then
            echo "✅ State file is valid JSON"
        else
            echo "❌ State file is invalid JSON"
            issues=$((issues + 1))
        fi
    else
        echo "⚪ State file has not been created yet"
    fi
    if [ -f "$DATABASE_PATH" ]; then
        if "$VENV_PATH/bin/python" - "$DATABASE_PATH" <<'PY'
import sqlite3
import sys

connection = sqlite3.connect(f"file:{sys.argv[1]}?mode=ro", uri=True)
try:
    result = connection.execute("PRAGMA quick_check").fetchone()[0]
finally:
    connection.close()
raise SystemExit(0 if result == "ok" else 1)
PY
        then
            echo "✅ SQLite quick check passed"
        else
            echo "❌ SQLite quick check failed"
            issues=$((issues + 1))
        fi
    else
        echo "⚪ Notification database has not been created yet"
    fi

    echo -e "${CYAN}Application logs:${NC}"
    local log_name
    for log_name in "${LOG_FILES[@]}"; do
        if [ -f "$LOG_PATH/$log_name" ]; then
            echo "✅ $log_name ($(du -h "$LOG_PATH/$log_name" | cut -f1))"
        else
            echo "❌ $log_name is missing"
            issues=$((issues + 1))
        fi
    done

    echo -e "${CYAN}Scoped configuration:${NC}"
    for component in telegram-bot discord-bot notifier-telegram notifier-discord; do
        if [ -f "$CONFIG_PATH/$component.env" ]; then
            echo "✅ $component.env is installed"
        else
            echo "⚪ $component.env is absent"
        fi
    done

    echo
    if [ "$issues" -eq 0 ]; then
        echo -e "${GREEN}All required services and runtime checks passed${NC}"
        return 0
    fi
    echo -e "${RED}$issues required health check(s) failed${NC}"
    return 1
}

monitor_resources() {
    echo -e "${BLUE}Parking Monitor Resources${NC}"
    free -h
    echo
    local unit
    for unit in "${UNITS[@]}"; do
        systemctl show "$unit" \
            --property=ActiveState,SubState,MainPID,MemoryCurrent,CPUUsageNSec \
            --no-pager
    done
}

continuous_monitor() {
    while true; do
        clear
        date
        echo
        local component
        for component in monitor notifier bot discord; do
            service_health "$component" || true
        done
        sleep 10
    done
}

analyze_logs() {
    local lines="${1:-100}"
    if ! [[ "$lines" =~ ^[1-9][0-9]*$ ]]; then
        echo "Log line count must be a positive integer" >&2
        return 2
    fi

    local log_name
    local found=false
    for log_name in "${LOG_FILES[@]}"; do
        if [ -f "$LOG_PATH/$log_name" ]; then
            found=true
            echo "--- $log_name ---"
            tail -n "$lines" "$LOG_PATH/$log_name" \
                | grep -i -E 'error|exception|failed|retry|disabled' \
                | tail -n 10 || true
        fi
    done
    if ! "$found"; then
        echo "No application logs are available" >&2
        return 1
    fi
}

check_parking_status() {
    if [ ! -f "$STATE_PATH" ]; then
        echo "State file has not been created yet"
        return 1
    fi
    "$VENV_PATH/bin/python" - "$STATE_PATH" <<'PY'
import json
import sys

with open(sys.argv[1], encoding="utf-8") as state_file:
    state = json.load(state_file)
print(f"available: {bool(state.get('last_enabled', False))}")
print(f"checks: {int(state.get('checks', 0))}")
print(f"hits: {int(state.get('hits', 0))}")
print(f"last check: {state.get('last_check') or 'never'}")
print(f"normal interval: {int(state.get('interval', 0))} seconds")
PY
}

case "${1:-monitor}" in
    monitor) health_check ;;
    continuous) continuous_monitor ;;
    resources) monitor_resources ;;
    logs) analyze_logs "${2:-100}" ;;
    parking) check_parking_status ;;
    help|--help|-h)
        echo "Usage: $0 {monitor|continuous|resources|logs [N]|parking|help}"
        ;;
    *)
        echo "Unknown command: $1" >&2
        exit 2
        ;;
esac
