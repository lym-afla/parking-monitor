#!/bin/bash
# Parking Monitor Management Script
# Manages the parking-service systemd service and application updates

# Configuration
SERVICE_NAME="parking-service"
SERVICE_COMPONENTS=(monitor notifier bot discord)
START_ORDER=(notifier bot discord monitor)
STOP_ORDER=(monitor discord bot notifier)
APP_DIR="/opt/parking_monitor"
APP_USER="parking_user"
VENV_DIR="/opt/parking_monitor/venv"
LOG_DIR="/opt/parking_monitor/logs"
ENV_FILE="/etc/parking-monitor.env"
LOG_LINES=50
GITHUB_REPO_URL="https://github.com/yourusername/parking-monitor.git"  # Update with actual repo
BRANCH_NAME="main"  # Adjust to your branch

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
NC='\033[0m' # No Color

# Function to print colored output
print_status() {
    echo -e "${GREEN}[INFO]${NC} $1"
}

print_warning() {
    echo -e "${YELLOW}[WARN]${NC} $1"
}

print_error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

print_header() {
    echo -e "${BLUE}[${SERVICE_NAME^^}]${NC} $1"
}

# Check if running as root (needed for service management)
check_root() {
    if [[ $EUID -ne 0 ]]; then
        print_error "This script must be run as root (use sudo)"
        exit 1
    fi
}

# Check if root is needed for specific commands
check_root_for_command() {
    local command="$1"
    case "$command" in
        status|logs|help|monitor)
            # These commands can run without root
            return 0
            ;;
        *)
            # Other commands need root - try to escalate automatically
            if [[ $EUID -ne 0 ]]; then
                print_warning "This command requires root privileges"
                print_status "Attempting to escalate with sudo..."
                exec sudo "$0" "$@"
            fi
            ;;
    esac
}

# Check if services exist
check_service_exists() {
    local missing_services=()

    local component
    for component in "${SERVICE_COMPONENTS[@]}"; do
        if ! systemctl list-unit-files | grep -q "^${SERVICE_NAME}-${component}.service"; then
            missing_services+=("$component")
        fi
    done

    if [ ${#missing_services[@]} -ne 0 ]; then
        print_error "Services not found: ${missing_services[*]}"
        print_status "Run the setup script first to create the services"
        print_status "  cd $APP_DIR"
        print_status "  sudo ./scripts/setup-service.sh"
        exit 1
    fi
}

# Start the services
start_service() {
    print_header "Starting parking monitor services..."
    check_root_for_command "start"
    check_service_exists

    local component
    local started_services=0
    for component in "${START_ORDER[@]}"; do
        if systemctl is-active --quiet "${SERVICE_NAME}-${component}"; then
            print_warning "${component} service is already running"
            continue
        fi

        print_status "Starting ${component} service..."
        systemctl start "${SERVICE_NAME}-${component}"
        sleep 2
        if systemctl is-active --quiet "${SERVICE_NAME}-${component}"; then
            print_status "✅ ${component} service started successfully"
            started_services=$((started_services + 1))
        else
            print_error "❌ Failed to start ${component} service"
            systemctl status "${SERVICE_NAME}-${component}" --no-pager -l
            exit 1
        fi
    done

    if [ "$started_services" -eq 0 ]; then
        print_status "All four services are already running"
    else
        print_status "🎉 All four services are running"
    fi
    status_service
}

# Stop the services
stop_service() {
    print_header "Stopping parking monitor services..."
    check_root_for_command "stop"
    check_service_exists

    local services_stopped=0

    local component
    for component in "${STOP_ORDER[@]}"; do
        if systemctl is-active --quiet "${SERVICE_NAME}-${component}"; then
            print_status "Stopping ${component} service..."
            systemctl stop "${SERVICE_NAME}-${component}"
            services_stopped=$((services_stopped + 1))
        else
            print_status "${component} service was not running"
        fi
    done

    if [ $services_stopped -gt 0 ]; then
        print_status "✅ Services stopped successfully"
    else
        print_status "No services were running"
    fi

    status_service
}

# Restart the services
restart_service() {
    print_header "Restarting parking monitor services..."
    check_root_for_command "restart"
    check_service_exists

    # Stop both services
    stop_service

    # Wait a moment
    sleep 2

    # Start both services
    start_service
}

# Show service status
status_service() {
    print_header "Parking Monitor Services Status"
    check_service_exists

    local component
    for component in "${SERVICE_COMPONENTS[@]}"; do
        echo
        echo -e "${CYAN}${component} service:${NC}"
        if systemctl is-active --quiet "${SERVICE_NAME}-${component}"; then
            echo -e "  ${GREEN}RUNNING${NC}"
        else
            echo -e "  ${RED}STOPPED${NC}"
        fi
        if systemctl is-enabled --quiet "${SERVICE_NAME}-${component}"; then
            echo -e "  ${GREEN}Enabled (starts on boot)${NC}"
        else
            echo -e "  ${YELLOW}Disabled (will not start on boot)${NC}"
        fi
    done

    echo
    echo -e "${CYAN}Detailed Service Status:${NC}"
    for component in "${SERVICE_COMPONENTS[@]}"; do
        echo "--- ${component} service ---"
        systemctl status "${SERVICE_NAME}-${component}" --no-pager -l | grep -v "Loaded:" || true
        echo
    done

    echo
    echo -e "${CYAN}Service Uptime:${NC}"
    for component in "${SERVICE_COMPONENTS[@]}"; do
        echo "${component}: $(systemctl show "${SERVICE_NAME}-${component}" --property=ActiveEnterTimestamp --value 2>/dev/null || echo "Not started")"
    done
}

# Show logs
component_log_file() {
    case "$1" in
        monitor) echo "$LOG_DIR/monitor.log" ;;
        notifier) echo "$LOG_DIR/notifier.log" ;;
        bot|telegram) echo "$LOG_DIR/telegram.log" ;;
        discord) echo "$LOG_DIR/discord.log" ;;
        *) return 2 ;;
    esac
}

