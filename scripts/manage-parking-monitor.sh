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
        service|systemd)
            if $follow_logs; then
                print_status "Following all four service logs (Ctrl+C to stop)..."
                journalctl "${journal_args[@]}" -f
            else
                journalctl "${journal_args[@]}" -n "${LOG_LINES}" --no-pager
            fi
            ;;
        python|app|file)
            local existing_logs=()
            local log_file
            for log_file in "${log_files[@]}"; do
                if [ -f "$log_file" ]; then
                    existing_logs+=("$log_file")
                fi
            done
            if [ "${#existing_logs[@]}" -eq 0 ]; then
                print_warning "Application log files not found; using journalctl"
                if $follow_logs; then
                    journalctl "${journal_args[@]}" -f
                else
                    journalctl "${journal_args[@]}" -n "${LOG_LINES}" --no-pager
                fi
            elif $follow_logs; then
                print_status "Following available application logs (Ctrl+C to stop)..."
                tail -f "${existing_logs[@]}"
            else
                for log_file in "${existing_logs[@]}"; do
                    print_status "$(basename "$log_file"):"
                    tail -n "${LOG_LINES}" "$log_file"
                    echo
                done
            fi
            ;;
        monitor|notifier|bot|telegram|discord)
            if [ "$log_type" = "telegram" ]; then
                component=bot
            else
                component="$log_type"
            fi
            if $follow_logs; then
                journalctl -u "${SERVICE_NAME}-${component}" -f
            else
                journalctl -u "${SERVICE_NAME}-${component}" -n "${LOG_LINES}" --no-pager
            fi
            ;;
        error|errors)
            print_status "Showing error logs from all four services..."
            journalctl "${journal_args[@]}" -n "${LOG_LINES}" --no-pager -p err
            ;;
        all)
            print_status "Showing all logs from all four services..."
            if $follow_logs; then
                journalctl "${journal_args[@]}" -f
            else
                journalctl "${journal_args[@]}" -n "${LOG_LINES}" --no-pager
            fi
            ;;
        *)
            print_error "Unknown log type: $log_type"
            print_status "Available types: service, python, monitor, notifier, bot, discord, file, error, all"
            exit 1
            ;;
    esac
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

    # Check if service is running (we'll need to restart it)
    local was_running=false
    if systemctl is-active --quiet "${SERVICE_NAME}"; then
        was_running=true
        print_status "Service is running, will restart after update"
    fi

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
    local latest_commit=$(sudo -u "$APP_USER" git rev-parse origin/${BRANCH_NAME} 2>/dev/null)
    if [ "$current_commit" = "$latest_commit" ]; then
        print_status "Already up to date"
        cd "${ORIGINAL_DIR}"
        return
    fi

    # Stop service if running
    if $was_running; then
        print_status "Stopping service for update..."
        systemctl stop "${SERVICE_NAME}"
    fi

    # Pull latest changes
    print_status "Pulling latest version..."
    if ! sudo -u "$APP_USER" git pull origin ${BRANCH_NAME}; then
        print_error "Failed to pull latest changes"
        cd "${ORIGINAL_DIR}"
        exit 1
    fi

    # Show what changed
    local new_commit=$(sudo -u "$APP_USER" git rev-parse HEAD)
    print_status "Updated to version: ${new_commit:0:8}"

    if [ "$current_commit" != "unknown" ] && [ "$current_commit" != "$new_commit" ]; then
        echo
        print_status "Changes in this update:"
        sudo -u "$APP_USER" git log --oneline "${current_commit}..${new_commit}" | head -10
        echo
    fi

    # Update file permissions and git config
    print_status "Updating file permissions..."
    chown -R "${APP_USER}:${APP_USER}" "${APP_DIR}"
    chmod +x "${APP_DIR}/scripts/"*.sh 2>/dev/null || true

    # Ensure management script and symlink remain executable after updates
    chmod +x "${APP_DIR}/scripts/manage-parking-monitor.sh"

    # Fix symlink permissions - remove and recreate if needed
    if [ -L "/usr/local/bin/parking-monitor" ]; then
        rm -f "/usr/local/bin/parking-monitor"
    fi
    ln -sf "${APP_DIR}/scripts/manage-parking-monitor.sh" "/usr/local/bin/parking-monitor"
    chmod +x "/usr/local/bin/parking-monitor"

    print_status "Symlink recreated and permissions fixed"

    # Configure git to ignore file mode changes and reset any phantom changes
    sudo -u "${APP_USER}" bash -c "
        cd '${APP_DIR}'
        git config core.filemode false
        git config core.autocrlf false
        git config --global --add safe.directory '${APP_DIR}'
        git checkout -- . 2>/dev/null || true
    "

    # Also add safe.directory for root user (for future updates)
    git config --global --add safe.directory "${APP_DIR}" 2>/dev/null || true

    # Activate virtual environment and update dependencies
    print_status "Updating Python dependencies..."
    if [ -f "${VENV_DIR}/bin/activate" ]; then
        # Use the app user to update dependencies
        sudo -u "${APP_USER}" bash -c "
            source '${VENV_DIR}/bin/activate'
            pip install -r '${APP_DIR}/requirements.txt' --quiet --upgrade
        " 2>/dev/null

        if [ $? -eq 0 ]; then
            print_status "Dependencies updated successfully"
        else
            print_warning "Some dependencies may not have updated properly"
        fi
    else
        print_warning "Virtual environment not found, skipping dependency update"
    fi

    # Reload systemd if service files changed
    if sudo -u "$APP_USER" git diff --name-only "${current_commit}..${new_commit}" 2>/dev/null | grep -q "\.service$"; then
        print_status "Service file changed, reloading systemd..."
        systemctl daemon-reload
    fi

    # Restart service if it was running
    if $was_running; then
        print_status "Restarting service..."
        systemctl start "${SERVICE_NAME}"

        # Wait a moment and check status
        sleep 3
        if systemctl is-active --quiet "${SERVICE_NAME}"; then
            print_status "Service restarted successfully"
        else
            print_error "Service failed to start after update"
            print_status "Check logs for details:"
            journalctl -u "${SERVICE_NAME}" -n 20 --no-pager
            cd "${ORIGINAL_DIR}"
            exit 1
        fi
    fi

    cd "${ORIGINAL_DIR}"

    # Verify symlink is working
    print_status "Verifying symlink functionality..."
    if [ -x "/usr/local/bin/parking-monitor" ]; then
        print_status "Symlink is executable and working"
    else
        print_warning "Symlink may have permission issues"
        print_status "Fixing symlink permissions..."
        chmod +x "/usr/local/bin/parking-monitor"
    fi

    print_status "Update completed successfully!"
}

