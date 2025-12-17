# 🅿️ Parking Monitor

[![Python](https://img.shields.io/badge/python-3.7%2B-blue.svg)](https://python.org)
[![License](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)
[![System](https://img.shields.io/badge/system-Linux%20%7C%20Windows-lightgrey.svg)]()
[![Status](https://img.shields.io/badge/status-Production%20Ready-brightgreen.svg)]()

An automated monitoring system for Moscow parking availability that sends real-time Telegram notifications when parking spots become available.

## 🚀 Features

- **Automated Monitoring**: Continuously checks Moscow parking website availability
- **Real-time Alerts**: Instant Telegram notifications when parking becomes available
- **Interactive Bot**: User-friendly Telegram bot with buttons and commands
- **Service Architecture**: Dual-service design for reliability
- **Smart Time Display**: Human-friendly date/time formatting
- **Statistics Tracking**: Monitor success rates and uptime
- **Production Ready**: Systemd service configuration included

## 🏗️ Architecture

The system uses a dual-service architecture with state-based communication:

```
┌─────────────────────┐    ┌─────────────────┐    ┌─────────────────────┐
│                     │    │                 │    │                     │
│  monitor.py         │───▶│  state.json     │◀───│  telegram_bot.py   │
│  (Web Scraper)      │    │  (State File)   │    │  (Telegram Bot)     │
│                     │    │                 │    │                     │
│ • Playwright        │    │ • State         │    │ • Commands/Buttons  │
│ • Status Updates    │    │ • Statistics    │    │ • Notifications     │
│ • Alert Signals     │    │ • Configuration │    │ • User Interface    │
└─────────────────────┘    └─────────────────┘    └─────────────────────┘
        │                                               │
        ▼                                               ▼
┌─────────────────────┐                        ┌─────────────────────┐
│ parking-service-    │                        │ parking-service-    │
│ monitor.service     │                        │ bot.service         │
│ (Systemd Service)   │                        │ (Systemd Service)   │
└─────────────────────┘                        └─────────────────────┘
```

## 📋 Prerequisites

- **Python 3.7+**
- **Playwright** (for web automation)
- **Node.js** (required by Playwright)
- **systemd** (for service management on Linux)
- **Python 3.7+**: Runtime environment
- **python-telegram-bot**: Telegram Bot API wrapper
- **Playwright**: Web automation framework

## 🛠️ Installation

### 1. Clone Repository

```bash
git clone https://github.com/yourusername/parking_monitor.git
cd parking_monitor
```

### 2. Set Up Virtual Environment

```bash
python3 -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate
```

### 3. Install Dependencies

```bash
pip install -r requirements.txt
python -m playwright install chromium
python -m playwright install-deps chromium
```

### 4. Configuration

Copy the environment template:
```bash
cp config.py.example config.py
```

Edit `config.py` with your settings:
```python
# Telegram Bot Configuration
TELEGRAM_BOT_TOKEN = "YOUR_BOT_TOKEN_HERE"
TELEGRAM_CHAT_ID = "YOUR_CHAT_ID_HERE"

# Monitoring Configuration
CHECK_INTERVAL_SECONDS = 60
TARGET_REGION_TEXT = "Западный административный округ"
TARGET_ADDRESS_TEXT = "улица Поклонная, дом 11А"
```

### 5. Test the System

```bash
# Test monitor service
python monitor.py

# Test Telegram bot (in separate terminal)
python telegram_bot.py
```

## 🚀 Deployment

### Ubuntu Server Setup

For production deployment on Ubuntu/Debian:

1. **Copy files to server**:
   ```bash
   scp -r parking_monitor/ user@server:/opt/parking_monitor/
   ```

2. **Run setup script**:
   ```bash
   cd /opt/parking_monitor
   sudo ./scripts/setup-service.sh
   ```

3. **Configure environment**:
   ```bash
   sudo nano .env
   # Add your Telegram tokens
   ```

4. **Start services**:
   ```bash
   sudo parking-monitor start
   ```

### Service Management

```bash
# Start services
sudo parking-monitor start

# Stop services
sudo parking-monitor stop

# Check status
sudo parking-monitor status

# View logs
sudo parking-monitor logs

# Follow logs in real-time
sudo parking-monitor logs -f

# Update application
sudo parking-monitor update

# Monitor system health
sudo parking-monitor monitor
```

## 🤖 Telegram Bot Features

### Commands
- `/start` - Initialize bot and show menu
- `/status` - Show current parking status
- `/stats` - Display monitoring statistics
- `/interval <seconds>` - Set check interval

### Interactive Buttons
- **📊 Status** - View current parking availability
- **📈 Statistics** - Check success rates and uptime
- **⚙️ Set Interval** - Configure monitoring frequency
- **⚡ Quick Intervals** - Preset interval options
- **🔄 Refresh** - Update current status

### Quick Interval Presets
- 1 min, 2 min, 5 min, 10 min, 15 min, 30 min

## 📊 Monitoring Features

### Health Checks
```bash
sudo parking-monitor monitor health
```

### Log Analysis
```bash
sudo parking-monitor monitor logs 100  # Analyze last 100 lines
```

### Resource Monitoring
```bash
sudo parking-monitor monitor resources
```

### Continuous Monitoring
```bash
sudo parking-monitor monitor continuous  # Real-time dashboard
```

## 📁 Project Structure

```
parking_monitor/
├── config.py                 # Configuration settings
├── monitor.py                # Web scraping service
├── telegram_bot.py           # Telegram bot service
├── requirements.txt           # Python dependencies
├── .env.template            # Environment template
├── state.json               # Current state (auto-created)
├── scripts/                 # Management scripts
│   ├── setup-service.sh      # Service installation
│   ├── manage-parking-monitor.sh  # Service management
│   └── monitor.sh            # Health monitoring
├── logs/                    # Log files (auto-created)
├── DOCUMENTATION.md         # Technical documentation
├── SERVICE_ARCHITECTURE.md # Architecture guide
└── DEPLOYMENT.md           # Deployment guide
```

## ⚙️ Configuration

### Environment Variables

| Variable | Description | Required |
|----------|-------------|----------|
| `TELEGRAM_BOT_TOKEN` | Telegram bot authentication token | Yes |
| `TELEGRAM_CHAT_ID` | Target chat for notifications | Yes |
| `CHECK_INTERVAL_SECONDS` | Default check interval (seconds) | No |
| `TARGET_REGION_TEXT` | Moscow parking region | No |
| `TARGET_ADDRESS_TEXT` | Specific parking address | No |

### State File

The system maintains state in `state.json`:
```json
{
  "checks": 1234,
  "hits": 56,
  "last_enabled": false,
  "alert": false,
  "last_check": "2024-01-17T14:30:00",
  "interval": 60
}
```

## 🔧 Development

### Running Locally

```bash
# Terminal 1: Start monitor
python monitor.py

# Terminal 2: Start bot
python telegram_bot.py
```

### Testing

```bash
# Test Playwright installation
python -c "from playwright.sync_api import sync_playwright; print('OK')"

# Test Telegram connection
python -c "import telegram; print('OK')"
```

### Adding Features

1. Modify `monitor.py` for new scraping logic
2. Update `telegram_bot.py` for new bot features
3. Update `config.py` for new configuration options
4. Test both services independently

## 📈 Monitoring Statistics

The bot tracks:
- **Total Checks**: Number of parking availability checks performed
- **Successful Alerts**: Times parking became available
- **Success Rate**: Percentage of successful alerts
- **Monitoring Uptime**: Total time system has been running
- **Check Interval**: Current monitoring frequency

## 🚨 Alerts

When parking becomes available, the bot sends:
- **Immediate notification**: "🚨 PARKING AVAILABLE!"
- **Action buttons**: Quick access to status and statistics
- **Context**: Current monitoring information

## 🔧 Troubleshooting

### Common Issues

1. **Services won't start**:
   ```bash
   sudo parking-monitor test
   ```

2. **Playwright errors**:
   ```bash
   python -m playwright install-deps chromium
   ```

3. **Telegram bot not responding**:
   - Check bot token in config.py
   - Verify chat ID is correct
   - Check bot logs: `sudo parking-monitor logs -t bot`

4. **No monitoring activity**:
   ```bash
   sudo parking-monitor logs -t monitor
   ```

### Log Analysis

```bash
# Check for errors
sudo parking-monitor logs -t error

# Analyze recent activity
sudo parking-monitor monitor logs 100
```

## 📚 Documentation

- [Technical Documentation](DOCUMENTATION.md)
- [Service Architecture](SERVICE_ARCHITECTURE.md)
- [Deployment Guide](DEPLOYMENT.md)

## 🤝 Contributing

1. Fork the repository
2. Create a feature branch
3. Make your changes
4. Test thoroughly
5. Submit a pull request

## 📄 License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.

## 🐛 Bug Reports

Please report bugs via:
- [GitHub Issues](https://github.com/yourusername/parking_monitor/issues)
- Include system information and error logs

## 📧 Support

For questions or support:
- Create an issue on GitHub
- Check the documentation first
- Include system logs with bug reports

---

**⭐ If you find this useful, please give it a star on GitHub!**