#!/bin/bash
# Service Setup Script for Parking Monitor
# Creates isolated users, sets trust boundaries, and configures systemd services

set -euo pipefail

# Configuration
SERVICE_NAME="parking-service"
APP_DIR="/opt/parking_monitor"
VENV_DIR="${APP_DIR}/venv"
RUNTIME_ROOT="/var/lib/parking-monitor"
DATA_DIR="${RUNTIME_ROOT}/data"
PLAYWRIGHT_BROWSERS_PATH="${RUNTIME_ROOT}/ms-playwright"
LOG_DIR="/var/log/parking-monitor"
RUNTIME_GROUP="parking-monitor"
MONITOR_USER="parking-monitor-monitor"
NOTIFIER_USER="parking-monitor-notifier"
TELEGRAM_USER="parking-monitor-telegram"
DISCORD_USER="parking-monitor-discord"
SYMLINK_PATH="/usr/local/bin/parking-monitor"
CONFIG_DIR="/etc/parking-monitor"
SYSTEMD_DIR="/etc/systemd/system"

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

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
    echo -e "${BLUE}[SETUP]${NC} $1"
}

# Check if running as root
check_root() {
    if [[ $EUID -ne 0 ]]; then
        print_error "This script must be run as root (use sudo)"
        exit 1
    fi
}

# Create four non-login service identities. Their only shared privilege is the
# runtime group used for state.json and SQLite coordination.
create_service_identities() {
    print_header "Configuring isolated service identities..."

    if ! getent group "$RUNTIME_GROUP" >/dev/null; then
        groupadd --system "$RUNTIME_GROUP"
    fi

    local service_user
    for service_user in \
        "$MONITOR_USER" "$NOTIFIER_USER" "$TELEGRAM_USER" "$DISCORD_USER"
    do
        if ! id "$service_user" >/dev/null 2>&1; then
            useradd --system --no-create-home --home-dir /nonexistent \
                --shell /usr/sbin/nologin --gid "$RUNTIME_GROUP" "$service_user"
        fi
    done
    print_status "Four distinct service identities configured"
}

# Pin a regular file descriptor before changing metadata. O_NOFOLLOW prevents a
# service-controlled symlink swap from redirecting privileged operations.
secure_shared_file() {
    local path="$1"
    local mode="$2"
    local create="${3:-false}"
    python3 - "$path" "$RUNTIME_GROUP" "$mode" "$create" <<'PY'
import grp
import os
import stat
import sys

path, group_name, mode_text, create_text = sys.argv[1:]
flags = os.O_RDWR | os.O_CLOEXEC | os.O_NOFOLLOW
if create_text == "true":
    flags |= os.O_CREAT
fd = os.open(path, flags, 0o600)
try:
    metadata = os.fstat(fd)
    if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1:
        raise OSError("refusing non-regular or multiply-linked runtime file")
    os.fchown(fd, 0, grp.getgrnam(group_name).gr_gid)
    os.fchmod(fd, int(mode_text, 8))
finally:
    os.close(fd)
PY
}

