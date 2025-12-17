# Parking Monitor - Service Architecture

This document explains how the Parking Monitor system is structured and runs as services on Ubuntu.

## Architecture Overview

The Parking Monitor uses a **dual-service architecture** with two separate processes that communicate through a shared state file:

```
┌─────────────────────┐    ┌─────────────────┐    ┌─────────────────────┐
│                     │    │                 │    │                     │
│  monitor.py         │───▶│  state.json     │◀───│  telegram_bot.py    │
│  (Web Scraper)      │    │  (State File)   │    │  (Telegram Bot)     │
│                     │    │                 │    │                     │
│ • Scrapes website  │    │ • last_check    │    │ • Sends alerts      │
│ • Updates state     │    │ • last_enabled  │    │ • Handles commands  │
│ • Sets alert flag   │    │ • alert flag    │    │ • Reads state       │
│                     │    │ • statistics    │    │                     │
└─────────────────────┘    └─────────────────┘    └─────────────────────┘
        │                                               │
        ▼                                               ▼
┌─────────────────────┐                        ┌─────────────────────┐
│ parking-service-    │                        │ parking-service-    │
│ monitor.service     │                        │ bot.service         │
│ (Systemd Service)   │                        │ (Systemd Service)   │
└─────────────────────┘                        └─────────────────────┘
```

## Services

### 1. Monitor Service (`parking-service-monitor`)

**Purpose**: Continuously monitors the Moscow parking website for availability

**File**: `monitor.py`

**Key Functions**:
- Launches Playwright browser to scrape parking.mos.ru
- Checks if target parking spot becomes available
- Updates `state.json` with current status
- Sets alert flag when parking becomes available
- Runs in infinite loop with configurable interval

**Systemd Service**: `/etc/systemd/system/parking-service-monitor.service`

### 2. Bot Service (`parking-service-bot`)

**Purpose**: Manages Telegram bot interface and sends notifications

**File**: `telegram_bot.py`

**Key Functions**:
- Runs Telegram bot with command handlers
- Reads `state.json` for parking status
- Sends alerts when parking becomes available
- Handles user commands (/status, /stats, /interval)
- Runs background alert loop

**Systemd Service**: `/etc/systemd/system/parking-service-bot.service`

## Communication Mechanism

### State File (`state.json`)

The two services communicate through a shared JSON file:

```json
{
  "last_check": "2024-01-17T10:30:00",
  "last_enabled": false,
  "alert": true,
  "interval": 60,
  "checks": 1234,
  "hits": 5
}
```

**Key Fields**:
- `last_check`: Timestamp of last website check
- `last_enabled`: Last known parking availability status
- `alert`: Flag to trigger Telegram notification
- `interval`: Monitoring interval in seconds
- `checks`: Total number of checks performed
- `hits`: Number of times parking was available

### Data Flow

1. **Monitor Service**:
   - Scrapes parking website
   - Updates `last_check` and `last_enabled` in state file
   - Sets `alert=true` if parking becomes available

2. **Bot Service**:
   - Reads state file every 5 seconds
   - If `alert=true`, sends Telegram notification
   - Sets `alert=false` after sending notification
   - Responds to user commands by reading state file

## Service Management

### Starting Services

```bash
# Start both services
sudo parking-monitor start

# Services are started in order:
# 1. Monitor service (needs to start first)
# 2. Bot service (reads state created by monitor)
```

### Stopping Services

```bash
# Stop both services
sudo parking-monitor stop

# Both services are stopped gracefully
```

### Checking Status

```bash
# Check both services status
sudo parking-monitor status

# Shows individual status for each service:
# - Monitor Service: RUNNING/STOPPED
# - Bot Service: RUNNING/STOPPED
# - Resource usage for each
# - Uptime for each
```

### Viewing Logs

```bash
# View logs from both services
sudo parking-monitor logs

# View specific service logs
sudo parking-monitor logs -t monitor    # Monitor service only
sudo parking-monitor logs -t bot        # Bot service only

# Follow logs in real-time
sudo parking-monitor logs -f

# View log files directly
tail -f /opt/parking_monitor/logs/monitor.log
tail -f /opt/parking_monitor/logs/bot.log
```