# Test system
test_system() {
    print_header "Testing system..."
    check_root_for_command "test"

    local all_tests_passed=true

    # Test 1: Virtual environment
    echo
    print_status "Test 1: Virtual Environment"
    if [ -d "$VENV_DIR" ] && [ -f "$VENV_DIR/bin/python" ]; then
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

    # Test 4: Service status
    echo
    print_status "Test 4: Service Status"
    if systemctl is-enabled --quiet "$SERVICE_NAME" 2>/dev/null; then
        echo "  ✅ Service is enabled (will start on boot)"
    else
        echo "  ⚠️  Service is not enabled"
    fi

    if systemctl is-active --quiet "$SERVICE_NAME"; then
        echo "  ✅ Service is running"
    else
        echo "  ❌ Service is not running"
        echo "     Start with: sudo parking-monitor start"
    fi

    # Test 5: Configuration file
    echo
    print_status "Test 5: Configuration"
    if [ -f "$APP_DIR/config.py" ]; then
        echo "  ✅ Configuration file exists (config.py)"

        # Check for required configuration variables
        if [ -d "$VENV_DIR" ]; then
            source "$VENV_DIR/bin/activate"
            cd "$APP_DIR"
            $VENV_DIR/bin/python -c "
import config
required_vars = ['TELEGRAM_BOT_TOKEN', 'TELEGRAM_CHAT_ID']
missing_vars = []
for var in required_vars:
    if not hasattr(config, var) or getattr(config, var) == '' or getattr(config, var).startswith('YOUR_'):
        missing_vars.append(var)

if missing_vars:
    print(f'  ⚠️  Missing or unconfigured variables: {\", \".join(missing_vars)}')
    exit(1)
else:
    print('  ✅ All required configuration variables present')
" || all_tests_passed=false
        fi
    else
        echo "  ❌ Configuration file not found at $APP_DIR/config.py"
        all_tests_passed=false
    fi

    # Test 6: Log files
    echo
    print_status "Test 6: Log Files"
    if [ -d "$LOG_DIR" ]; then
        echo "  ✅ Log directory exists: $LOG_DIR"
        if [ -f "$LOG_DIR/monitor.log" ] && [ -f "$LOG_DIR/bot.log" ]; then
            echo "  ✅ Both log files exist (monitor.log, bot.log)"
            echo "  Last 3 lines of monitor.log:"
            tail -n 3 "$LOG_DIR/monitor.log" | sed 's/^/     /'
            echo "  Last 3 lines of bot.log:"
            tail -n 3 "$LOG_DIR/bot.log" | sed 's/^/     /'
        elif [ -f "$LOG_DIR/monitor.log" ] || [ -f "$LOG_DIR/bot.log" ]; then
            echo "  ⚠️  Partial log files found (normal if only one service has started)"
            if [ -f "$LOG_DIR/monitor.log" ]; then
                echo "    - monitor.log exists"
            fi
            if [ -f "$LOG_DIR/bot.log" ]; then
                echo "    - bot.log exists"
            fi
        else
            echo "  ⚠️  Log files not yet created (normal if services haven't started)"
        fi
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
    else
        print_warning "⚠️  Some tests failed. Review output above."
        echo
        print_status "Common fixes:"
        echo "  - Missing venv: Run setup-service.sh"
        echo "  - Missing config: Copy config.py.example and configure"
        echo "  - Service not set up: Run setup-service.sh"
        echo "  - Wrong permissions: sudo chown -R $APP_USER:$APP_USER $APP_DIR"
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
    echo "  -t, --type TYPE      Log type: service, python, file, error, all (default: service)"
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
