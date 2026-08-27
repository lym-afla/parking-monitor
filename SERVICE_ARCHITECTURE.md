# Service architecture

## Process and privilege map

| Unit | Identity | Credentials | Mutable access |
|---|---|---|---|
| `parking-service-monitor` | `parking-monitor-monitor` | none | shared state and SQLite |
| `parking-service-notifier` | `parking-monitor-notifier` | Telegram/Discord delivery-only files | shared SQLite/state migration |
| `parking-service-bot` | `parking-monitor-telegram` | Telegram command file only | interval state and SQLite health |
| `parking-service-discord` | `parking-monitor-discord` | Discord command file only | interval state and SQLite health |

Every identity has primary group `parking-monitor`, a nonexistent home, and
`/usr/sbin/nologin`. Distinct UIDs prevent sibling environment inspection. The
group grants write access only to `/var/lib/parking-monitor/data`.

The systemd manager reads root-owned mode `0600` environment files before
dropping privileges. Units use `ProtectSystem=strict`, `ProtectHome=true`,
`NoNewPrivileges=true`, `PrivateTmp=true`, `UMask=0007`, and only
`ReadWritePaths=/var/lib/parking-monitor/data`.

## Filesystem map

```text
/opt/parking_monitor/                     root:root, service read-only
  venv/                                   root:root, service read-only
  scripts/                                root:root, service read-only

/var/lib/parking-monitor/                 root:root
  data/                                   root:parking-monitor, 2770
    state.json                            shared JSON state, 0660
    state.json.lock                       cross-process lock, 0660
    notifications.sqlite3[-wal|-shm]      shared SQLite files
  ms-playwright/                          root:root, service read-only
  backups/                                root-only update snapshots

/var/log/parking-monitor/                 root:parking-monitor, 0750
  monitor.log
  notifier.log
  telegram.log
  discord.log

/etc/parking-monitor/                     root:root, 0700
  telegram-bot.env                        root:root, 0600
  discord-bot.env                         root:root, 0600
  notifier-telegram.env                   root:root, 0600
  notifier-discord.env                    root:root, 0600

/usr/local/bin/parking-monitor            root-owned regular-file copy, 0755
```

Playwright uses the explicit unit environment
`PLAYWRIGHT_BROWSERS_PATH=/var/lib/parking-monitor/ms-playwright`. Setup installs
Chromium there as root and removes group/other write permission. No unit may
write the checkout, venv, browser tree, setup scripts, or installed management
command.

## State consistency

`state_store.py` uses one stable `state.json.lock` file. On POSIX it uses
`fcntl.flock`; on Windows it uses `msvcrt.locking`, with an additional in-process
path mutex. A mutation holds the lock across JSON read, field merge, fsync, and
atomic replace.

The monitor owns `checks`, `hits`, `last_check`, `last_enabled`, and `error`.
Command front ends own `interval`. Both mutate the newest on-disk object under
the same lock, so neither can replace the other's concurrent fields. Legacy
alert migration uses the same protocol.

## Delivery lifecycle

The monitor writes one idempotent event for each false-to-true transition.
SQLite contains one delivery row per channel. `BEGIN IMMEDIATE` and a claim
token make claims exclusive across processes; expired claims can be recovered.

- `delivered` is terminal and never re-sent;
- transient failures become `retry` with bounded delays;
- authentication/destination failures become `failed`;
- notifier startup calls `requeue_failed` only for configured channels;
- absent channel configuration persists `disabled` health and is never claimed.

Retry delay is measured from network completion, not claim start. Unexpected
exceptions are sanitized with the in-memory configured token before persistence.
Health transitions are logged immediately and summaries every five minutes.

## Command views

Telegram and Discord call the same `CommandService`. Status includes current
availability, polling mode, active/normal interval, next expected check, and
per-channel state/counts. Statistics include checks, hits, rate, last check,
and per-channel delivered/pending/retrying/failed counts.

Authorization is checked before any state access. Telegram requires the exact
authorized user and chat. Discord requires the exact guild, channel, and user.

## Update transaction

`parking-monitor update` fetches and stages the target revision in a detached
worktree. A fresh venv, Chromium tree, test suite, Python compilation, Bash
syntax, rendered units, and `systemd-analyze verify` must pass before cutover.

All four database users then stop. The management command takes a SQLite backup
through the SQLite backup API plus the matching state/unit snapshot. Cutover is
a `git pull --ff-only` followed by verified venv/browser and unit installation.
Any failure restores the recorded revision, venv, browser tree, units,
enablement, state, and SQLite snapshot before restarting the previously active
services.