tail_log_files() {
    local follow_logs="$1"
    shift
    local requested_files=("$@")
    local existing_files=()
    local log_file

    for log_file in "${requested_files[@]}"; do
        if [ -f "$log_file" ]; then
            existing_files+=("$log_file")
        else
            print_warning "Log file not found: $log_file"
        fi
    done
    if [ "${#existing_files[@]}" -eq 0 ]; then
        print_error "No application log files are available"
        return 1
    fi

    if "$follow_logs"; then
        tail -F -- "${existing_files[@]}"
    else
        for log_file in "${existing_files[@]}"; do
            print_status "$(basename "$log_file"):"
            tail -n "$LOG_LINES" -- "$log_file"
            echo
        done
    fi
}

show_logs() {
    check_root_for_command "logs"
    local follow_logs=false
    local log_type="service"

    # Parse arguments
    while [[ $# -gt 0 ]]; do
        case $1 in
            -f|--follow)
                follow_logs=true
                shift
                ;;
            -t|--type)
                log_type="$2"
                shift 2
                ;;
            -n|--lines)
                LOG_LINES="$2"
                shift 2
                ;;
            *)
                break
                ;;
        esac
    done

    if ! [[ "$LOG_LINES" =~ ^[1-9][0-9]*$ ]]; then
        print_error "Log line count must be a positive integer"
        return 2
    fi

    print_header "Showing logs (type: $log_type, lines: $LOG_LINES)..."

    local journal_args=()
    local component
    for component in "${SERVICE_COMPONENTS[@]}"; do
        journal_args+=("-u" "${SERVICE_NAME}-${component}")
    done

    local log_files=(
        "$LOG_DIR/monitor.log"
        "$LOG_DIR/notifier.log"
        "$LOG_DIR/telegram.log"
        "$LOG_DIR/discord.log"
    )

    case $log_type in
        service|python|app|file|all)
            tail_log_files "$follow_logs" "${log_files[@]}"
            ;;
        systemd|journal)
            if $follow_logs; then
                print_status "Following systemd unit-status journal (Ctrl+C to stop)..."
                journalctl "${journal_args[@]}" -f
            else
                journalctl "${journal_args[@]}" -n "${LOG_LINES}" --no-pager
            fi
            ;;
        monitor|notifier|bot|telegram|discord)
            tail_log_files "$follow_logs" "$(component_log_file "$log_type")"
            ;;
        error|errors)
            local existing_logs=()
            local log_file
            for log_file in "${log_files[@]}"; do
                if [ -f "$log_file" ]; then
                    existing_logs+=("$log_file")
                fi
            done
            if [ "${#existing_logs[@]}" -eq 0 ]; then
                print_error "No application log files are available"
                return 1
            fi
            grep -H -i -E -- \
                'error|exception|failed|authentication|unauthorized|forbidden' \
                "${existing_logs[@]}" | tail -n "$LOG_LINES" || true
            ;;
        *)
            print_error "Unknown log type: $log_type"
            print_status "Available types: service, systemd, monitor, notifier, bot, discord, file, error, all"
            exit 1
            ;;
    esac
}

