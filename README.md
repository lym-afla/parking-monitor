# Parking Monitor

Parking Monitor checks the configured Moscow parking subscription page on an
adaptive schedule and queues a durable event when availability changes from
unavailable to available. Telegram and Discord delivery and commands operate
independently.

## Runtime components

- `monitor.py` scrapes the site and records check state/events.
- `notifier.py` claims per-channel SQLite deliveries and sends Telegram or
  Discord alerts independently.
- `telegram_bot.py` serves the private Telegram command interface.
- `discord_bot.py` serves private guild-scoped Discord slash commands and
  persistent alert controls.
- `command_service.py` provides shared status, statistics, and interval logic.
- `state_store.py` serializes cross-process `state.json` mutations.
- `notification_store.py` owns durable events, claims, retries, channel health,
  and failed-delivery recovery.

The approved authorization IDs are unchanged. Tokens exist only in scoped
root-owned environment files installed interactively; no token belongs in the
repository or logs.

## Production boundary

Trusted code and the venv are root-owned under `/opt/parking_monitor`. Four
distinct non-login users run the monitor, notifier, Telegram bot, and Discord
bot. They share the `parking-monitor` group only for runtime coordination.

- state/SQLite: `/var/lib/parking-monitor/data`;
- read-only Playwright Chromium: `/var/lib/parking-monitor/ms-playwright`;
- logs: `/var/log/parking-monitor`;
- scoped configuration: `/etc/parking-monitor/*.env`;
- root-owned management copy: `/usr/local/bin/parking-monitor`.

The monitor receives no credentials. Each command bot receives only its own
channel file. The notifier receives two delivery-only files. A missing file
disables only that channel and is reported as `disabled`.

## Development verification

```powershell
& 'D:\Project Y\Parking monitor\.venv\Scripts\python.exe' -m unittest discover -v
& 'D:\Project Y\Parking monitor\.venv\Scripts\python.exe' -m py_compile config.py state_store.py command_service.py notification_store.py notifier.py telegram_bot.py discord_bot.py monitor.py
& 'C:\Program Files\Git\bin\bash.exe' -n scripts/configure-secrets.sh scripts/setup-service.sh scripts/manage-parking-monitor.sh scripts/monitor.sh
git diff --check
```

Tests use synthetic credentials and temporary SQLite/state files. Do not use a
production token in a local test.

## Production operations

```bash
cd /opt/parking_monitor
sudo ./scripts/setup-service.sh
sudo ./scripts/configure-secrets.sh
sudo parking-monitor start
sudo parking-monitor status
sudo parking-monitor logs -n 100
sudo parking-monitor update
```

The update command stages and verifies the target revision before stopping
services. It uses `git pull --ff-only`, a SQLite backup, and coherent rollback
of revision, venv/browser environment, units, state, and database.

See [DEPLOYMENT.md](DEPLOYMENT.md) for installation, verification, recovery,
and Linux-only terminal checks.
