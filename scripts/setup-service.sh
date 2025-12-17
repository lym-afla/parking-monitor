#!/bin/bash
# Service Setup Script for Parking Monitor
# Creates user, sets permissions, creates symlink, and configures systemd service

# Configuration
SERVICE_NAME="parking-service"
APP_DIR="/opt/parking_monitor"
LOG_DIR="/opt/parking_monitor/logs"
APP_USER="parking_user"
SYMLINK_PATH="/usr/local/bin/parking-monitor"
CONFIG_DIR="/opt/parking_monitor/config"

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
    if [ -n "$SUDO_USER" ] && id "$SUDO_USER" &>/dev/null; then
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
    useradd --system --create-home --home-dir "/home/parking_user" --shell /bin/bash "parking_user" 2>/dev/null || {
        print_warning "User parking_user already exists"
    }
    APP_USER="parking_user"
    print_status "User $APP_USER configured"

    # Add user to necessary groups
    usermod -aG systemd-journal "$APP_USER" 2>/dev/null || true
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

    # Change ownership to app user
    chown -R "$APP_USER:$APP_USER" "$APP_DIR"
    chown -R "$APP_USER:$APP_USER" "$LOG_DIR"
    chown -R "$APP_USER:$APP_USER" "$CONFIG_DIR"
    print_status "Directory ownership set to $APP_USER"

    # Set proper permissions
    find "$APP_DIR" -type d -exec chmod 755 {} \; 2>/dev/null || true
    find "$APP_DIR" -type f -exec chmod 644 {} \; 2>/dev/null || true

    # Make scripts executable
    chmod +x "$APP_DIR/scripts/"*.sh 2>/dev/null || true
    chmod +x "$APP_DIR/"*.py 2>/dev/null || true

    # Ensure management script is executable
    chmod +x "$APP_DIR/scripts/manage-parking-monitor.sh"

    # Secure sensitive files
    chmod 600 "$APP_DIR/.env" 2>/dev/null || true
    chmod 700 "$CONFIG_DIR" 2>/dev/null || true

    # Configure git for server deployment
    if [ -d "$APP_DIR/.git" ]; then
        print_status "Configuring git for server deployment..."
        sudo -u "$APP_USER" bash -c "
            cd '$APP_DIR'
            git config core.filemode false
            git config core.autocrlf false
            git config --global --add safe.directory '$APP_DIR'
            git checkout -- . 2>/dev/null || true
        "

        # Also add safe.directory for root user
        git config --global --add safe.directory "$APP_DIR" 2>/dev/null || true

        print_status "Git configuration completed"
    fi

    print_status "File permissions configured"
}

