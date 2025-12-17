# Parking Monitor - Ubuntu Server Deployment Guide

This guide explains how to deploy and run the Parking Monitor as a service on an Ubuntu virtual server.

## Table of Contents

1. [Prerequisites](#prerequisites)
2. [Server Setup](#server-setup)
3. [Application Deployment](#application-deployment)
4. [Service Installation](#service-installation)
5. [Configuration](#configuration)
6. [Service Management](#service-management)
7. [Monitoring](#monitoring)
8. [Troubleshooting](#troubleshooting)
9. [Maintenance](#maintenance)

## Prerequisites

### Server Requirements

- **OS**: Ubuntu 20.04 LTS or later
- **RAM**: Minimum 1GB, recommended 2GB
- **Storage**: Minimum 10GB free space
- **Network**: Stable internet connection
- **Permissions**: sudo/root access

### Required Software

The setup script will automatically install these dependencies:
- Python 3.8+
- pip
- Node.js (for Playwright)
- Git
- systemd (for service management)

## Server Setup

### 1. Update System

```bash
sudo apt update && sudo apt upgrade -y
```

### 2. Create Application Directory

```bash
sudo mkdir -p /opt/parking_monitor
sudo mkdir -p /opt/parking_monitor/logs
sudo mkdir -p /opt/parking_monitor/config
```

### 3. Install Basic Dependencies

```bash
sudo apt install -y python3 python3-pip python3-venv git curl wget
```

## Application Deployment

### Option A: Deploy from Git Repository

1. **Clone the repository:**

```bash
cd /opt
sudo git clone <your-github-repo-url> parking_monitor
```

2. **Set ownership:**

```bash
sudo chown -R $USER:$USER /opt/parking_monitor
```

### Option B: Deploy from Local Files

1. **Upload files to server:**

```bash
# Using scp
scp -r /path/to/parking_monitor user@server:/tmp/

# Or using rsync
rsync -avz /path/to/parking_monitor/ user@server:/opt/parking_monitor/
```

2. **Set ownership:**

```bash
sudo chown -R $USER:$USER /opt/parking_monitor
```

## Service Installation

### 1. Make Scripts Executable

```bash
cd /opt/parking_monitor
chmod +x scripts/*.sh
```

### 2. Run the Setup Script

```bash
sudo ./scripts/setup-service.sh
```

The setup script will:
- Create a dedicated system user (`parking_user`)
- Install Python dependencies
- Install Playwright and browsers
- Create systemd service file
- Set up management script symlink
- Configure proper permissions

### 3. Verify Installation

```bash
sudo parking-monitor test
```

## Configuration

### 1. Create Environment File

```bash
# Copy template if exists
cp .env.template .env

# Or create from scratch
nano .env
```

### 2. Configure Required Variables

```bash
# Telegram Bot Configuration
TELEGRAM_BOT_TOKEN=your_bot_token_here
TELEGRAM_AUTHORIZED_USER_IDS=user_id1,user_id2
TELEGRAM_ADMIN_USER_ID=admin_user_id

# Monitoring Configuration
MONITORING_INTERVAL=60          # Check every 60 seconds
LOG_LEVEL=INFO                  # DEBUG, INFO, WARNING, ERROR

# Optional Configuration
TIMEZONE=Europe/Moscow
MAX_RETRIES=3
RETRY_DELAY=10
```

### 3. Get Telegram Bot Token

1. Talk to [@BotFather](https://t.me/botfather) on Telegram
2. Create a new bot: `/newbot`
3. Copy the bot token

### 4. Get Your Telegram User ID

1. Talk to [@userinfobot](https://t.me/userinfobot) on Telegram
2. Copy your numeric user ID

## Service Management

### Using the Management Script

All commands use the `parking-monitor` symlink created during setup:

```bash
# Start the service
sudo parking-monitor start

# Stop the service
sudo parking-monitor stop

# Restart the service
sudo parking-monitor restart

# Check status
sudo parking-monitor status

# View logs
sudo parking-monitor logs

# Follow logs in real-time
sudo parking-monitor logs -f

# View Python application logs
sudo parking-monitor logs -t python

# View error logs only
sudo parking-monitor logs -t error
```

### Using systemctl Directly

```bash
# Enable service to start on boot
sudo systemctl enable parking-service

# Start service
sudo systemctl start parking-service

# Check status
sudo systemctl status parking-service

# View logs
sudo journalctl -u parking-service -f
```

## Monitoring

### Health Checks

```bash
# Quick health check
sudo parking-monitor monitor

# Continuous monitoring (refreshes every 10s)
sudo parking-monitor monitor continuous

# Resource usage
sudo parking-monitor monitor resources

# Log analysis
sudo parking-monitor monitor logs 200

# Check parking status
sudo parking-monitor monitor parking
```

### Log Files

- **Service logs**: `/opt/parking_monitor/logs/parking.log`
- **Systemd logs**: `journalctl -u parking-service`
- **State file**: `/opt/parking_monitor/state.json`

### Important Log Locations

```bash
# Application logs
tail -f /opt/parking_monitor/logs/parking.log

# System service logs
sudo journalctl -u parking-service -f

# Error logs only
sudo journalctl -u parking-service -p err -f
```

## Troubleshooting

### Common Issues

#### 1. Service Won't Start

```bash
# Check service status
sudo parking-monitor status

# Check logs for errors
sudo parking-monitor logs -t error

# Test configuration
sudo parking-monitor test
```

#### 2. Playwright Issues

```bash
# Reinstall Playwright
cd /opt/parking_monitor
source venv/bin/activate
python -m playwright install chromium
python -m playwright install-deps chromium
```

#### 3. Permission Issues

```bash
# Fix ownership
sudo chown -R parking_user:parking_user /opt/parking_monitor

# Fix permissions
sudo chmod +x /opt/parking_monitor/scripts/*.sh
sudo chmod +x /opt/parking_monitor/*.py
```

#### 4. Network Issues

```bash
# Test connectivity to parking website
ping parking.mos.ru

# Test with curl
curl -I https://parking.mos.ru

# Check firewall rules
sudo ufw status
```

#### 5. Telegram Bot Issues

```bash
# Test bot token
curl https://api.telegram.org/bot<YOUR_BOT_TOKEN>/getMe

# Check bot is running and responsive
curl https://api.telegram.org/bot<YOUR_BOT_TOKEN>/getUpdates
```

### Error Codes

- **Exit Code 1**: General error (check logs)
- **Exit Code 2**: Configuration error (missing .env or invalid tokens)
- **Exit Code 3**: Network error (cannot reach parking website)
- **Exit Code 4**: Authentication error (invalid Telegram token)

## Maintenance

### Updating the Application

```bash
# Update from git repository
sudo parking-monitor update

# Or manually:
cd /opt/parking_monitor
sudo -u parking_user git pull
source venv/bin/activate
pip install -r requirements.txt --upgrade
sudo systemctl restart parking-service
```

### Log Rotation

Create log rotation configuration:

```bash
sudo nano /etc/logrotate.d/parking-monitor
```

Content:
```
/opt/parking_monitor/logs/*.log {
    daily
    missingok
    rotate 7
    compress
    delaycompress
    notifempty
    create 644 parking_user parking_user
    postrotate
        systemctl reload parking-service || true
    endscript
}
```

### Backup Configuration

Backup important files:

```bash
# Create backup directory
sudo mkdir -p /opt/backups/parking_monitor

# Backup configuration and state
sudo cp /opt/parking_monitor/.env /opt/backups/parking_monitor/
sudo cp /opt/parking_monitor/state.json /opt/backups/parking_monitor/ 2>/dev/null || true

# Create backup script
cat > /opt/parking_monitor/scripts/backup.sh << 'EOF'
#!/bin/bash
BACKUP_DIR="/opt/backups/parking_monitor/$(date +%Y%m%d_%H%M%S)"
mkdir -p "$BACKUP_DIR"
cp /opt/parking_monitor/.env "$BACKUP_DIR/"
cp /opt/parking_monitor/state.json "$BACKUP_DIR/" 2>/dev/null || true
tar -czf "$BACKUP_DIR.tar.gz" -C "$BACKUP_DIR" .
rm -rf "$BACKUP_DIR"
EOF
chmod +x /opt/parking_monitor/scripts/backup.sh
```

### Scheduled Tasks

Add cron job for daily backup:

```bash
sudo crontab -e
```

Add line:
```
0 2 * * * /opt/parking_monitor/scripts/backup.sh
```

### Performance Optimization

1. **Monitor Resource Usage**:
```bash
sudo parking-monitor monitor resources
```

2. **Adjust Monitoring Interval**:
Edit `.env` file:
```bash
MONITORING_INTERVAL=120  # Increase to check every 2 minutes
```

3. **Log Level Adjustment**:
For production, use INFO or WARNING level:
```bash
LOG_LEVEL=WARNING
```

## Security Considerations

### 1. Secure Configuration File

```bash
# Restrict .env file permissions
sudo chmod 600 /opt/parking_monitor/.env
sudo chown parking_user:parking_user /opt/parking_monitor/.env
```

### 2. Firewall Configuration

```bash
# Allow only necessary ports
sudo ufw allow ssh
sudo ufw enable
```

### 3. System User Security

The setup creates a system user with limited permissions:
- No shell access by default
- Limited file system access
- Cannot gain additional privileges

### 4. Update Regularly

```bash
# Update system packages
sudo apt update && sudo apt upgrade -y

# Update Python dependencies
sudo parking-monitor update
```

## Production Deployment Checklist

- [ ] Server resources meet requirements
- [ ] System packages updated
- [ ] Application deployed to `/opt/parking_monitor`
- [ ] Setup script executed successfully
- [ ] Environment file configured with proper values
- [ ] Telegram bot token valid and authorized
- [ ] Service starts and runs without errors
- [ ] Monitoring script shows healthy status
- [ ] Log rotation configured
- [ ] Backup procedures in place
- [ ] Firewall configured
- [ ] Service enabled for automatic start
- [ ] Documentation available for team

## Support

For issues and questions:

1. Check the troubleshooting section above
2. Review application logs: `sudo parking-monitor logs`
3. Run health check: `sudo parking-monitor monitor`
4. Test configuration: `sudo parking-monitor test`

## Recovery Procedures

### Full Service Recovery

```bash
# 1. Stop service if running
sudo parking-monitor stop

# 2. Backup current state
sudo cp /opt/parking_monitor/state.json /tmp/state_backup.json 2>/dev/null || true

# 3. Reinstall dependencies
cd /opt/parking_monitor
source venv/bin/activate
pip install -r requirements.txt --force-reinstall

# 4. Reinstall Playwright
python -m playwright install chromium --force

# 5. Restart service
sudo parking-monitor start

# 6. Verify
sudo parking-monitor test
```

### Configuration Reset

```bash
# Reset to template
cd /opt/parking_monitor
sudo cp .env.template .env
sudo nano .env  # Reconfigure
sudo parking-monitor restart
```