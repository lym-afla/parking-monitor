#!/bin/bash
# monitor.sh - Monitoring script for parking monitor

set -e

# Configuration
SERVICE_NAME="parking-service"
VENV_PATH="/opt/parking_monitor/venv"
APP_PATH="/opt/parking_monitor"
LOG_PATH="/opt/parking_monitor/logs"
STATE_PATH="/opt/parking_monitor/state.json"

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
NC='\033[0m'

# Health check function
health_check() {
    echo -e "${BLUE}🏥 Parking Monitor Health Check${NC}"
    echo "=================================================="

    local issues=0

    # Check systemd services
    echo -e "${CYAN}📋 Service Status:${NC}"

    # Monitor service
    if systemctl is-active --quiet "$SERVICE_NAME-monitor"; then
        echo "✅ Monitor service is running"
    else
        echo "❌ Monitor service is not running"
        issues=$((issues + 1))
    fi

    if systemctl is-enabled --quiet "$SERVICE_NAME-monitor"; then
        echo "✅ Monitor service is enabled"
    else
        echo "⚠️  Monitor service is not enabled (won't start on boot)"
    fi

    # Bot service
    if systemctl is-active --quiet "$SERVICE_NAME-bot"; then
        echo "✅ Bot service is running"
    else
        echo "❌ Bot service is not running"
        issues=$((issues + 1))
    fi

    if systemctl is-enabled --quiet "$SERVICE_NAME-bot"; then
        echo "✅ Bot service is enabled"
    else
        echo "⚠️  Bot service is not enabled (won't start on boot)"
    fi

    # Check virtual environment
    echo -e "${CYAN}🐍 Virtual Environment:${NC}"
    if [ -d "$VENV_PATH" ]; then
        echo "✅ Virtual environment exists"
        echo "Python: $($VENV_PATH/bin/python --version)"
    else
        echo "❌ Virtual environment missing"
        issues=$((issues + 1))
    fi

    # Check Playwright installation
    echo -e "${CYAN}🎭 Playwright:${NC}"
    if [ -d "$VENV_PATH" ]; then
        if $VENV_PATH/bin/python -c "from playwright.sync_api import sync_playwright" 2>/dev/null; then
            echo "✅ Playwright is installed"
        else
            echo "❌ Playwright not properly installed"
            issues=$((issues + 1))
        fi
    fi

    # Check log files
    echo -e "${CYAN}📄 Log Files:${NC}"

    # Monitor log file
    if [ -f "$LOG_PATH/monitor.log" ]; then
        local monitor_log_size=$(du -h "$LOG_PATH/monitor.log" | cut -f1)
        echo "✅ Monitor log file exists ($monitor_log_size)"

        # Check for recent activity (last 10 minutes)
        if find "$LOG_PATH/monitor.log" -newermt "-10 minutes" | grep -q .; then
            echo "✅ Recent monitor log activity detected"
        else
            echo "⚠️  No recent monitor log activity (last 10 minutes)"
        fi

        # Check for recent errors
        local monitor_error_count=$(tail -n 100 "$LOG_PATH/monitor.log" | grep -i error | wc -l)
        if [ "$monitor_error_count" -eq 0 ]; then
            echo "✅ No recent errors in monitor logs"
        else
            echo "⚠️  Found $monitor_error_count recent errors in monitor logs"
        fi
    else
        echo "⚠️  Monitor log file not yet created (normal if service hasn't started)"
    fi

    # Bot log file
    if [ -f "$LOG_PATH/bot.log" ]; then
        local bot_log_size=$(du -h "$LOG_PATH/bot.log" | cut -f1)
        echo "✅ Bot log file exists ($bot_log_size)"

        # Check for recent activity (last 10 minutes)
        if find "$LOG_PATH/bot.log" -newermt "-10 minutes" | grep -q .; then
            echo "✅ Recent bot log activity detected"
        else
            echo "⚠️  No recent bot log activity (last 10 minutes)"
        fi

        # Check for recent errors
        local bot_error_count=$(tail -n 100 "$LOG_PATH/bot.log" | grep -i error | wc -l)
        if [ "$bot_error_count" -eq 0 ]; then
            echo "✅ No recent errors in bot logs"
        else
            echo "⚠️  Found $bot_error_count recent errors in bot logs"
        fi
    else
        echo "⚠️  Bot log file not yet created (normal if service hasn't started)"
    fi

    # Check state file
    echo -e "${CYAN}💾 State Management:${NC}"
    if [ -f "$STATE_PATH" ]; then
        echo "✅ State file exists"

        # Check state file modification time
        if find "$STATE_PATH" -newermt "-1 hour" | grep -q .; then
            echo "✅ State file recently updated"
        else
            echo "⚠️  State file not updated in the last hour"
        fi

        # Check if state file is valid JSON
        if python -m json.tool "$STATE_PATH" >/dev/null 2>&1; then
            echo "✅ State file is valid JSON"
        else
            echo "❌ State file is corrupted"
            issues=$((issues + 1))
        fi
    else
        echo "⚠️  State file not found (will be created on first run)"
    fi

    # Check environment configuration
    echo -e "${CYAN}⚙️  Configuration:${NC}"
    if [ -f "$APP_PATH/.env" ]; then
        echo "✅ Environment file exists"

        # Check for required tokens
        if grep -q "TELEGRAM_BOT_TOKEN" "$APP_PATH/.env"; then
            echo "✅ Telegram bot token configured"
        else
            echo "❌ Telegram bot token missing"
            issues=$((issues + 1))
        fi

        if grep -q "TELEGRAM_AUTHORIZED_USER_IDS" "$APP_PATH/.env"; then
            echo "✅ Authorized users configured"
        else
            echo "⚠️  Authorized users not configured"
        fi
    else
        echo "❌ Environment file missing"
        issues=$((issues + 1))
    fi

    # Check disk space
    echo -e "${CYAN}💾 Disk Space:${NC}"
    local disk_usage=$(df "$APP_PATH" | awk 'NR==2 {print $5}' | sed 's/%//')
    if [ "$disk_usage" -lt 80 ]; then
        echo "✅ Disk usage: ${disk_usage}%"
    else
        echo "⚠️  High disk usage: ${disk_usage}%"
        if [ "$disk_usage" -gt 90 ]; then
            issues=$((issues + 1))
        fi
    fi

    # Check memory usage
    echo -e "${CYAN}🧠 Memory Usage:${NC}"
    local mem_usage=$(free | awk 'NR==2{printf "%.0f", $3/$2*100}')
    if [ "$mem_usage" -lt 80 ]; then
        echo "✅ Memory usage: ${mem_usage}%"
    else
        echo "⚠️  High memory usage: ${mem_usage}%"
    fi

    # Check network connectivity (if service is running)
    echo -e "${CYAN}🌐 Network Connectivity:${NC}"
    if systemctl is-active --quiet "$SERVICE_NAME"; then
        # Test connectivity to parking.mos.ru
        if ping -c 1 parking.mos.ru >/dev/null 2>&1; then
            echo "✅ Can reach parking.mos.ru"
        else
            echo "❌ Cannot reach parking.mos.ru"
            issues=$((issues + 1))
        fi
    else
        echo "⚠️  Service not running - cannot test connectivity"
    fi

    # Summary
    echo ""
    echo -e "${CYAN}📊 Health Summary:${NC}"
    if [ $issues -eq 0 ]; then
        echo -e "${GREEN}✅ All systems operational${NC}"
        return 0
    else
        echo -e "${RED}❌ $issues issue(s) detected${NC}"
        return 1
    fi
}