# Set up directory permissions
setup_permissions() {
    print_header "Setting up directory permissions..."

    if [ ! -d "$APP_DIR" ]; then
        print_error "Application directory $APP_DIR does not exist"
        print_status "Please deploy the parking monitor application first"
        exit 1
    fi

    local protected_path
    for protected_path in \
        "$RUNTIME_ROOT" "$DATA_DIR" "$PLAYWRIGHT_BROWSERS_PATH" \
        "$LOG_DIR" "$CONFIG_DIR"
    do
        if [ -L "$protected_path" ]; then
            print_error "Refusing symlink at protected path $protected_path"
            return 1
        fi
    done

    mkdir -p "$DATA_DIR" "$PLAYWRIGHT_BROWSERS_PATH" "$LOG_DIR" "$CONFIG_DIR"

    # Trusted code, Git metadata, the virtual environment, and root-invoked
    # scripts are immutable to every network-facing service identity.
    chown -R root:root "$APP_DIR"
    chmod -R go-w "$APP_DIR"
    chmod 0755 "$APP_DIR"

    chown root:root "$RUNTIME_ROOT" "$PLAYWRIGHT_BROWSERS_PATH"
    chmod 0755 "$RUNTIME_ROOT" "$PLAYWRIGHT_BROWSERS_PATH"
    chown root:"$RUNTIME_GROUP" "$DATA_DIR" "$LOG_DIR"
    chmod 2770 "$DATA_DIR"
    chmod 0750 "$LOG_DIR"
    chown root:root "$CONFIG_DIR"
    chmod 0700 "$CONFIG_DIR"

    # Move legacy mutable files out of the checkout only when every possible
    # database user is stopped. This makes direct setup invocations fail closed
    # instead of copying a live SQLite database.
    local runtime_name
    local legacy_runtime_present=false
    for runtime_name in \
        state.json state.json.lock notifications.sqlite3 \
        notifications.sqlite3-wal notifications.sqlite3-shm
    do
        if [ -e "$APP_DIR/$runtime_name" ]; then
            legacy_runtime_present=true
        fi
    done
    if "$legacy_runtime_present"; then
        local unit
        for unit in \
            "${SERVICE_NAME}-monitor" "${SERVICE_NAME}-notifier" \
            "${SERVICE_NAME}-bot" "${SERVICE_NAME}-discord" "${SERVICE_NAME}"
        do
            if systemctl is-active --quiet "$unit"; then
                print_error "Refusing legacy runtime migration while $unit is active"
                return 1
            fi
        done
    fi
    for runtime_name in \
        state.json state.json.lock notifications.sqlite3 \
        notifications.sqlite3-wal notifications.sqlite3-shm
    do
        if [ -e "$APP_DIR/$runtime_name" ] && [ ! -e "$DATA_DIR/$runtime_name" ]; then
            mv -fT -- "$APP_DIR/$runtime_name" "$DATA_DIR/$runtime_name"
        fi
    done

    local runtime_path
    for runtime_name in \
        state.json state.json.lock notifications.sqlite3 \
        notifications.sqlite3-wal notifications.sqlite3-shm
    do
        runtime_path="$DATA_DIR/$runtime_name"
        if [ -L "$runtime_path" ]; then
            print_error "Refusing symlink at runtime path $runtime_path"
            return 1
        fi
        if [ -f "$runtime_path" ]; then
            secure_shared_file "$runtime_path" 0660 || return 1
        elif [ -e "$runtime_path" ]; then
            print_error "Refusing non-file runtime path $runtime_path"
            return 1
        fi
    done

    local log_name
    for log_name in monitor.log notifier.log telegram.log discord.log; do
        protected_path="$LOG_DIR/$log_name"
        if [ -L "$protected_path" ]; then
            print_error "Refusing symlink at log path $protected_path"
            return 1
        fi
        secure_shared_file "$protected_path" 0640 true || return 1
    done

    # Configure Git only for the root-owned operational checkout.
    if [ -d "$APP_DIR/.git" ]; then
        print_status "Configuring git for server deployment..."
        git -C "$APP_DIR" config core.filemode false
        git -C "$APP_DIR" config core.autocrlf false
        git config --global --add safe.directory "$APP_DIR"
        print_status "Git configuration completed"
    fi

    print_status "File permissions configured"
}

# Render one systemd service file. The systemd manager opens EnvironmentFile
# before dropping privileges, so the service account never needs read access to
# the root:root mode 0600 secret file itself.
render_service_unit() {
    local output_directory="$1"
    local component="$2"
    local description="$3"
    local program="$4"
    local log_name="$5"
    local unit_path="${output_directory}/${SERVICE_NAME}-${component}.service"
    local service_user

    case "$component" in
        monitor) service_user="$MONITOR_USER" ;;
        notifier) service_user="$NOTIFIER_USER" ;;
        bot) service_user="$TELEGRAM_USER" ;;
        discord) service_user="$DISCORD_USER" ;;
        *) print_error "Unsupported service component: $component"; return 1 ;;
    esac

    cat > "$unit_path" << EOF
[Unit]
Description=${description}
Wants=network-online.target
After=network-online.target

