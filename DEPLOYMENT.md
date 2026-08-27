# Secure dual-channel deployment

This runbook deploys the monitor, notifier, private Telegram bot, and private
Discord bot without exposing credentials. Commands assume the checkout is
`/opt/parking_monitor` and its virtual environment is `venv`.

## Security boundaries

Keep both of these values secret:

- `TELEGRAM_BOT_TOKEN`
- `DISCORD_BOT_TOKEN`

Never pass a token as a command-line argument, paste it into chat, place it in
Git, or print `/etc/parking-monitor.env`. The Discord integration uses a bot
token, not a webhook URL.

These Discord identifiers are configuration, not credentials:

| Variable | Reviewed value |
|---|---:|
| `DISCORD_APPLICATION_ID` | `1542514080810664018` |
| `DISCORD_GUILD_ID` | `1476852384826392628` |
| `DISCORD_CHANNEL_ID` | `1542511880659017792` |
| `DISCORD_AUTHORIZED_USER_ID` | `1138419941926776893` |

The secret installer writes all eight required variable names to
`/etc/parking-monitor.env`, owned by `root:root` with mode `0600`. Systemd's
manager reads that file and passes the environment to processes running as
`parking_user`; the account itself must not be able to read the file.

The units use `ProtectSystem=strict` and expose `/opt/parking_monitor` as their
single writable application boundary. This is required for current behavior:
all processes initialize/read SQLite health, and both command bots atomically
replace `state.json` for `/interval`. Moving `state.json`, SQLite, and logs to
`/var/lib/parking-monitor` and `/var/log/parking-monitor` is a future hardening
step that would permit narrower per-service write paths.

## Platform preparation

### Telegram BotFather