# Resource monitoring
monitor_resources() {
    echo -e "${BLUE}📊 Resource Monitoring${NC}"
    echo "=================================================="

    # CPU usage
    echo -e "${CYAN}🖥️  CPU Usage:${NC}"
    top -bn1 | grep "Cpu(s)" | awk '{print $2}' | sed 's/%us,//'

    # Memory usage
    echo -e "${CYAN}🧠 Memory Usage:${NC}"
    free -h

    # Process information
    echo -e "${CYAN}🔍 Parking Monitor Processes:${NC}"
    ps aux | grep -E "(parking|playwright)" | grep -v grep || echo "No parking monitor processes found"

    # Service status
    echo -e "${CYAN}📋 Service Status:${NC}"
    echo "--- Monitor Service ---"
    systemctl status "$SERVICE_NAME-monitor" --no-pager
    echo
    echo "--- Bot Service ---"
    systemctl status "$SERVICE_NAME-bot" --no-pager

    # Recent logs
    echo -e "${CYAN}📄 Recent Logs (last 10 lines):${NC}"
    if [ -f "$LOG_PATH/monitor.log" ]; then
        echo "--- Monitor Logs ---"
        tail -n 5 "$LOG_PATH/monitor.log"
        echo
    fi
    if [ -f "$LOG_PATH/bot.log" ]; then
        echo "--- Bot Logs ---"
        tail -n 5 "$LOG_PATH/bot.log"
    fi
    if [ ! -f "$LOG_PATH/monitor.log" ] && [ ! -f "$LOG_PATH/bot.log" ]; then
        echo "No log files found"
    fi

    # State file info
    echo -e "${CYAN}💾 State File Info:${NC}"
    if [ -f "$STATE_PATH" ]; then
        echo "Size: $(du -h "$STATE_PATH" | cut -f1)"
        echo "Last modified: $(stat -c %y "$STATE_PATH")"
        echo "Content preview:"
        head -n 5 "$STATE_PATH" | sed 's/^/  /'
    else
        echo "State file not found"
    fi
}

