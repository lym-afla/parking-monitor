#!/bin/bash
# Parking Monitor Management Script
# Manages the parking-service systemd service and application updates

# Configuration
SERVICE_NAME="parking-service"
SERVICE_COMPONENTS=(monitor notifier bot discord)
START_ORDER=(notifier bot discord monitor)
STOP_ORDER=(monitor discord bot notifier)
APP_DIR="/opt/parking_monitor"
VENV_DIR="/opt/parking_monitor/venv"
RUNTIME_ROOT="/var/lib/parking-monitor"
DATA_DIR="${RUNTIME_ROOT}/data"
STATE_PATH="${DATA_DIR}/state.json"
DATABASE_PATH="${DATA_DIR}/notifications.sqlite3"
PLAYWRIGHT_BROWSERS_PATH="${RUNTIME_ROOT}/ms-playwright"
LOG_DIR="/var/log/parking-monitor"
CONFIG_DIR="/etc/parking-monitor"
BACKUP_ROOT="${RUNTIME_ROOT}/backups"
SYSTEMD_DIR="/etc/systemd/system"
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
        elif component_is_intentionally_disabled "$component"; then
            print_warning "${component} service is disabled because its scoped configuration is absent"
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

component_is_intentionally_disabled() {
    case "$1" in
        bot) [ ! -f "$CONFIG_DIR/telegram-bot.env" ] ;;
        discord) [ ! -f "$CONFIG_DIR/discord-bot.env" ] ;;
        *) return 1 ;;
    esac
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
    for component in "${SERVICE_COMPONENTS[@]}"; do
        if systemctl is-active --quiet "${SERVICE_NAME}-${component}"; then
            print_error "${component} service did not stop"
            return 1
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