## Log Files

Each service maintains its own log file:

- **Monitor logs**: `/opt/parking_monitor/logs/monitor.log`
  - Web scraping activity
  - Parking check results
  - Errors in browser automation

- **Bot logs**: `/opt/parking_monitor/logs/bot.log`
  - Telegram bot messages
  - Command processing
  - Alert notifications
  - User interactions

## Service Configuration

### Service Files Location

```
/etc/systemd/system/parking-service-monitor.service
/etc/systemd/system/parking-service-bot.service
```

### Key Service Settings

- **User**: `parking_user` (dedicated system user)
- **Restart**: Always restart on failure
- **Logging**: Both systemd journal and log files
- **Security**: Restricted permissions, sandboxed

## Benefits of This Architecture

### 1. **Separation of Concerns**
- Web scraping logic is separate from Telegram bot logic
- Each service can be developed, tested, and debugged independently
- Failures in one service don't directly crash the other

### 2. **Resilience**
- If monitor service fails, bot service continues running
- If bot service fails, monitor service continues checking
- Services can be restarted independently

### 3. **State Persistence**
- State file survives service restarts
- Historical data is preserved
- Services can be updated without losing state

### 4. **Scalability**
- Can easily add more services (e.g., web interface, API service)
- Each service can be scaled independently
- State file provides simple coordination mechanism

### 5. **Debugging**
- Clear separation of logs for each component
- Can monitor each service independently
- Easy to identify which component has issues

## Deployment Sequence

1. **Setup Phase**:
   ```bash
   sudo ./scripts/setup-service.sh
   ```

2. **Configuration Phase**:
   ```bash
   # Edit .env file with Telegram tokens
   sudo nano /opt/parking_monitor/.env
   ```

3. **Testing Phase**:
   ```bash
   sudo parking-monitor test
   ```

4. **Start Services**:
   ```bash
   sudo parking-monitor start
   ```

5. **Verify Running**:
   ```bash
   sudo parking-monitor status
   sudo parking-monitor monitor
   ```

## Common Patterns

### Service Dependencies

Monitor service must start before bot service because:
- Bot service expects state file to exist
- Initial state is created by monitor service
- Bot service reads state created by monitor

### Error Handling

- Monitor service: Scrapping errors are logged, service continues
- Bot service: Telegram errors are logged, service continues
- Both services: Automatic restart on crash

### Resource Usage

- Monitor service: Higher CPU during browser operations
- Bot service: Minimal CPU, mostly idle
- Both services: Low memory footprint

## Troubleshooting

### Service Won't Start

1. Check if both services exist:
   ```bash
   systemctl list-unit-files | grep parking-service
   ```

2. Check individual service status:
   ```bash
   systemctl status parking-service-monitor
   systemctl status parking-service-bot
   ```

3. Check logs for errors:
   ```bash
   journalctl -u parking-service-monitor -n 50
   journalctl -u parking-service-bot -n 50
   ```

### No Alerts Being Sent

1. Check if monitor service is running:
   ```bash
   sudo parking-monitor status
   ```

2. Check state file:
   ```bash
   cat /opt/parking_monitor/state.json
   ```

3. Check if bot service is reading alerts:
   ```bash
   sudo parking-monitor logs -t bot
   ```

### High CPU Usage

1. Check which service is using CPU:
   ```bash
   sudo parking-monitor monitor resources
   ```

2. Monitor service might be stuck in browser operations
3. Consider increasing monitoring interval in state file

## Migration from Single Service

If you're migrating from a single-service setup:

1. **Backup current state**:
   ```bash
   cp /opt/parking_monitor/state.json /tmp/state_backup.json
   ```

2. **Run updated setup script**:
   ```bash
   sudo ./scripts/setup-service.sh
   ```

3. **Restore state**:
   ```bash
   cp /tmp/state_backup.json /opt/parking_monitor/state.json
   ```

4. **Start new dual services**:
   ```bash
   sudo parking-monitor start
   ```