[Service]
Type=simple
User=${service_user}
Group=${RUNTIME_GROUP}
WorkingDirectory=${APP_DIR}
Environment=PATH=${APP_DIR}/venv/bin
Environment=PYTHONUNBUFFERED=1
EOF

    case "$component" in
        monitor)
            echo "Environment=PLAYWRIGHT_BROWSERS_PATH=${PLAYWRIGHT_BROWSERS_PATH}" \
                >> "$unit_path"
            ;;
        notifier)
            echo "EnvironmentFile=-${CONFIG_DIR}/notifier-telegram.env" >> "$unit_path"
            echo "EnvironmentFile=-${CONFIG_DIR}/notifier-discord.env" >> "$unit_path"
            ;;
        bot)
            echo "EnvironmentFile=-${CONFIG_DIR}/telegram-bot.env" >> "$unit_path"
            ;;
        discord)
            echo "EnvironmentFile=-${CONFIG_DIR}/discord-bot.env" >> "$unit_path"
            ;;
    esac

    cat >> "$unit_path" << EOF
ExecStart=${APP_DIR}/venv/bin/python ${APP_DIR}/${program}
Restart=on-failure
RestartSec=10
StandardOutput=append:${LOG_DIR}/${log_name}
StandardError=append:${LOG_DIR}/${log_name}
UMask=0007

# Security settings
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ProtectHome=true
PrivateDevices=true
ProtectKernelTunables=true
ProtectKernelModules=true
ProtectControlGroups=true
LockPersonality=true
RestrictSUIDSGID=true
ReadWritePaths=${DATA_DIR}
EOF

    cat >> "$unit_path" << EOF

[Install]
WantedBy=multi-user.target
EOF
}

# Render the exact four units without requiring root. The installer uses this
# same function so tests exercise the output that is actually installed.
render_service_files() {
    local output_directory="$1"
    mkdir -p "$output_directory"

    render_service_unit "$output_directory" monitor \
        "Parking Monitor - Web Scraper" monitor.py monitor.log
    render_service_unit "$output_directory" notifier \
        "Parking Monitor - Notification Delivery Worker" notifier.py notifier.log
    render_service_unit "$output_directory" bot \
        "Parking Monitor - Private Telegram Bot" telegram_bot.py telegram.log
    render_service_unit "$output_directory" discord \
        "Parking Monitor - Private Discord Bot" discord_bot.py discord.log
}

# Create systemd service files.
create_service_file() {
    print_header "Creating systemd service files..."
    render_service_files "$SYSTEMD_DIR"
    chmod 0644 "$SYSTEMD_DIR/${SERVICE_NAME}-"*.service

    print_status "Service files created:"
    print_status "  - ${SERVICE_NAME}-monitor.service (web scraper)"
    print_status "  - ${SERVICE_NAME}-notifier.service (delivery worker)"
    print_status "  - ${SERVICE_NAME}-bot.service (private Telegram bot)"
    print_status "  - ${SERVICE_NAME}-discord.service (private Discord bot)"

    remove_legacy_aggregate_service
    systemctl daemon-reload
    print_status "Systemd configuration reloaded"
}

# Retire the former aggregate oneshot unit during upgrades. Stopping and
# disabling precede removal so a reboot cannot resurrect stale orchestration.
remove_legacy_aggregate_service() {
    local legacy_unit="${SERVICE_NAME}.service"
    local legacy_path="${SYSTEMD_DIR}/${legacy_unit}"

    if [ ! -e "$legacy_path" ]; then
        return 0
    fi
    if systemctl is-active --quiet "$legacy_unit"; then
        systemctl stop "$legacy_unit"
    fi
    systemctl disable "$legacy_unit"
    rm -f -- "$legacy_path"
    print_status "Removed obsolete ${legacy_unit}"
}

# Install an immutable root-owned copy of the management script.
create_management_command() {
    print_header "Installing management command..."

    local script_path="$APP_DIR/scripts/manage-parking-monitor.sh"

    if [ ! -f "$script_path" ]; then
        print_error "Management script not found: $script_path"
        return 1
    fi
    if ! install -o root -g root -m 0755 "$script_path" "$SYMLINK_PATH"; then
        print_error "Management command installation failed"
        return 1
    fi
    if [ -f "$SYMLINK_PATH" ] && [ ! -L "$SYMLINK_PATH" ]; then
        print_status "Root-owned management command installed: $SYMLINK_PATH"
        print_status "You can now use: sudo parking-monitor <command>"
    else
        print_error "Management command test failed"
        return 1
    fi
}