RUNNING_BEFORE_UPDATE=()

capture_and_stop_running_services() {
    check_service_exists
    RUNNING_BEFORE_UPDATE=()
    local component
    for component in "${SERVICE_COMPONENTS[@]}"; do
        if systemctl is-active --quiet "${SERVICE_NAME}-${component}"; then
            RUNNING_BEFORE_UPDATE+=("$component")
            systemctl stop "${SERVICE_NAME}-${component}"
        fi
    done
}

restore_running_services() {
    local component
    local failed=false
    for component in "${RUNNING_BEFORE_UPDATE[@]}"; do
        systemctl start "${SERVICE_NAME}-${component}"
        if ! systemctl is-active --quiet "${SERVICE_NAME}-${component}"; then
            print_error "${component} service failed to return after update"
            failed=true
        fi
    done
    if "$failed"; then
        return 1
    fi
}

# Update application
update_app() {
    print_header "Updating application..."
    check_root_for_command "update"

    # Check if git repository exists
    if [ ! -d "${APP_DIR}/.git" ]; then
        print_error "No git repository found in ${APP_DIR}"
        print_status "This script requires the application to be installed from git"
        exit 1
    fi

    # Store current directory
    ORIGINAL_DIR=$(pwd)

    # Change to application directory
    cd "${APP_DIR}" || {
        print_error "Cannot access application directory: ${APP_DIR}"
        exit 1
    }

    # Backup current version info
    local current_commit=$(sudo -u "$APP_USER" git rev-parse HEAD 2>/dev/null || echo "unknown")
    print_status "Current version: ${current_commit:0:8}"

    # Fetch latest changes
    print_status "Fetching latest changes from repository..."
    if ! sudo -u "$APP_USER" git fetch origin; then
        print_error "Failed to fetch from repository"
        cd "${ORIGINAL_DIR}"
        exit 1
    fi

    # Check if there are updates
    local latest_commit
    if ! latest_commit=$(sudo -u "$APP_USER" git rev-parse "origin/${BRANCH_NAME}"); then
        print_error "Cannot resolve origin/${BRANCH_NAME}"
        cd "${ORIGINAL_DIR}"
        return 1
    fi
    if [ "$current_commit" = "$latest_commit" ]; then
        print_status "Already up to date; reconciling current service units"
        if ! "$APP_DIR/scripts/setup-service.sh" --install-units; then
            print_error "Service unit reconciliation failed"
            cd "${ORIGINAL_DIR}"
            return 1
        fi
        cd "${ORIGINAL_DIR}"
        return 0
    fi

    print_status "Stopping currently active services for update..."
    capture_and_stop_running_services

    # Pull latest changes
    print_status "Pulling latest version..."
    if ! sudo -u "$APP_USER" git pull origin ${BRANCH_NAME}; then
        print_error "Failed to pull latest changes"
        restore_running_services || true
        cd "${ORIGINAL_DIR}"
        return 1
    fi

    # Show what changed
    local new_commit
    if ! new_commit=$(sudo -u "$APP_USER" git rev-parse HEAD); then
        print_error "Cannot resolve updated revision"
        restore_running_services || true
        cd "${ORIGINAL_DIR}"
        return 1
    fi
    print_status "Updated to version: ${new_commit:0:8}"

    if [ "$current_commit" != "unknown" ] && [ "$current_commit" != "$new_commit" ]; then
        echo
        print_status "Changes in this update:"
        sudo -u "$APP_USER" git log --oneline "${current_commit}..${new_commit}" | head -10 || true
        echo
    fi

    # Preserve the virtual environment and apply executable mode only to
    # reviewed operator scripts.
    print_status "Updating script permissions..."
    if ! chmod 0755 "${APP_DIR}/scripts/"*.sh; then
        print_error "Could not restore operator script modes"
        restore_running_services || true
        cd "${ORIGINAL_DIR}"
        return 1
    fi

    # Activate virtual environment and update dependencies
    print_status "Updating Python dependencies..."
    if [ -x "${VENV_DIR}/bin/python" ]; then
        if ! sudo -u "${APP_USER}" "${VENV_DIR}/bin/python" -m pip \
            install -r "${APP_DIR}/requirements.txt" --quiet --upgrade; then
            print_error "Dependency update failed"
            restore_running_services || true
            cd "${ORIGINAL_DIR}"
            return 1
        fi
        print_status "Dependencies updated successfully"
    else
        print_error "Virtual environment not found; update aborted"
        restore_running_services || true
        cd "${ORIGINAL_DIR}"
        return 1
    fi

    # Reconcile the four rendered units on every update. This also removes the
    # obsolete aggregate unit and verifies daemon-reload/enable operations.
    if ! "$APP_DIR/scripts/setup-service.sh" --install-units; then
        print_error "Service unit installation failed"
        restore_running_services || true
        cd "${ORIGINAL_DIR}"
        return 1
    fi
    if ! restore_running_services; then
        print_error "One or more services failed after update"
        cd "${ORIGINAL_DIR}"
        return 1
    fi

    cd "${ORIGINAL_DIR}"

    print_status "Update completed successfully!"
}