# Continuous monitoring
continuous_monitor() {
    echo -e "${BLUE}🔄 Starting continuous monitoring...${NC}"
    echo "Press Ctrl+C to stop"
    echo ""

    while true; do
        clear
        echo -e "${BLUE}$(date): Parking Monitor Monitor${NC}"
        echo "=================================================="

        # Quick health check
        local monitor_running=false
        local bot_running=false

        if systemctl is-active --quiet "$SERVICE_NAME-monitor"; then
            echo -e "${GREEN}✅ Monitor: RUNNING${NC}"
            monitor_running=true
        else
            echo -e "${RED}❌ Monitor: STOPPED${NC}"
        fi

        if systemctl is-active --quiet "$SERVICE_NAME-bot"; then
            echo -e "${GREEN}✅ Bot: RUNNING${NC}"
            bot_running=true
        else
            echo -e "${RED}❌ Bot: STOPPED${NC}"
        fi

        if $monitor_running && $bot_running; then
            echo -e "${GREEN}🟢 SYSTEM: OPERATIONAL${NC}"
        else
            echo -e "${RED}🔴 SYSTEM: DEGRADED${NC}"
        fi

        # CPU and memory
        local cpu_usage=$(top -bn1 | grep "Cpu(s)" | awk '{print $2}' | sed 's/%us,//')
        local mem_usage=$(free | awk 'NR==2{printf "%.0f", $3/$2*100}')
        echo -e "${CYAN}CPU: ${cpu_usage}% | Memory: ${mem_usage}%${NC}"

        # State file status
        if [ -f "$STATE_PATH" ]; then
            local state_age=$(find "$STATE_PATH" -mmin +5 | wc -l)
            if [ "$state_age" -eq 0 ]; then
                echo -e "${GREEN}✅ State: Recently updated${NC}"
            else
                echo -e "${YELLOW}⚠️  State: Not updated in 5+ minutes${NC}"
            fi
        fi

        # Log tail
        echo -e "${CYAN}Recent activity:${NC}"
        if [ -f "$LOG_PATH/parking.log" ]; then
            tail -n 5 "$LOG_PATH/parking.log" | while read line; do
                echo "  $line"
            done
        else
            echo "  No logs available"
        fi

        sleep 10
    done
}

