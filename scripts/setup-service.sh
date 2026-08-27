#!/bin/bash
# Service Setup Script for Parking Monitor
# Creates user, sets permissions, creates symlink, and configures systemd services

set -euo pipefail

# Configuration
SERVICE_NAME="parking-service"
APP_DIR="/opt/parking_monitor"
VENV_DIR="${APP_DIR}/venv"
LOG_DIR="/opt/parking_monitor/logs"
APP_USER="parking_user"
SYMLINK_PATH="/usr/local/bin/parking-monitor"
CONFIG_DIR="/opt/parking_monitor/config"
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

# Detect and configure user
detect_and_configure_user() {
    print_header "Configuring application user..."

    # If APP_USER is set to a specific existing user, use that
    if [ "$APP_USER" != "parking_user" ] && id "$APP_USER" &>/dev/null; then
        print_status "Using existing user: $APP_USER"
        return
    fi

    # If parking_user already exists, use it
    if id "parking_user" &>/dev/null; then
        APP_USER="parking_user"
        print_status "Using existing parking_user"
        return
    fi

    # Try to detect the actual user who ran sudo
    if [ -n "${SUDO_USER:-}" ] && id "$SUDO_USER" &>/dev/null; then
        print_status "Detected user who ran sudo: $SUDO_USER"
        echo "Choose user configuration:"
        echo "1) Create dedicated 'parking_user' (recommended for production)"
        echo "2) Use existing user '$SUDO_USER'"
        read -p "Enter choice (1 or 2): " choice

        case $choice in
            1)
                create_parking_user
                ;;
            2)
                APP_USER="$SUDO_USER"
                print_status "Using existing user: $APP_USER"
                ;;
            *)
                print_warning "Invalid choice, creating dedicated user"
                create_parking_user
                ;;
        esac
    else
        # Default: create parking_user
        create_parking_user
    fi
}

# Create dedicated parking_user
create_parking_user() {
    print_status "Creating dedicated 'parking_user'..."

    # Create system user with home directory
    useradd --system --create-home --home-dir "/home/parking_user" --shell /bin/bash "parking_user"
    APP_USER="parking_user"
    print_status "User $APP_USER configured"

    # Add user to necessary groups
    usermod -aG systemd-journal "$APP_USER" 2>/dev/null || \
        print_warning "Could not add $APP_USER to systemd-journal"
    print_status "User permissions configured"
}