# Create systemd service files
create_service_file() {
    print_header "Creating systemd service files..."

    # Create Monitor Service
    cat > "/etc/systemd/system/${SERVICE_NAME}-monitor.service" << EOF
[Unit]
Description=Parking Monitor - Web Scraper
After=network.target
PartOf=${SERVICE_NAME}.service

[Service]
Type=simple
User=${APP_USER}
Group=${APP_USER}
WorkingDirectory=${APP_DIR}
Environment=PATH=${APP_DIR}/venv/bin
ExecStart=${APP_DIR}/venv/bin/python ${APP_DIR}/monitor.py
EnvironmentFile=${APP_DIR}/.env
Restart=on-failure
RestartSec=10
StandardOutput=append:${LOG_DIR}/monitor.log
StandardError=append:${LOG_DIR}/monitor.log

# Security settings
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ProtectHome=true
ReadWritePaths=${APP_DIR}
ReadWritePaths=${LOG_DIR}
ReadWritePaths=${CONFIG_DIR}
PrivateDevices=true
ProtectKernelTunables=true
ProtectKernelModules=true
ProtectControlGroups=true

[Install]
WantedBy=multi-user.target
EOF

    # Create Telegram Bot Service
    cat > "/etc/systemd/system/${SERVICE_NAME}-bot.service" << EOF
[Unit]
Description=Parking Monitor - Telegram Bot
After=network.target
PartOf=${SERVICE_NAME}.service

[Service]
Type=simple
User=${APP_USER}
Group=${APP_USER}
WorkingDirectory=${APP_DIR}
Environment=PATH=${APP_DIR}/venv/bin
ExecStart=${APP_DIR}/venv/bin/python ${APP_DIR}/telegram_bot.py
EnvironmentFile=${APP_DIR}/.env
Restart=on-failure
RestartSec=10
StandardOutput=append:${LOG_DIR}/bot.log
StandardError=append:${LOG_DIR}/bot.log

# Security settings
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ProtectHome=true
ReadWritePaths=${APP_DIR}
ReadWritePaths=${LOG_DIR}
ReadWritePaths=${CONFIG_DIR}
PrivateDevices=true
ProtectKernelTunables=true
ProtectKernelModules=true
ProtectControlGroups=true

[Install]
WantedBy=multi-user.target
EOF

    # Create Target Service for managing both
    cat > "/etc/systemd/system/${SERVICE_NAME}.service" << EOF
[Unit]
Description=Parking Monitor - Complete System
After=network.target

[Service]
Type=oneshot
RemainAfterExit=yes
ExecStart=/bin/echo "Parking monitor target service started"

[Install]
WantedBy=multi-user.target
EOF

    print_status "Service files created:"
    print_status "  - ${SERVICE_NAME}-monitor.service (web scraper)"
    print_status "  - ${SERVICE_NAME}-bot.service (telegram bot)"
    print_status "  - ${SERVICE_NAME}.service (target for management)"

    # Reload systemd
    systemctl daemon-reload
    print_status "Systemd configuration reloaded"
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

    # Enable both services (start on boot)
    systemctl enable "$SERVICE_NAME-monitor"
    systemctl enable "$SERVICE_NAME-bot"
    print_status "Both services enabled for automatic startup"

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
    echo "  - Bot Service: Manages Telegram bot and sends alerts"
    echo "  - State File: Communication between services (state.json)"
    echo
    print_status "Available commands:"
    echo "  sudo parking-monitor start     # Start both services"
    echo "  sudo parking-monitor stop      # Stop both services"
    echo "  sudo parking-monitor restart   # Restart both services"
    echo "  sudo parking-monitor status    # Show both services status"
    echo "  sudo parking-monitor logs      # Show logs from both services"
    echo "  sudo parking-monitor logs -f   # Follow logs in real-time"
    echo "  sudo parking-monitor update    # Update from git and restart"
    echo "  sudo parking-monitor test      # Test system connections"
    echo "  sudo parking-monitor monitor   # Monitor service health"
    echo
    print_status "Service status:"
    echo "  Monitor: $(systemctl is-enabled "$SERVICE_NAME-monitor" --quiet && echo "Enabled" || echo "Disabled") | $(systemctl is-active "$SERVICE_NAME-monitor" --quiet && echo "Running" || echo "Stopped")"
    echo "  Bot: $(systemctl is-enabled "$SERVICE_NAME-bot" --quiet && echo "Enabled" || echo "Disabled") | $(systemctl is-active "$SERVICE_NAME-bot" --quiet && echo "Running" || echo "Stopped")"
    echo
    print_status "Configuration file:"
    echo "  ${APP_DIR}/.env"
    echo
    print_status "Important environment variables to set:"
    echo "  TELEGRAM_BOT_TOKEN           - Your Telegram bot token"
    echo "  TELEGRAM_AUTHORIZED_USER_IDS - Comma-separated user IDs"
    echo "  TELEGRAM_ADMIN_USER_ID       - Admin user ID for alerts"
    echo "  MONITORING_INTERVAL          - Monitoring check interval (default: 60)"
    echo "  LOG_LEVEL                   - Logging level (DEBUG, INFO, WARNING, ERROR)"
    echo
    print_status "Next steps:"
    echo "  1. Configure environment: sudo nano ${APP_DIR}/.env"
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
    if [ ! -f "$APP_DIR/venv/bin/python" ]; then
        print_warning "Virtual environment not found at $APP_DIR/venv"
        print_status "Make sure you've deployed the parking monitor first"
        print_status "Run setup script to create venv and install dependencies"
    else
        print_status "Virtual environment found"
    fi

    # Check if .env file exists
    if [ ! -f "$APP_DIR/.env" ]; then
        print_warning ".env file not found at $APP_DIR/.env"
        print_status "You'll need to create it from the template"
        if [ -f "$APP_DIR/.env.template" ]; then
            print_status "Template available at: $APP_DIR/.env.template"
        fi
    else
        print_status "Environment file found"
    fi

    print_status "Environment validation completed"
}

# Install dependencies
install_dependencies() {
    print_header "Installing Python dependencies..."

    if [ ! -d "$APP_DIR/venv" ]; then
        print_status "Creating virtual environment..."
        python3 -m venv "$APP_DIR/venv"
        print_status "Virtual environment created"
    fi

    source "$APP_DIR/venv/bin/activate"

    # Install system dependencies for Playwright
    if ! command -v npx &> /dev/null; then
        print_status "Installing Node.js for Playwright..."
        apt-get update
        apt-get install -y nodejs npm
    fi

    # Install Python dependencies
    if [ -f "$APP_DIR/requirements.txt" ]; then
        pip install --upgrade pip --quiet
        pip install -r "$APP_DIR/requirements.txt" --quiet
        print_status "Python dependencies installed"
    else
        print_warning "requirements.txt not found, installing minimal dependencies..."
        pip install --upgrade pip --quiet
        pip install python-telegram-bot playwright --quiet
    fi

    # Install Playwright browsers
    print_status "Installing Playwright browsers..."
    python -m playwright install chromium
    python -m playwright install-deps chromium

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

# Show help
show_help() {
    echo "Parking Monitor Service Setup Script"
    echo
    echo "This script sets up the parking monitor as a systemd service with:"
    echo "  - Dedicated application user (parking_user)"
    echo "  - Proper file permissions and security"
    echo "  - Systemd service configuration"
    echo "  - Management script symlink (parking-monitor command)"
    echo
    echo "Usage: sudo $0"
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
    *)
        print_error "Unknown command: $1"
        show_help
        exit 1
        ;;
esac