# Enable services
enable_service() {
    print_header "Enabling services..."

    # Enable all units without starting them. The separate secret installer
    # must run before the first start.
    systemctl enable \
        "$SERVICE_NAME-monitor" \
        "$SERVICE_NAME-notifier" \
        "$SERVICE_NAME-bot" \
        "$SERVICE_NAME-discord"
    print_status "All four services enabled for automatic startup"

    # Don't start automatically, let user start them manually
    print_status "Services are ready but not started"
    print_status "Use: sudo parking-monitor start"
}

# Show setup summary
show_summary() {
    print_header "Setup Summary"
    echo
    print_status "Service users: $MONITOR_USER, $NOTIFIER_USER, $TELEGRAM_USER, $DISCORD_USER"
    print_status "Application directory: $APP_DIR"
    print_status "Runtime data directory: $DATA_DIR"
    print_status "Playwright browser directory: $PLAYWRIGHT_BROWSERS_PATH"
    print_status "Log directory: $LOG_DIR"
    print_status "Config directory: $CONFIG_DIR"
    print_status "Service name: $SERVICE_NAME"
    print_status "Management command: sudo parking-monitor <command>"
    echo
    print_status "Architecture:"
    echo "  - Monitor Service: Continuously scrapes parking website"
    echo "  - Notifier Service: Delivers queued events independently"
    echo "  - Telegram Bot Service: Private Telegram commands"
    echo "  - Discord Bot Service: Private Discord commands"
    echo "  - SQLite Store: Durable per-channel delivery state"
    echo
    print_status "Available commands:"
    echo "  sudo parking-monitor start     # Start all four services"
    echo "  sudo parking-monitor stop      # Stop all four services"
    echo "  sudo parking-monitor restart   # Restart all four services"
    echo "  sudo parking-monitor status    # Show all four service statuses"
    echo "  sudo parking-monitor logs      # Show all four service logs"
    echo "  sudo parking-monitor logs -f   # Follow logs in real-time"
    echo "  sudo parking-monitor update    # Update from git and restart"
    echo "  sudo parking-monitor test      # Test system connections"
    echo "  sudo parking-monitor monitor   # Monitor service health"
    echo
    print_status "Service status:"
    echo "  Monitor: $(systemctl is-enabled "$SERVICE_NAME-monitor" --quiet && echo "Enabled" || echo "Disabled") | $(systemctl is-active "$SERVICE_NAME-monitor" --quiet && echo "Running" || echo "Stopped")"
    echo "  Notifier: $(systemctl is-enabled "$SERVICE_NAME-notifier" --quiet && echo "Enabled" || echo "Disabled") | $(systemctl is-active "$SERVICE_NAME-notifier" --quiet && echo "Running" || echo "Stopped")"
    echo "  Bot: $(systemctl is-enabled "$SERVICE_NAME-bot" --quiet && echo "Enabled" || echo "Disabled") | $(systemctl is-active "$SERVICE_NAME-bot" --quiet && echo "Running" || echo "Stopped")"
    echo "  Discord: $(systemctl is-enabled "$SERVICE_NAME-discord" --quiet && echo "Enabled" || echo "Disabled") | $(systemctl is-active "$SERVICE_NAME-discord" --quiet && echo "Running" || echo "Stopped")"
    echo
    print_status "Configuration file:"
    echo "  $CONFIG_DIR/*.env (root:root, mode 0600)"
    echo
    print_status "Important environment variables to set:"
    echo "  TELEGRAM_BOT_TOKEN           - Your Telegram bot token"
    echo "  TELEGRAM_CHAT_ID             - Private destination chat ID"
    echo "  TELEGRAM_AUTHORIZED_USER_ID  - Authorized Telegram user ID"
    echo "  DISCORD_BOT_TOKEN            - Private Discord bot token"
    echo "  DISCORD_APPLICATION_ID       - Discord application ID"
    echo "  DISCORD_GUILD_ID             - Private server ID"
    echo "  DISCORD_CHANNEL_ID           - Private channel ID"
    echo "  DISCORD_AUTHORIZED_USER_ID   - Authorized Discord user ID"
    echo
    print_status "Next steps:"
    echo "  1. Install secrets privately: sudo ./scripts/configure-secrets.sh"
    echo "  2. Test the system: sudo parking-monitor test"
    echo "  3. Start the service: sudo parking-monitor start"
    echo "  4. Open Telegram and send /start to your bot"
}