# Set up directory permissions
setup_permissions() {
    print_header "Setting up directory permissions..."

    if [ ! -d "$APP_DIR" ]; then
        print_error "Application directory $APP_DIR does not exist"
        print_status "Please deploy the parking monitor application first"
        exit 1
    fi

    # Create necessary directories
    mkdir -p "$LOG_DIR"
    mkdir -p "$CONFIG_DIR"

    # Own only runtime boundaries and their existing runtime files. Adopt the
    # Git metadata and an existing virtual environment so all future Git/pip
    # operations can run as the service account. Chown preserves executable
    # modes; never chmod virtual-environment internals.
    chown "$APP_USER:$APP_USER" "$APP_DIR" "$LOG_DIR" "$CONFIG_DIR"
    chmod 0755 "$APP_DIR" "$LOG_DIR"
    chmod 0700 "$CONFIG_DIR"

    if [ -e "$APP_DIR/.git" ]; then
        chown -R "$APP_USER:$APP_USER" "$APP_DIR/.git"
    fi
    if [ -d "$VENV_DIR" ]; then
        chown -R "$APP_USER:$APP_USER" "$VENV_DIR"
    fi

    local runtime_path
    for runtime_path in \
        "$APP_DIR/state.json" \
        "$APP_DIR/notifications.sqlite3" \
        "$APP_DIR/notifications.sqlite3-wal" \
        "$APP_DIR/notifications.sqlite3-shm"
    do
        if [ -e "$runtime_path" ]; then
            chown "$APP_USER:$APP_USER" "$runtime_path"
            chmod 0600 "$runtime_path"
        fi
    done

    local script_path
    for script_path in "$APP_DIR"/scripts/*.sh; do
        if [ -f "$script_path" ]; then
            chmod 0755 "$script_path"
        fi
    done

    # Runtime secrets live outside the application tree and are never changed here.
    # Configure git for server deployment
    if [ -d "$APP_DIR/.git" ]; then
        print_status "Configuring git for server deployment..."
        sudo -u "$APP_USER" bash -c "
            cd '$APP_DIR'
            git config core.filemode false
            git config core.autocrlf false
            git config --global --add safe.directory '$APP_DIR'
        "

        # Also add safe.directory for root user
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

    cat > "$unit_path" << EOF
[Unit]
Description=${description}
Wants=network-online.target
After=network-online.target

[Service]
Type=simple
User=${APP_USER}
Group=${APP_USER}
WorkingDirectory=${APP_DIR}
Environment=PATH=${APP_DIR}/venv/bin
Environment=PYTHONUNBUFFERED=1
EnvironmentFile=/etc/parking-monitor.env
ExecStart=${APP_DIR}/venv/bin/python ${APP_DIR}/${program}
Restart=on-failure
RestartSec=10
StandardOutput=append:${LOG_DIR}/${log_name}
StandardError=append:${LOG_DIR}/${log_name}
UMask=0077

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
ReadWritePaths=${APP_DIR}
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

# Create symlink for management script
create_symlink() {
    print_header "Creating management script symlink..."

    local script_path="$APP_DIR/scripts/manage-parking-monitor.sh"

    if [ ! -f "$script_path" ]; then
        print_error "Management script not found: $script_path"
        return 1
    fi

    # Make sure script is executable
    chmod +x "$script_path"

    # Remove existing symlink if it exists
    if [ -L "$SYMLINK_PATH" ]; then
        rm "$SYMLINK_PATH"
        print_warning "Removed existing symlink"
    fi

    # Create new symlink
    ln -s "$script_path" "$SYMLINK_PATH"
    print_status "Symlink created: $SYMLINK_PATH -> $script_path"

    # Ensure symlink is executable
    chmod +x "$SYMLINK_PATH"

    # Test symlink
    if [ -x "$SYMLINK_PATH" ]; then
        print_status "Symlink is working correctly"
        print_status "You can now use: sudo parking-monitor <command>"
    else
        print_error "Symlink test failed"
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
    print_status "Application user: $APP_USER"
    print_status "Application directory: $APP_DIR"
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
    echo "  /etc/parking-monitor.env (root:root, mode 0600)"
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
    if [ ! -f "/etc/parking-monitor.env" ]; then
        print_warning "Secrets are not installed at /etc/parking-monitor.env"
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
        sudo -u "$APP_USER" python3 -m venv "$VENV_DIR"
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
        sudo -u "$APP_USER" "$VENV_DIR/bin/python" -m pip install --upgrade pip --quiet
        sudo -u "$APP_USER" "$VENV_DIR/bin/python" -m pip install -r "$APP_DIR/requirements.txt" --quiet
        print_status "Python dependencies installed"
    else
        print_error "requirements.txt not found at $APP_DIR/requirements.txt"
        return 1
    fi

    # Install Playwright browsers
    print_status "Installing Playwright browsers..."
    sudo -u "$APP_USER" "$VENV_DIR/bin/python" -m playwright install chromium
    "$VENV_DIR/bin/python" -m playwright install-deps chromium

    print_status "Dependencies installed successfully"
}

# Main setup function
main() {
    print_header "Parking Monitor Service Setup"
    echo

    check_root
    validate_environment
    detect_and_configure_user
    setup_permissions
    install_dependencies
    create_service_file
    create_symlink
    enable_service
    echo
    show_summary

    print_header "Setup completed successfully!"
}

install_units_only() {
    check_root
    create_service_file
    create_symlink
    enable_service
    print_header "Systemd units installed successfully"
}

# Show help
show_help() {
    echo "Parking Monitor Service Setup Script"
    echo
    echo "This script sets up the parking monitor as a systemd service with:"
    echo "  - Dedicated application user (parking_user)"
    echo "  - Proper file permissions and security"
    echo "  - Four hardened systemd service configurations"
    echo "  - Management script symlink (parking-monitor command)"
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