test_service_units() {
    local component
    local failed=false
    for component in "${SERVICE_COMPONENTS[@]}"; do
        local unit="${SERVICE_NAME}-${component}"
        if systemctl is-enabled --quiet "$unit"; then
            echo "  ${component}: enabled"
        else
            echo "  ${component}: NOT ENABLED"
            failed=true
        fi
        if systemctl is-active --quiet "$unit"; then
            echo "  ${component}: active"
        else
            echo "  ${component}: NOT ACTIVE"
            failed=true
        fi
    done
    if "$failed"; then
        return 1
    fi
}

# Test system
test_system() {
    print_header "Testing system..."
    check_root_for_command "test"

    local all_tests_passed=true

    # Test 1: Virtual environment
    echo
    print_status "Test 1: Virtual Environment"
    if [ -d "$VENV_DIR" ] && [ -x "$VENV_DIR/bin/python" ]; then
        echo "  ✅ Virtual environment exists at $VENV_DIR"
        echo "  Python version: $($VENV_DIR/bin/python --version)"
    else
        echo "  ❌ Virtual environment not found at $VENV_DIR"
        all_tests_passed=false
    fi

    # Test 2: Python imports
    echo
    print_status "Test 2: Python Imports"
    if [ -d "$VENV_DIR" ]; then
        source "$VENV_DIR/bin/activate"
        cd "$APP_DIR"
        $VENV_DIR/bin/python -c "
try:
    import telegram_bot
    import discord_bot
    import notifier
    import notification_store
    import command_service
    import monitor
    import config
    print('  ✅ All Python imports successful')
except Exception as e:
    print(f'  ❌ Python imports failed: {e}')
    exit(1)
" || all_tests_passed=false
    else
        echo "  ❌ Cannot test imports - venv not found"
        all_tests_passed=false
    fi

    # Test 3: Playwright
    echo
    print_status "Test 3: Playwright Installation"
    if [ -d "$VENV_DIR" ]; then
        source "$VENV_DIR/bin/activate"
        if $VENV_DIR/bin/python -c "from playwright.sync_api import sync_playwright; print('Playwright available')" 2>/dev/null; then
            echo "  ✅ Playwright is installed"
        else
            echo "  ❌ Playwright not properly installed"
            all_tests_passed=false
        fi
    fi

    # Test 4: all current service units
    echo
    print_status "Test 4: Service Status"
    test_service_units || all_tests_passed=false

    # Test 5: root-owned environment file metadata and required names. Values
    # are never printed.
    echo
    print_status "Test 5: Configuration"
    if [ ! -f "$ENV_FILE" ]; then
        echo "  ❌ Environment file missing: $ENV_FILE"
        all_tests_passed=false
    else
        local metadata
        metadata=$(stat -c '%U:%G %a' "$ENV_FILE")
        if [ "$metadata" != "root:root 600" ]; then
            echo "  ❌ Environment file metadata is $metadata; expected root:root 600"
            all_tests_passed=false
        fi
        local variable_name
        for variable_name in \
            TELEGRAM_BOT_TOKEN TELEGRAM_CHAT_ID TELEGRAM_AUTHORIZED_USER_ID \
            DISCORD_BOT_TOKEN DISCORD_APPLICATION_ID DISCORD_GUILD_ID \
            DISCORD_CHANNEL_ID DISCORD_AUTHORIZED_USER_ID
        do
            if ! grep -q "^${variable_name}=." "$ENV_FILE"; then
                echo "  ❌ Missing required variable name: $variable_name"
                all_tests_passed=false
            fi
        done
        if sudo -u "$APP_USER" test -r "$ENV_FILE"; then
            echo "  ❌ $ENV_FILE is readable by $APP_USER"
            all_tests_passed=false
        fi
    fi

    # Test 6: Log files
    echo
    print_status "Test 6: Log Files"
    if [ -d "$LOG_DIR" ]; then
        echo "  ✅ Log directory exists: $LOG_DIR"
        local log_name
        for log_name in monitor.log notifier.log telegram.log discord.log; do
            if [ -f "$LOG_DIR/$log_name" ]; then
                echo "  ✅ $log_name exists"
            else
                echo "  ❌ $log_name is missing"
                all_tests_passed=false
            fi
        done
    else
        echo "  ❌ Log directory not found"
        all_tests_passed=false
    fi

    # Test 7: Symlink
    echo
    print_status "Test 7: Management Symlink"
    if [ -L "/usr/local/bin/parking-monitor" ]; then
        echo "  ✅ Symlink exists"
        if [ -x "/usr/local/bin/parking-monitor" ]; then
            echo "  ✅ Symlink is executable"
        else
            echo "  ❌ Symlink is not executable"
            all_tests_passed=false
        fi
    else
        echo "  ❌ Symlink not found"
        echo "     Run: cd $APP_DIR && sudo ./scripts/setup-service.sh"
        all_tests_passed=false
    fi

    # Test 8: Permissions
    echo
    print_status "Test 8: File Permissions"
    if [ -O "$APP_DIR" ] || [ "$(stat -c '%U' $APP_DIR)" = "$APP_USER" ]; then
        echo "  ✅ Application directory owned by $APP_USER"
    else
        echo "  ⚠️  Application directory ownership may be incorrect"
    fi

    # Summary
    echo
    if $all_tests_passed; then
        print_status "✅ All tests passed! System is ready."
        return 0
    else
        print_warning "⚠️  Some tests failed. Review output above."
        echo
        print_status "Common fixes:"
        echo "  - Missing venv: Run setup-service.sh"
        echo "  - Missing config: Copy config.py.example and configure"
        echo "  - Service not set up: Run setup-service.sh"
        echo "  - Wrong runtime permissions: rerun setup-service.sh"
        return 1
    fi
}