In a private conversation with [BotFather](https://t.me/BotFather):

1. Use `/setjoingroups`, select the bot, and choose **Disable** so it cannot be
   added to groups.
2. Use `/setprivacy`, select the bot, and choose **Enable**. This is defense in
   depth if group membership is ever enabled later.
3. Restore the desired public name, description, and avatar if required.

The application allowlist remains authoritative even with these controls.

### Private Discord bot installation

In the Discord Developer Portal for the reviewed application:

1. On **Bot**, disable **Public Bot**. Do not enable Message Content, Server
   Members, or Presence privileged intents.
2. Under **Installation**, allow **Guild Install** only. Do not enable user
   installation.
3. For the guild install, request the `bot` and `applications.commands` scopes.
   Grant only View Channels, Send Messages, Embed Links, and Read Message
   History in the private parking channel.
4. Use the generated install link while signed in as the private server owner,
   choose the reviewed server, and confirm the bot can see only the intended
   channel.

Slash commands are registered only to the reviewed guild. Runtime checks also
require the reviewed guild, channel, and user IDs; Discord role permissions are
not the sole authorization boundary.

## Pre-deployment record

Do this before changing the checkout. Keep the output in the deployment record:

```bash
ssh cloudru-server
cd /opt/parking_monitor
set -euo pipefail

PREVIOUS_REVISION=$(sudo -u parking_user git rev-parse HEAD)
printf 'previous revision: %s\n' "$PREVIOUS_REVISION"
UNIT_BACKUP="/var/backups/parking-monitor-units-$(date -u +%Y%m%dT%H%M%SZ)"
sudo install -d -m 700 "$UNIT_BACKUP"
sudo install -m 600 /dev/null "$UNIT_BACKUP/unit-files.txt"
sudo install -m 600 /dev/null "$UNIT_BACKUP/enablement.txt"

backup_unit() {
  local requirement=$1
  local source_path=$2
  local unit_name=${source_path##*/}
  if sudo test -e "$source_path"; then
    sudo test -s "$source_path"
    sudo test -r "$source_path"
    sudo cp -a -- "$source_path" "$UNIT_BACKUP/$unit_name"
    sudo test -s "$UNIT_BACKUP/$unit_name"
    sudo test -r "$UNIT_BACKUP/$unit_name"
    printf 'present %s\n' "$unit_name" | \
      sudo tee -a "$UNIT_BACKUP/unit-files.txt" >/dev/null
  elif [ "$requirement" = mandatory ]; then
    printf 'mandatory unit is missing: %s\n' "$source_path" >&2
    return 1
  else
    printf 'absent %s\n' "$unit_name" | \
      sudo tee -a "$UNIT_BACKUP/unit-files.txt" >/dev/null
  fi
}

backup_unit mandatory /etc/systemd/system/parking-service-monitor.service
backup_unit mandatory /etc/systemd/system/parking-service-bot.service
backup_unit optional /etc/systemd/system/parking-service.service
backup_unit optional /etc/systemd/system/parking-service-notifier.service
backup_unit optional /etc/systemd/system/parking-service-discord.service

for unit in \
  parking-service.service parking-service-monitor parking-service-notifier \
  parking-service-bot parking-service-discord
do
  if state=$(systemctl is-enabled "$unit" 2>/dev/null); then
    :
  else
    state=${state:-not-found}
  fi
  printf '%s %s\n' "$unit" "${state:-not-found}" | \
    sudo tee -a "$UNIT_BACKUP/enablement.txt" >/dev/null
done

sudo test -s "$UNIT_BACKUP/parking-service-monitor.service"
sudo test -r "$UNIT_BACKUP/parking-service-monitor.service"
sudo test -s "$UNIT_BACKUP/parking-service-bot.service"
sudo test -r "$UNIT_BACKUP/parking-service-bot.service"
sudo test -s "$UNIT_BACKUP/unit-files.txt"
sudo test -r "$UNIT_BACKUP/unit-files.txt"
sudo test -s "$UNIT_BACKUP/enablement.txt"
sudo test -r "$UNIT_BACKUP/enablement.txt"
test "$(sudo awk 'END {print NR}' "$UNIT_BACKUP/unit-files.txt")" -eq 5
test "$(sudo awk 'END {print NR}' "$UNIT_BACKUP/enablement.txt")" -eq 5
for expected_unit in \
  parking-service.service parking-service-monitor.service \
  parking-service-notifier.service parking-service-bot.service \
  parking-service-discord.service
do
  test "$(sudo awk -v unit="$expected_unit" \
    '$2 == unit {count += 1} END {print count + 0}' \
    "$UNIT_BACKUP/unit-files.txt")" -eq 1
done
printf 'unit backup: %s\n' "$UNIT_BACKUP"
sudo systemctl show \
  parking-service-monitor parking-service-notifier \
  parking-service-bot parking-service-discord \
  --property=Id --property=ActiveState --property=ActiveEnterTimestamp
```

Also preserve monitoring counters for comparison without changing them:

```bash
sudo -u parking_user venv/bin/python - <<'PY'
import json
from pathlib import Path

state = json.loads(Path("state.json").read_text(encoding="utf-8"))
for name in ("checks", "hits", "last_enabled", "last_check"):
    print(f"{name}={state.get(name)!r}")
PY
```

Every command above is fail-closed. Do not install or remove any unit unless
both mandatory units, the five-line presence manifest, and the five-line
enablement record pass the non-empty/readable checks.

A first-time full setup recursively adopts the entire checkout for
`parking_user`, including nested source/docs directories, `.git`, and an
existing `venv`, without recursively chmodding the tree or changing
virtual-environment modes. After that boundary is established, all Git and
Python dependency operations run as `parking_user`; an existing host must not
rerun full setup during upgrade.

## Deploy code without restarting

Pull only a fast-forward update, install dependencies, and verify the checked
out code before installing units:

```bash
cd /opt/parking_monitor
sudo -u parking_user git pull --ff-only
sudo -u parking_user venv/bin/python -m pip install -r requirements.txt
sudo -u parking_user venv/bin/python -m playwright install chromium
sudo -u parking_user venv/bin/python -m unittest discover -v
sudo -u parking_user venv/bin/python -m py_compile \
  config.py command_service.py notification_store.py notifier.py \
  telegram_bot.py discord_bot.py monitor.py
sudo ./scripts/setup-service.sh --install-units
```

The existing-host upgrade uses the tested `--install-units` path only after
dependencies and code verification pass. It installs/enables the four unit
files and reloads systemd without rerunning full setup. It does not start
services and does not create or overwrite
`/etc/parking-monitor.env`. Do not restart any service until local remote tests
pass and the secret installation below validates.

## Install credentials privately

The user who owns the credentials must open their own SSH terminal and run
exactly:

```bash
ssh cloudru-server
cd /opt/parking_monitor
sudo ./scripts/configure-secrets.sh
```

Enter the replacement Telegram bot token and Discord bot token at the
non-echoing prompts. Do not send them to another operator.

Verify metadata and variable names only. These commands deliberately do not
print values:

```bash
sudo stat -c '%U:%G %a %n' /etc/parking-monitor.env
sudo awk -F= 'NF {print $1}' /etc/parking-monitor.env
sudo -u parking_user test ! -r /etc/parking-monitor.env
```

Required metadata is `root:root 600`. Required names are:

```text
TELEGRAM_BOT_TOKEN
TELEGRAM_CHAT_ID
TELEGRAM_AUTHORIZED_USER_ID
DISCORD_BOT_TOKEN
DISCORD_APPLICATION_ID
DISCORD_GUILD_ID
DISCORD_CHANNEL_ID
DISCORD_AUTHORIZED_USER_ID
```

If any check fails, do not start the services. Re-run the interactive installer;
do not repair the file by pasting tokens into an editor command.

## Start and verify

Restart the notifier and command bots first, then the monitor:

```bash
sudo systemctl restart parking-service-notifier
sudo systemctl restart parking-service-bot
sudo systemctl restart parking-service-discord
sudo systemctl restart parking-service-monitor

sudo parking-monitor status
sudo parking-monitor logs -t error -n 100
```

All four units must be `active`. Compare `checks`, `hits`, `last_enabled`, and
`last_check` with the pre-deployment record; a deployment must not reset them.
The Telegram startup must report polling with pending updates dropped, which
removes a stale webhook. Discord must register exactly the guild commands
`status`, `stats`, and `interval`, with no authentication or configuration
errors in either bot log.

The authorized user then runs:

- Telegram: `/status`
- Discord in the configured channel: `/status`, `/stats`, and `/interval`

Each `/status` response reports Telegram and Discord delivery health
independently.

## Health and delivery audit

Service health:

```bash
sudo parking-monitor status
sudo parking-monitor logs -t notifier -n 100
sudo parking-monitor logs -t bot -n 100
sudo parking-monitor logs -t discord -n 100
```

Read delivery metadata without selecting payloads or stored error text:

```bash
sudo -u parking_user venv/bin/python - <<'PY'
import sqlite3

connection = sqlite3.connect("notifications.sqlite3")
for row in connection.execute(
    """
    SELECT event_id, channel, status, attempt_count,
           last_attempt_at, delivered_at
    FROM notification_deliveries
    ORDER BY event_id DESC, channel
    LIMIT 20
    """
):
    print(row)
PY
```

Expected healthy delivery rows are `delivered`, one per event and channel.
`retry` on one channel does not invalidate a `delivered` row on the other.

## Clearly labeled test event

Record the four monitoring fields before the test, then create one administrative
event. The command below writes `test_notification` with `test=true`; both bots
render it as **TEST NOTIFICATION**. It does not write `state.json`.

```bash
cd /opt/parking_monitor
sudo -u parking_user venv/bin/python - <<'PY'
from datetime import datetime, timezone
from pathlib import Path

from notification_store import NotificationStore

store = NotificationStore(Path("notifications.sqlite3"))
event_id = store.create_event(
    "test_notification",
    {"test": True, "label": "operator delivery verification"},
    source_check=0,
    channels=("telegram", "discord"),
    event_key=f"operator-test:{datetime.now(timezone.utc).isoformat()}",
)
print(f"created test event_id={event_id}")
PY
```

Wait for the notifier, then confirm that the displayed event ID has exactly one
`delivered` row for Telegram and one for Discord. Confirm the labeled message is
visible in both applications. Re-read `checks`, `hits`, `last_enabled`, and
`last_check`; all four must match the pre-test values.

Do not simulate parking availability by editing `state.json`, toggling
`last_enabled`, or changing monitoring counters.

## Credential-leak audit

Before sign-off, a root-only operator must scan the four append files
`logs/monitor.log`, `logs/notifier.log`, `logs/telegram.log`, and
`logs/discord.log`, plus `notification_deliveries.last_error`, for the full
current token values and stable token fragments. The scanner must print only a
match count and location category, never the fragment or matching line. A
non-zero count blocks deployment. Also confirm no authorization headers,
token-bearing request URLs, or environment values appear in those append files.
Use `sudo parking-monitor logs -t systemd` only for unit lifecycle diagnostics;
application authentication errors are in the append files.

Record the final revision and service timestamps:

```bash
sudo -u parking_user git rev-parse HEAD
sudo systemctl show \
  parking-service-monitor parking-service-notifier \
  parking-service-bot parking-service-discord \
  --property=Id --property=ActiveState --property=ActiveEnterTimestamp
```

## Recovery

For a single failed service, inspect its dedicated log and restart only that
unit:

```bash
sudo parking-monitor logs -t notifier -n 100
sudo systemctl restart parking-service-notifier
sudo parking-monitor status
```

Authentication failures require rerunning the private secret installer. Never
put a replacement token on the command line.

## Rollback

Rollback is required if the monitor or notifier cannot start or monitoring
statistics change unexpectedly. Preserve runtime data before changing Git:

```bash
cd /opt/parking_monitor
set -euo pipefail
: "${PREVIOUS_REVISION:?set PREVIOUS_REVISION to the recorded revision}"
: "${UNIT_BACKUP:?set UNIT_BACKUP to the recorded backup directory}"

sudo test -s "$UNIT_BACKUP/parking-service-monitor.service"
sudo test -r "$UNIT_BACKUP/parking-service-monitor.service"
sudo test -s "$UNIT_BACKUP/parking-service-bot.service"
sudo test -r "$UNIT_BACKUP/parking-service-bot.service"
sudo test -s "$UNIT_BACKUP/unit-files.txt"
sudo test -r "$UNIT_BACKUP/unit-files.txt"
sudo test -s "$UNIT_BACKUP/enablement.txt"
sudo test -r "$UNIT_BACKUP/enablement.txt"
test "$(sudo awk 'END {print NR}' "$UNIT_BACKUP/unit-files.txt")" -eq 5
test "$(sudo awk 'END {print NR}' "$UNIT_BACKUP/enablement.txt")" -eq 5
for expected_unit in \
  parking-service.service parking-service-monitor.service \
  parking-service-notifier.service parking-service-bot.service \
  parking-service-discord.service
do
  test "$(sudo awk -v unit="$expected_unit" \
    '$2 == unit {count += 1} END {print count + 0}' \
    "$UNIT_BACKUP/unit-files.txt")" -eq 1
done
for expected_unit in \
  parking-service.service parking-service-monitor parking-service-notifier \
  parking-service-bot parking-service-discord
do
  test "$(sudo awk -v unit="$expected_unit" \
    '$1 == unit {count += 1} END {print count + 0}' \
    "$UNIT_BACKUP/enablement.txt")" -eq 1
done
sudo grep -qx 'present parking-service-monitor.service' \
  "$UNIT_BACKUP/unit-files.txt"
sudo grep -qx 'present parking-service-bot.service' \
  "$UNIT_BACKUP/unit-files.txt"
while read -r presence unit_name; do
  case "$unit_name" in
    parking-service.service|parking-service-monitor.service|\
    parking-service-notifier.service|parking-service-bot.service|\
    parking-service-discord.service)
      ;;
    *)
      printf 'invalid unit backup manifest entry: %s\n' "$unit_name" >&2
      exit 1
      ;;
  esac
  case "$presence" in
    present)
      sudo test -s "$UNIT_BACKUP/$unit_name"
      sudo test -r "$UNIT_BACKUP/$unit_name"
      ;;
    absent)
      sudo test ! -e "$UNIT_BACKUP/$unit_name"
      ;;
    *)
      printf 'invalid unit backup state: %s\n' "$presence" >&2
      exit 1
      ;;
  esac
done < <(sudo cat "$UNIT_BACKUP/unit-files.txt")

ROLLBACK_BACKUP="/var/backups/parking-monitor-$(date -u +%Y%m%dT%H%M%SZ)"
sudo install -d -m 700 "$ROLLBACK_BACKUP"
sudo cp -a state.json notifications.sqlite3 "$ROLLBACK_BACKUP"/
for file in notifications.sqlite3-wal notifications.sqlite3-shm; do
  if [ -e "$file" ]; then sudo cp -a "$file" "$ROLLBACK_BACKUP"/; fi
done
sudo systemctl disable --now parking-service-notifier parking-service-discord
sudo -u parking_user git switch --detach "$PREVIOUS_REVISION"
sudo rm -f \
  /etc/systemd/system/parking-service.service \
  /etc/systemd/system/parking-service-monitor.service \
  /etc/systemd/system/parking-service-notifier.service \
  /etc/systemd/system/parking-service-bot.service \
  /etc/systemd/system/parking-service-discord.service
while read -r presence unit_name; do
  if [ "$presence" = present ]; then
    sudo cp -a -- "$UNIT_BACKUP/$unit_name" \
      "/etc/systemd/system/$unit_name"
  fi
done < <(sudo cat "$UNIT_BACKUP/unit-files.txt")
sudo systemctl daemon-reload
while read -r unit state; do
  case "$unit" in
    parking-service-notifier|parking-service-discord)
      continue
      ;;
  esac
  case "$state" in
    enabled|enabled-runtime|linked|linked-runtime)
      sudo systemctl enable "$unit"
      ;;
    disabled)
      sudo systemctl disable "$unit"
      ;;
    masked|masked-runtime)
      sudo systemctl mask "$unit"
      ;;
  esac
done < <(sudo cat "$UNIT_BACKUP/enablement.txt")
sudo systemctl restart parking-service-bot parking-service-monitor
sudo systemctl status parking-service-bot parking-service-monitor --no-pager
```

The rollback preflight is intentionally before `disable --now`, Git checkout,
or unit removal. Any missing, unreadable, empty, malformed, or incomplete
backup aborts rollback without changing service state or installed units.

The new notifier and Discord units remain disabled throughout rollback so a
reboot cannot resurrect them. The recorded unit files and enablement are
restored for the aggregate, monitor, and Telegram units where they existed.
Report the exact gate that failed and the recorded revisions and timestamps.

Never delete or overwrite `notifications.sqlite3`, its WAL files, `state.json`,
the rollback backup, or `/etc/parking-monitor.env` during rollback.