prepare_update_stage() {
    local current_revision="$1"
    local target_revision="$2"
    local stage_root="$3"
    local source_dir="$stage_root/source"
    local stage_venv="$stage_root/venv"
    local stage_browsers="$stage_root/browsers"
    local rendered_units="$stage_root/units"

    git merge-base --is-ancestor "$current_revision" "$target_revision" || {
        print_error "Update is not a fast-forward from the installed revision"
        return 1
    }
    git worktree add --detach "$source_dir" "$target_revision" || return 1
    python3 -m venv "$stage_venv" || return 1
    "$stage_venv/bin/python" -m pip install --upgrade pip --quiet || return 1
    "$stage_venv/bin/python" -m pip install \
        -r "$source_dir/requirements.txt" --quiet || return 1
    PLAYWRIGHT_BROWSERS_PATH="$stage_browsers" \
        "$stage_venv/bin/python" -m playwright install chromium || return 1
    (
        cd "$source_dir" \
            && "$stage_venv/bin/python" -m unittest discover -v
    ) || return 1
    "$stage_venv/bin/python" -m py_compile \
        "$source_dir/config.py" "$source_dir/state_store.py" \
        "$source_dir/command_service.py" "$source_dir/notification_store.py" \
        "$source_dir/notifier.py" "$source_dir/telegram_bot.py" \
        "$source_dir/discord_bot.py" "$source_dir/monitor.py" || return 1
    bash -n "$source_dir/scripts/configure-secrets.sh" \
        "$source_dir/scripts/setup-service.sh" \
        "$source_dir/scripts/manage-parking-monitor.sh" \
        "$source_dir/scripts/monitor.sh" || return 1
    "$source_dir/scripts/setup-service.sh" \
        --render-only "$rendered_units" || return 1
    systemd-analyze verify "$rendered_units"/*.service || return 1
    chown -R root:root "$stage_root" || return 1
}

create_runtime_snapshot() {
    local backup_dir="$1"
    local component
    for component in "${SERVICE_COMPONENTS[@]}"; do
        if systemctl is-active --quiet "${SERVICE_NAME}-${component}"; then
            print_error "Refusing runtime backup while ${component} is active"
            return 1
        fi
    done

    [ ! -e "$backup_dir" ] || {
        print_error "Refusing to reuse runtime snapshot directory"
        return 1
    }
    install -d -o root -g root -m 0700 \
        "$backup_dir" "$backup_dir/units" || return 1
    if [ -f "$STATE_PATH" ]; then
        cp -a -- "$STATE_PATH" "$backup_dir/state.json" || return 1
        : > "$backup_dir/state.data" || return 1
    elif [ -f "$APP_DIR/state.json" ]; then
        cp -a -- "$APP_DIR/state.json" "$backup_dir/state.json" || return 1
        : > "$backup_dir/state.checkout" || return 1
    else
        : > "$backup_dir/state.absent" || return 1
    fi
    local database_source
    if [ -f "$DATABASE_PATH" ]; then
        database_source="$DATABASE_PATH"
        : > "$backup_dir/database.data" || return 1
    elif [ -f "$APP_DIR/notifications.sqlite3" ]; then
        database_source="$APP_DIR/notifications.sqlite3"
        : > "$backup_dir/database.checkout" || return 1
    else
        database_source=
        : > "$backup_dir/database.absent" || return 1
    fi
    if [ -n "$database_source" ]; then
        if ! "$VENV_DIR/bin/python" - "$database_source" \
            "$backup_dir/notifications.sqlite3" <<'PY'
import sqlite3
import sys

source = sqlite3.connect(sys.argv[1])
destination = sqlite3.connect(sys.argv[2])
try:
    source.backup(destination)
finally:
    destination.close()
    source.close()
PY
        then
            return 1
        fi
    fi
    for component in "${SERVICE_COMPONENTS[@]}"; do
        cp -a -- "$SYSTEMD_DIR/${SERVICE_NAME}-${component}.service" \
            "$backup_dir/units/" || return 1
        [ -s "$backup_dir/units/${SERVICE_NAME}-${component}.service" ] \
            || return 1
    done
    : > "$backup_dir/enablement.txt" || return 1
    for component in "${SERVICE_COMPONENTS[@]}"; do
        if systemctl is-enabled --quiet "${SERVICE_NAME}-${component}"; then
            printf '%s enabled\n' "$component" \
                >> "$backup_dir/enablement.txt" || return 1
        else
            printf '%s disabled\n' "$component" \
                >> "$backup_dir/enablement.txt" || return 1
        fi
    done
}

fast_forward_checkout() {
    local expected_revision="$1"
    git pull --ff-only origin "$BRANCH_NAME" || return 1
    [ "$(git rev-parse HEAD)" = "$expected_revision" ] || {
        print_error "Pulled revision does not match the verified staged revision"
        return 1
    }
}

cutover_staged_update() {
    local target_revision="$1"
    local stage_root="$2"
    local backup_dir="$3"
    local old_venv="${APP_DIR}/.venv-before-update"
    local old_browsers="${RUNTIME_ROOT}/.browsers-before-update"

    [ ! -e "$old_venv" ] && [ ! -e "$old_browsers" ] || {
        print_error "A previous update rollback environment still exists"
        return 1
    }
    fast_forward_checkout "$target_revision" || return 1
    mv -- "$VENV_DIR" "$old_venv" || return 1
    mv -- "$stage_root/venv" "$VENV_DIR" || return 1
    if [ -d "$PLAYWRIGHT_BROWSERS_PATH" ]; then
        mv -- "$PLAYWRIGHT_BROWSERS_PATH" "$old_browsers" || return 1
    fi
    cp -a -- "$stage_root/browsers" "$PLAYWRIGHT_BROWSERS_PATH" || return 1
    chown -R root:root "$APP_DIR" "$PLAYWRIGHT_BROWSERS_PATH" || return 1
    chmod -R go-w "$APP_DIR" "$PLAYWRIGHT_BROWSERS_PATH" || return 1
    "$APP_DIR/scripts/setup-service.sh" --install-units || return 1
    printf '%s\n' "$target_revision" \
        > "$backup_dir/cutover-revision.txt" || return 1
}

restore_runtime_snapshot() {
    local backup_dir="$1"
    if [ -f "$backup_dir/state.json" ]; then
        local state_target state_other
        if [ -f "$backup_dir/state.data" ]; then
            state_target="$STATE_PATH"
            state_other="$APP_DIR/state.json"
        elif [ -f "$backup_dir/state.checkout" ]; then
            state_target="$APP_DIR/state.json"
            state_other="$STATE_PATH"
        else
            print_error "Runtime snapshot has no state location record"
            return 1
        fi
        cp -a -- "$backup_dir/state.json" "$state_target.restore" || return 1
        mv -f -- "$state_target.restore" "$state_target" || return 1
        rm -f -- "$state_other" || return 1
    elif [ -f "$backup_dir/state.absent" ]; then
        rm -f -- "$STATE_PATH" "$APP_DIR/state.json" || return 1
    else
        print_error "Runtime snapshot has no state presence record"
        return 1
    fi
    if [ -f "$backup_dir/notifications.sqlite3" ]; then
        local database_target database_other
        if [ -f "$backup_dir/database.data" ]; then
            database_target="$DATABASE_PATH"
            database_other="$APP_DIR/notifications.sqlite3"
        elif [ -f "$backup_dir/database.checkout" ]; then
            database_target="$APP_DIR/notifications.sqlite3"
            database_other="$DATABASE_PATH"
        else
            print_error "Runtime snapshot has no database location record"
            return 1
        fi
        cp -a -- "$backup_dir/notifications.sqlite3" \
            "$database_target.restore" || return 1
        mv -f -- "$database_target.restore" "$database_target" || return 1
        rm -f -- \
            "$database_target-wal" "$database_target-shm" \
            "$database_other" "$database_other-wal" "$database_other-shm" \
            || return 1
    elif [ -f "$backup_dir/database.absent" ]; then
        rm -f -- "$DATABASE_PATH" "$DATABASE_PATH-wal" \
            "$DATABASE_PATH-shm" \
            "$APP_DIR/notifications.sqlite3" \
            "$APP_DIR/notifications.sqlite3-wal" \
            "$APP_DIR/notifications.sqlite3-shm" || return 1
    else
        print_error "Runtime snapshot has no database presence record"
        return 1
    fi
}

rollback_update() {
    local previous_revision="$1"
    local backup_dir="$2"
    local old_venv="${APP_DIR}/.venv-before-update"
    local old_browsers="${RUNTIME_ROOT}/.browsers-before-update"
    local component

    for component in "${SERVICE_COMPONENTS[@]}"; do
        systemctl stop "${SERVICE_NAME}-${component}" || true
    done
    git reset --hard "$previous_revision" || return 1
    if [ -d "$old_venv" ]; then
        if [ -d "$VENV_DIR" ]; then
            mv -- "$VENV_DIR" "$backup_dir/failed-venv" || return 1
        fi
        mv -- "$old_venv" "$VENV_DIR" || return 1
    fi
    if [ -d "$old_browsers" ]; then
        if [ -d "$PLAYWRIGHT_BROWSERS_PATH" ]; then
            mv -- "$PLAYWRIGHT_BROWSERS_PATH" \
                "$backup_dir/failed-browsers" || return 1
        fi
        mv -- "$old_browsers" "$PLAYWRIGHT_BROWSERS_PATH" || return 1
    fi
    restore_runtime_snapshot "$backup_dir" || return 1
    for component in "${SERVICE_COMPONENTS[@]}"; do
        cp -a -- "$backup_dir/units/${SERVICE_NAME}-${component}.service" \
            "$SYSTEMD_DIR/" || return 1
    done
    [ -s "$backup_dir/enablement.txt" ] || return 1
    local recorded_state
    while read -r component recorded_state; do
        case "$recorded_state" in
            enabled) systemctl enable "${SERVICE_NAME}-${component}" || return 1 ;;
            disabled) systemctl disable "${SERVICE_NAME}-${component}" || return 1 ;;
            *) print_error "Invalid recorded enablement state"; return 1 ;;
        esac
    done < "$backup_dir/enablement.txt"
    install -o root -g root -m 0755 \
        "$APP_DIR/scripts/manage-parking-monitor.sh" \
        /usr/local/bin/parking-monitor || return 1
    systemctl daemon-reload || return 1
    restore_running_services || return 1
}

cleanup_update_stage() {
    local stage_root="$1"
    case "$stage_root" in
        /opt/parking-monitor-update.*) ;;
        *) print_error "Refusing to remove unexpected stage path"; return 1 ;;
    esac
    if [ -d "$stage_root/source" ]; then
        git worktree remove --force "$stage_root/source" || return 1
    fi
    rm -rf -- "$stage_root"
}

finalize_update() {
    rm -rf -- "${APP_DIR}/.venv-before-update" \
        "${RUNTIME_ROOT}/.browsers-before-update"
}

run_update_transaction() {
    local previous_revision="$1"
    local target_revision="$2"
    local stage_root="$3"
    local backup_dir="$4"

    prepare_update_stage "$previous_revision" "$target_revision" "$stage_root" || {
        cleanup_update_stage "$stage_root" || true
        return 1
    }
    capture_and_stop_running_services || {
        restore_running_services || true
        cleanup_update_stage "$stage_root" || true
        return 1
    }
    create_runtime_snapshot "$backup_dir" || {
        restore_running_services || true
        cleanup_update_stage "$stage_root" || true
        return 1
    }
    if ! cutover_staged_update "$target_revision" "$stage_root" "$backup_dir"; then
        rollback_update "$previous_revision" "$backup_dir" || true
        cleanup_update_stage "$stage_root" || true
        return 1
    fi
    if ! restore_running_services; then
        rollback_update "$previous_revision" "$backup_dir" || true
        cleanup_update_stage "$stage_root" || true
        return 1
    fi
    finalize_update
    cleanup_update_stage "$stage_root"
}

# Update application through a verified staging environment and rollback gate.
update_app() {
    print_header "Updating application..."
    check_root_for_command "update"
    [ -d "${APP_DIR}/.git" ] || {
        print_error "No git repository found in ${APP_DIR}"
        return 1
    }
    cd "$APP_DIR" || return 1
    if [ -n "$(git status --porcelain)" ]; then
        print_error "Refusing update with local checkout changes"
        return 1
    fi

    local previous_revision target_revision stage_root backup_dir
    previous_revision=$(git rev-parse HEAD) || return 1
    git fetch origin || return 1
    target_revision=$(git rev-parse "origin/${BRANCH_NAME}") || return 1
    if [ "$previous_revision" = "$target_revision" ]; then
        "$APP_DIR/scripts/setup-service.sh" --install-units
        return
    fi

    stage_root=$(mktemp -d /opt/parking-monitor-update.XXXXXX) || return 1
    backup_dir="${BACKUP_ROOT}/update-$(date -u +%Y%m%dT%H%M%SZ)"
    run_update_transaction \
        "$previous_revision" "$target_revision" "$stage_root" "$backup_dir" || {
        print_error "Update failed; rollback was attempted"
        return 1
    }
    print_status "Update completed successfully at ${target_revision:0:8}"
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
        elif component_is_intentionally_disabled "$component"; then
            echo "  ${component}: intentionally disabled (configuration absent)"
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
        if PLAYWRIGHT_BROWSERS_PATH="$PLAYWRIGHT_BROWSERS_PATH" \
            $VENV_DIR/bin/python -c "from playwright.sync_api import sync_playwright; print('Playwright available')" 2>/dev/null \
            && sudo -u parking-monitor-monitor test -r "$PLAYWRIGHT_BROWSERS_PATH"; then
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

    # Test 5: independently optional, root-owned scoped environment files.
    # Values are never printed.
    echo
    print_status "Test 5: Configuration"
    local config_name service_user required_names metadata variable_name
    for config_name in \
        telegram-bot.env discord-bot.env \
        notifier-telegram.env notifier-discord.env
    do
        case "$config_name" in
            telegram-bot.env)
                service_user=parking-monitor-telegram
                required_names="TELEGRAM_BOT_TOKEN TELEGRAM_CHAT_ID TELEGRAM_AUTHORIZED_USER_ID"
                ;;
            discord-bot.env)
                service_user=parking-monitor-discord
                required_names="DISCORD_BOT_TOKEN DISCORD_APPLICATION_ID DISCORD_GUILD_ID DISCORD_CHANNEL_ID DISCORD_AUTHORIZED_USER_ID"
                ;;
            notifier-telegram.env)
                service_user=parking-monitor-notifier
                required_names="TELEGRAM_BOT_TOKEN TELEGRAM_CHAT_ID"
                ;;
            notifier-discord.env)
                service_user=parking-monitor-notifier
                required_names="DISCORD_BOT_TOKEN DISCORD_CHANNEL_ID"
                ;;
        esac
        if [ ! -f "$CONFIG_DIR/$config_name" ]; then
            echo "  ⚪ Channel service disabled: missing $config_name"
            continue
        fi
        metadata=$(stat -c '%U:%G %a' "$CONFIG_DIR/$config_name")
        if [ "$metadata" != "root:root 600" ]; then
            echo "  ❌ $config_name metadata is $metadata; expected root:root 600"
            all_tests_passed=false
        fi
        for variable_name in $required_names; do
            if ! grep -q "^${variable_name}=." "$CONFIG_DIR/$config_name"; then
                echo "  ❌ $config_name is missing variable: $variable_name"
                all_tests_passed=false
            fi
        done
        if sudo -u "$service_user" test -r "$CONFIG_DIR/$config_name"; then
            echo "  ❌ $config_name is directly readable by $service_user"
            all_tests_passed=false
        fi
    done

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

    # Test 7: root-owned management command copy
    echo
    print_status "Test 7: Management Command"
    if [ -f "/usr/local/bin/parking-monitor" ] \
        && [ ! -L "/usr/local/bin/parking-monitor" ] \
        && [ "$(stat -c '%U:%G %a' /usr/local/bin/parking-monitor)" = "root:root 755" ]; then
        echo "  ✅ Root-owned management command copy is installed"
    else
        echo "  ❌ Root-owned management command copy is invalid"
        echo "     Run: cd $APP_DIR && sudo ./scripts/setup-service.sh"
        all_tests_passed=false
    fi

    # Test 8: Permissions
    echo
    print_status "Test 8: File Permissions"
    if [ "$(stat -c '%U:%G' "$APP_DIR")" = "root:root" ] \
        && [ "$(stat -c '%G %a' "$DATA_DIR")" = "parking-monitor 2770" ]; then
        echo "  ✅ Trusted code is root-owned and runtime writes are group-scoped"
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
    echo "  Service users: parking-monitor-monitor/notifier/telegram/discord"
    echo "  Runtime data: $DATA_DIR"
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
