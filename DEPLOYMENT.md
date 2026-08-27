# Secure deployment and recovery

This runbook applies to the four-service deployment under `/opt/parking_monitor`.
Run production commands directly on the host. Never paste bot tokens into chat,
shell arguments, source files, logs, or issue trackers.

## Security and storage boundary

Trusted files remain root-owned and service-read-only:

- checkout and Git metadata: `/opt/parking_monitor`;
- virtual environment: `/opt/parking_monitor/venv`;
- setup, secret, and management scripts: `/opt/parking_monitor/scripts`;
- installed management command: root-owned copy at `/usr/local/bin/parking-monitor`.

Mutable files are outside the checkout:

- state and SQLite/WAL/SHM: `/var/lib/parking-monitor/data`;
- root-owned Playwright Chromium files: `/var/lib/parking-monitor/ms-playwright`;
- append logs: `/var/log/parking-monitor`;
- update snapshots: `/var/lib/parking-monitor/backups`.

The services use distinct non-login users:

- `parking-monitor-monitor`;
- `parking-monitor-notifier`;
- `parking-monitor-telegram`;
- `parking-monitor-discord`.

Their primary group is `parking-monitor`. Only
`/var/lib/parking-monitor/data` is group-writable. The checkout, venv,
Playwright browsers, root scripts, and installed management command are not.

## Scoped configuration

`sudo ./scripts/configure-secrets.sh` reads both tokens without echo, validates
their shapes, and atomically writes four root-owned mode `0600` files:

- `/etc/parking-monitor/telegram-bot.env` — Telegram command token, chat ID,
  and authorized-user ID;
- `/etc/parking-monitor/discord-bot.env` — Discord command token and all
  command-boundary IDs;
- `/etc/parking-monitor/notifier-telegram.env` — only the Telegram delivery
  token and destination chat;
- `/etc/parking-monitor/notifier-discord.env` — only the Discord delivery
  token and destination channel.

The monitor receives no environment file. A missing channel file disables only
that channel. The notifier persists the channel as `disabled`; the sibling
adapter and command service continue.

The reviewed identifiers are:

- Telegram chat/user: `404346140`;
- Discord application: `1542514080810664018`;
- Discord server: `1476852384826392628`;
- Discord channel: `1542511880659017792`;
- Discord user: `1138419941926776893`.

## Local verification before commit

From the worktree, run:

```powershell
& 'D:\Project Y\Parking monitor\.venv\Scripts\python.exe' -m unittest discover -v
& 'D:\Project Y\Parking monitor\.venv\Scripts\python.exe' -m py_compile config.py state_store.py command_service.py notification_store.py notifier.py telegram_bot.py discord_bot.py monitor.py
& 'C:\Program Files\Git\bin\bash.exe' -n scripts/configure-secrets.sh scripts/setup-service.sh scripts/manage-parking-monitor.sh scripts/monitor.sh
git diff --check
```

Use only synthetic tokens for mutation tests. A passing scan must not print a
live credential.

## First installation

Deploy the reviewed checkout as root, then run:

```bash
cd /opt/parking_monitor
sudo ./scripts/setup-service.sh
sudo ./scripts/configure-secrets.sh
```

The setup creates the four users, root-owned venv and browser installation,
runtime/log directories, four unit files, and the copied management command.
It enables units without starting them.

Verify unit syntax and Linux path/access assumptions before starting:

```bash
sudo systemd-analyze verify /etc/systemd/system/parking-service-{monitor,notifier,bot,discord}.service
sudo stat -c '%U:%G %a %n' \
  /opt/parking_monitor \
  /opt/parking_monitor/venv \
  /var/lib/parking-monitor \
  /var/lib/parking-monitor/data \
  /var/lib/parking-monitor/ms-playwright \
  /var/log/parking-monitor \
  /usr/local/bin/parking-monitor
sudo parking-monitor test
sudo parking-monitor start
sudo parking-monitor status
```

`/usr/local/bin/parking-monitor` must be a regular root-owned executable, not a
symlink. The four service users must not be able to create files in
`/opt/parking_monitor`, its venv, or the Playwright browser directory.

## Safe update

Use the installed root-owned command:

```bash
sudo parking-monitor update
```

The command rejects a dirty checkout, fetches the target, proves it is a fast
forward, and creates a detached staging worktree. It builds a fresh venv and
browser set and verifies tests, Python compilation, Bash syntax, rendered
units, and `systemd-analyze verify` before cutover.

Only after staging passes does it stop all four SQLite users. It then uses the
SQLite backup API and copies `state.json` and installed units into a timestamped
snapshot. Cutover uses `git pull --ff-only`, swaps the verified venv/browser
environment, installs units, and restores only the services that were running.

If pull, swap, unit installation, or service restart fails, the command stops
all four services and restores the recorded Git revision, previous venv,
Playwright browser tree, units/enablement, state, and SQLite snapshot before
restarting the formerly active services.

Do not copy a live SQLite main file while any of the four services is running.
Do not restore `state.json` without its matching SQLite snapshot.

## Channel recovery

After correcting a token, bot permission, or destination, restart the notifier:

```bash
sudo systemctl restart parking-service-notifier
```

Startup requeues terminal `failed` rows only for configured channels. Delivered
rows remain terminal and are never resent. Missing channels stay `disabled`.

## Verification after start

```bash
sudo parking-monitor status
sudo parking-monitor logs -n 100
sudo systemctl show \
  parking-service-monitor parking-service-notifier \
  parking-service-bot parking-service-discord \
  -p User -p Group -p Environment -p ReadWritePaths
```

Verify authorized Telegram and Discord `/status`, `/stats`, and `/interval`.
Status must include the next expected check and channel state; statistics must
include delivered counts. Create a clearly labeled synthetic notification
event and confirm independent Telegram and Discord delivery records without
changing parking statistics.

Inspect logs and SQLite error fields using token fragments generated solely for
the synthetic test. Neither the fragments, authorization headers, webhook URLs,
nor URL query strings may appear.

## External controls

Disable Telegram group addition in BotFather and keep the application allowlist
enabled. Install the Discord application only in the reviewed private server;
runtime guild/channel/user checks remain authoritative.

## Known local-test boundary

Windows tests cover atomic replacement, token-shape rejection, scoped file
contents, service rendering, and failure preservation. Terminal echo flags and
signal delivery differ across Linux TTY implementations; verify non-echo and
signal cleanup manually in a disposable Linux terminal without real tokens.
