# Parking Monitor

Parking Monitor checks the Moscow parking subscription page, stores availability
transitions as durable events, and delivers each event independently through
private Telegram and Discord bots.

## Runtime architecture

Four systemd services run from `/opt/parking_monitor`:

| Service | Program | Responsibility |
|---|---|---|
| `parking-service-monitor` | `monitor.py` | Check parking and create durable transition events |
| `parking-service-notifier` | `notifier.py` | Deliver each event independently to Telegram and Discord |
| `parking-service-bot` | `telegram_bot.py` | Serve the private Telegram command interface |
| `parking-service-discord` | `discord_bot.py` | Serve private, guild-scoped Discord commands and buttons |

Monitoring state remains in `state.json`. Notification events, per-channel
attempts, and delivery results are stored in `notifications.sqlite3`. A
successful delivery on one channel is not retried because the other channel is
unavailable.

## Security model

All four services load `/etc/parking-monitor.env`. The file is installed as
`root:root` with mode `0600`; systemd reads it before starting a service as the
unprivileged `parking_user`. The setup script renders service units but never
creates or overwrites this secret file.

`ProtectSystem=strict` keeps the host filesystem read-only except for the
existing `/opt/parking_monitor` application boundary. All four services need
that exception today: the command bots initialize/read SQLite delivery health,
and `/interval` uses an atomic sibling-file replacement for `state.json`. A
future migration of mutable state to `/var/lib/parking-monitor` would allow the
units to narrow this writable boundary further.

The Telegram and Discord bot tokens are secrets. Never paste them into chat,
shell history, source files, issue reports, or logs. Discord application, guild,
channel, and authorized-user IDs are identifiers, not credentials. This project
uses a Discord bot token and Gateway connection; it does not use Discord
webhooks.

Access is enforced in the applications as well as in each platform:

- Telegram requires the configured private chat ID and authorized user ID.
- Discord requires the configured application, guild, channel, and user IDs.
- Discord slash commands are registered only in the configured private guild.
- Neither bot requests Discord privileged Gateway intents.

## Local development

Create a virtual environment and install dependencies:

```bash
python3 -m venv venv
venv/bin/pip install -r requirements.txt
venv/bin/python -m playwright install chromium
```

Set the eight runtime variables only in the process environment when developing
locally. Production values belong only in `/etc/parking-monitor.env`.

Run the test suite:

```bash
venv/bin/python -m unittest discover -v
venv/bin/python -m py_compile config.py command_service.py notification_store.py notifier.py telegram_bot.py discord_bot.py monitor.py
```

Service units can be rendered without root for inspection or tests:

```bash
./scripts/setup-service.sh --render-only /tmp/parking-monitor-units
```

## Production installation

From the deployed checkout:

```bash
cd /opt/parking_monitor
sudo ./scripts/setup-service.sh
sudo ./scripts/configure-secrets.sh
sudo parking-monitor start
```

Full setup adopts the checkout's `.git` metadata and an existing `venv`
recursively for `parking_user`; it changes ownership only and never chmods
virtual-environment internals, so executable entry points remain intact. After
full setup, run Git and Python package operations as `parking_user`. Existing
hosts should install verified unit changes through the narrow upgrade path:

```bash
sudo -u parking_user git pull --ff-only
sudo -u parking_user venv/bin/python -m pip install -r requirements.txt
sudo ./scripts/setup-service.sh --install-units
```

`configure-secrets.sh` prompts for Telegram and Discord bot tokens without
echoing them. Do not start or restart services until that command succeeds.

Common management commands affect all four services:

```bash
sudo parking-monitor start
sudo parking-monitor stop
sudo parking-monitor restart
sudo parking-monitor status
sudo parking-monitor logs
sudo parking-monitor logs -f
sudo parking-monitor logs -t notifier -n 100
sudo parking-monitor logs -t discord -n 100
sudo parking-monitor logs -t systemd -n 100  # unit lifecycle only
```

The normal and component log commands read the dedicated files under `logs/`;
systemd journal output is an explicit unit-lifecycle diagnostic mode.

## Bot commands

The authorized user can run `/status`, `/stats`, and `/interval` in either bot.
Telegram also provides `/start`. Discord alert buttons and Telegram inline
buttons expose Status and Stats. `/status` includes independent Telegram and
Discord delivery health.

For BotFather and private Discord installation steps, deployment gates, a
clearly labeled test-event procedure, database health queries, and rollback,
see [DEPLOYMENT.md](DEPLOYMENT.md).

## Test notifications

An operator test uses event type `test_notification` with `{"test": true}`.
Both channels label it `TEST NOTIFICATION`. Creating this event adds only a
notification event and two delivery rows; it must not change `state.json`
fields such as `checks`, `hits`, `last_enabled`, or `last_check`. The exact
production verification procedure is in the deployment guide.

## Recovery rule

Never delete `notifications.sqlite3`, its WAL files, `state.json`, or
`/etc/parking-monitor.env` during recovery. Preserve runtime files before
changing revisions. If the monitor or notifier cannot start after deployment,
stop only the new notifier and Discord services, restore the recorded previous
revision, and restart the former monitor and Telegram units as described in
[DEPLOYMENT.md](DEPLOYMENT.md#rollback).