# Validate environment
validate_environment() {
    print_header "Validating environment..."

    # Check for required commands
    local missing_commands=()

    for cmd in git python3 pip3 systemctl; do
        if ! command -v "$cmd" &> /dev/null; then
            missing_commands+=("$cmd")
        fi
    done

    if [ ${#missing_commands[@]} -ne 0 ]; then
        print_error "Missing required commands: ${missing_commands[*]}"
        print_status "Please install missing packages and try again"
        exit 1
    fi

    # Check if virtual environment exists
    if [ ! -x "$VENV_DIR/bin/python" ]; then
        print_warning "Virtual environment not found at $VENV_DIR"
        print_status "Make sure you've deployed the parking monitor first"
        print_status "Run setup script to create venv and install dependencies"
    else
        print_status "Virtual environment found"
    fi

    # This script deliberately does not create, overwrite, or chmod secrets.
    if ! compgen -G "$CONFIG_DIR/*.env" >/dev/null; then
        print_warning "No scoped secret files are installed in $CONFIG_DIR"
        print_status "Before starting services, run: sudo ./scripts/configure-secrets.sh"
    else
        print_status "Root-managed environment file found"
    fi

    print_status "Environment validation completed"
}

# Install dependencies
install_dependencies() {
    print_header "Installing Python dependencies..."

    if [ ! -d "$VENV_DIR" ]; then
        print_status "Creating virtual environment..."
        python3 -m venv "$VENV_DIR"
        print_status "Virtual environment created"
    fi

    # Install system dependencies for Playwright
    if ! command -v npx &> /dev/null; then
        print_status "Installing Node.js for Playwright..."
        apt-get update
        apt-get install -y nodejs npm
    fi

    # Install Python dependencies
    if [ -f "$APP_DIR/requirements.txt" ]; then
        "$VENV_DIR/bin/python" -m pip install --upgrade pip --quiet
        "$VENV_DIR/bin/python" -m pip install -r "$APP_DIR/requirements.txt" --quiet
        print_status "Python dependencies installed"
    else
        print_error "requirements.txt not found at $APP_DIR/requirements.txt"
        return 1
    fi

    # Install Playwright browsers
    print_status "Installing Playwright browsers..."
    PLAYWRIGHT_BROWSERS_PATH="$PLAYWRIGHT_BROWSERS_PATH" \
        "$VENV_DIR/bin/python" -m playwright install chromium
    "$VENV_DIR/bin/python" -m playwright install-deps chromium
    chown -R root:root "$PLAYWRIGHT_BROWSERS_PATH" "$VENV_DIR"
    chmod -R go-w "$PLAYWRIGHT_BROWSERS_PATH" "$VENV_DIR"

    print_status "Dependencies installed successfully"
}

# Main setup function
main() {
    print_header "Parking Monitor Service Setup"
    echo

    check_root
    validate_environment
    create_service_identities
    setup_permissions
    install_dependencies
    create_service_file
    create_management_command
    enable_service
    echo
    show_summary

    print_header "Setup completed successfully!"
}

install_units_only() {
    check_root
    create_service_identities
    setup_permissions
    create_service_file
    create_management_command
    enable_service
    print_header "Systemd units installed successfully"
}

# Show help
show_help() {
    echo "Parking Monitor Service Setup Script"
    echo
    echo "This script sets up the parking monitor as a systemd service with:"
    echo "  - Four isolated non-login service users"
    echo "  - Proper file permissions and security"
    echo "  - Four hardened systemd service configurations"
    echo "  - Root-owned management command copy"
    echo
    echo "Usage: sudo $0"
    echo "       sudo $0 --install-units"
    echo "       $0 --render-only OUTPUT_DIRECTORY"
    echo
    echo "After running this script, you can manage the service with:"
    echo "  sudo parking-monitor start|stop|restart|status|logs|update|monitor"
    echo
    echo "See DOCUMENTATION.md for full documentation."
}

# Parse command line arguments
case "${1:-setup}" in
    setup)
        main
        ;;
    help|--help|-h)
        show_help
        ;;
    --render-only)
        if [ "$#" -ne 2 ]; then
            print_error "--render-only requires exactly one output directory"
            exit 2
        fi
        render_service_files "$2"
        ;;
    --install-units)
        if [ "$#" -ne 1 ]; then
            print_error "--install-units does not accept arguments"
            exit 2
        fi
        install_units_only
        ;;
    *)
        print_error "Unknown command: $1"
        show_help
        exit 1
        ;;
esac