# Monitor service
monitor_service() {
    local monitor_type="${1:-health}"
    print_header "Service monitoring..."
    check_root_for_command "monitor"

    # Check if monitor.sh exists
    if [ ! -f "$APP_DIR/scripts/monitor.sh" ]; then
        print_error "Monitor script not found at $APP_DIR/scripts/monitor.sh"
        exit 1
    fi

    case "$monitor_type" in
        health)
            bash "$APP_DIR/scripts/monitor.sh" monitor
            ;;
        continuous)
            bash "$APP_DIR/scripts/monitor.sh" continuous
            ;;
        resources)
            bash "$APP_DIR/scripts/monitor.sh" resources
            ;;
        logs)
            bash "$APP_DIR/scripts/monitor.sh" logs "${2:-100}"
            ;;
        *)
            print_error "Unknown monitor type: $monitor_type"
            print_status "Available types: health, continuous, resources, logs"
            exit 1
            ;;
    esac
}

# Show help
show_help() {
    echo "Parking Monitor Management Script"
    echo
    echo "Usage: $0 <command> [options]"
    echo
    echo "Commands:"
    echo "  start                Start the service"
    echo "  stop                 Stop the service"
    echo "  restart              Restart the service"
    echo "  status               Show service status and resource usage"
    echo "  logs [options]       Show logs"
    echo "  update               Update application from git and restart"
    echo "  test                 Test system configuration"
    echo "  monitor [type]       Monitor service health and resources"
    echo "  help                 Show this help message"
    echo
    echo "Log options:"
    echo "  -f, --follow         Follow logs in real-time"
    echo "  -t, --type TYPE      Log type: service, systemd, monitor, notifier, bot, discord, error"
    echo "  -n, --lines N        Number of lines to show (default: $LOG_LINES)"
    echo
    echo "Monitor types:"
    echo "  health               Health check with issue detection (default)"
    echo "  continuous           Live monitoring (refreshes every 10s)"
    echo "  resources            Show detailed resource usage"
    echo "  logs [N]             Analyze logs for errors/warnings (last N lines)"
    echo
    echo "Examples:"
    echo "  $0 start                    # Start the service"
    echo "  $0 logs -f                  # Follow service logs"
    echo "  $0 logs -t python -n 100    # Show 100 lines of Python logs"
    echo "  $0 logs -t error            # Show only error logs"
    echo "  $0 logs -t file -f          # Follow log file"
    echo "  $0 update                   # Update and restart"
    echo "  $0 test                     # Test system"
    echo "  $0 monitor                  # Health check"
    echo "  $0 monitor continuous       # Live monitoring"
    echo "  $0 monitor resources        # Resource usage"
    echo "  $0 monitor logs 200         # Analyze last 200 log lines"
    echo
    echo "Configuration:"
    echo "  Service name: $SERVICE_NAME"
    echo "  App directory: $APP_DIR"
    echo "  App user: $APP_USER"
    echo "  Venv: $VENV_DIR"
    echo "  Logs: $LOG_DIR"
}

# Main script logic
main() {
    case "${1:-help}" in
        start)
            start_service
            ;;
        stop)
            stop_service
            ;;
        restart)
            restart_service
            ;;
        status)
            status_service
            ;;
        logs)
            shift
            show_logs "$@"
            ;;
        update)
            update_app
            ;;
        test)
            test_system
            ;;
        monitor)
            shift
            monitor_service "$@"
            ;;
        help|--help|-h)
            show_help
            ;;
        *)
            print_error "Unknown command: $1"
            echo
            show_help
            exit 1
            ;;
    esac
}

# Run main function with all arguments
main "$@"