# Log analysis
analyze_logs() {
    local lines=${1:-100}
    echo -e "${BLUE}📊 Log Analysis (last $lines lines)${NC}"
    echo "=================================================="

    if [ ! -f "$LOG_PATH/monitor.log" ] && [ ! -f "$LOG_PATH/bot.log" ]; then
        echo "❌ No log files found"
        return 1
    fi

    # Error count from both logs
    echo -e "${CYAN}🚨 Error Analysis:${NC}"
    local monitor_error_count=0
    local bot_error_count=0

    if [ -f "$LOG_PATH/monitor.log" ]; then
        monitor_error_count=$(tail -n "$lines" "$LOG_PATH/monitor.log" | grep -i error | wc -l)
    fi

    if [ -f "$LOG_PATH/bot.log" ]; then
        bot_error_count=$(tail -n "$lines" "$LOG_PATH/bot.log" | grep -i error | wc -l)
    fi

    local total_error_count=$((monitor_error_count + bot_error_count))
    echo "Monitor errors: $monitor_error_count"
    echo "Bot errors: $bot_error_count"
    echo "Total errors: $total_error_count"

    if [ $total_error_count -gt 0 ]; then
        echo -e "${CYAN}Recent errors:${NC}"
        if [ $monitor_error_count -gt 0 ]; then
            echo "Monitor errors:"
            tail -n "$lines" "$LOG_PATH/monitor.log" | grep -i error | tail -n 3
        fi
        if [ $bot_error_count -gt 0 ]; then
            echo "Bot errors:"
            tail -n "$lines" "$LOG_PATH/bot.log" | grep -i error | tail -n 3
        fi
    fi

    # Parking monitoring activity (from monitor log)
    if [ -f "$LOG_PATH/monitor.log" ]; then
        echo -e "${CYAN}🅿️  Parking Monitoring Activity:${NC}"
        local check_count=$(tail -n "$lines" "$LOG_PATH/monitor.log" | grep -i "check\|monitor\|scraping" | wc -l)
        echo "Monitoring checks: $check_count"

        if [ $check_count -gt 0 ]; then
            echo -e "${CYAN}Recent monitoring activity:${NC}"
            tail -n "$lines" "$LOG_PATH/monitor.log" | grep -i "check\|monitor\|scraping" | tail -n 3
        fi
    fi

    # Telegram activity (from bot log)
    if [ -f "$LOG_PATH/bot.log" ]; then
        echo -e "${CYAN}📱 Telegram Activity:${NC}"
        local telegram_count=$(tail -n "$lines" "$LOG_PATH/bot.log" | grep -i "telegram\|bot\|message\|command" | wc -l)
        echo "Telegram-related entries: $telegram_count"

        if [ $telegram_count -gt 0 ]; then
            echo -e "${CYAN}Recent Telegram activity:${NC}"
            tail -n "$lines" "$LOG_PATH/bot.log" | grep -i "telegram\|bot\|message\|command" | tail -n 3
        fi
    fi

    # Web scraping activity (from monitor log)
    if [ -f "$LOG_PATH/monitor.log" ]; then
        echo -e "${CYAN}🕸️  Web Scraping Activity:${NC}"
        local scraping_count=$(tail -n "$lines" "$LOG_PATH/monitor.log" | grep -i "playwright\|scrape\|page\|element\|browser" | wc -l)
        echo "Web scraping entries: $scraping_count"

        if [ $scraping_count -gt 0 ]; then
            echo -e "${CYAN}Recent scraping activity:${NC}"
            tail -n "$lines" "$LOG_PATH/monitor.log" | grep -i "playwright\|scrape\|page\|element\|browser" | tail -n 3
        fi
    fi
}

# Check parking spots status
check_parking_status() {
    echo -e "${BLUE}🅿️  Parking Spots Status${NC}"
    echo "=================================================="

    if [ ! -f "$STATE_PATH" ]; then
        echo "❌ State file not found"
        return 1
    fi

    # Extract and display parking status from state file
    echo -e "${CYAN}Current State:${NC}"

    # Use Python to parse JSON nicely
    python -c "
import json
import sys
from datetime import datetime

try:
    with open('$STATE_PATH', 'r') as f:
        state = json.load(f)

    # Display last update time
    if 'last_update' in state:
        print(f'Last update: {state[\"last_update\"]}')

    # Display parking spots
    if 'parking_spots' in state:
        print(f'\\n🚗 Parking Spots ({len(state[\"parking_spots\"])} monitored):')
        for spot in state['parking_spots']:
            status = '✅ Available' if spot.get('available', False) else '❌ Occupied'
            name = spot.get('name', 'Unknown')
            print(f'  {name}: {status}')

    # Display recent alerts
    if 'recent_alerts' in state and state['recent_alerts']:
        print(f'\\n📢 Recent Alerts ({len(state[\"recent_alerts\"])}):')
        for alert in state['recent_alerts'][-5:]:  # Show last 5 alerts
            time = alert.get('time', 'Unknown time')
            message = alert.get('message', 'No message')
            print(f'  {time}: {message}')

except Exception as e:
    print(f'❌ Error reading state file: {e}')
    sys.exit(1)
"
}

# Main script logic
case "${1:-monitor}" in
    "monitor")
        health_check
        ;;
    "continuous")
        continuous_monitor
        ;;
    "resources")
        monitor_resources
        ;;
    "logs")
        analyze_logs "${2:-100}"
        ;;
    "parking")
        check_parking_status
        ;;
    "help")
        echo -e "${BLUE}📋 Available commands:${NC}"
        echo "  $0 monitor     - Run health check (default)"
        echo "  $0 continuous  - Continuous monitoring"
        echo "  $0 resources   - Show resource usage"
        echo "  $0 logs [N]    - Analyze logs (last N lines, default 100)"
        echo "  $0 parking     - Check current parking spots status"
        echo "  $0 help        - Show this help"
        ;;
    *)
        echo -e "${RED}❌ Unknown command: $1${NC}"
        echo "Use '$0 help' for available commands"
        exit 1
        ;;
esac