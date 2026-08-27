# Parking Monitor documentation

Parking Monitor checks parking availability and records durable notification
events for independent Telegram and Discord delivery. Four systemd services run
the monitor, notifier, Telegram command bot, and Discord command bot.

The authoritative documents are:

- [README.md](README.md) for the component overview and local development;
- [SERVICE_ARCHITECTURE.md](SERVICE_ARCHITECTURE.md) for process, trust, storage,
  and configuration boundaries;
- [DEPLOYMENT.md](DEPLOYMENT.md) for installation, credential setup, verification,
  safe update, rollback, and recovery;
- [docs/superpowers/specs/2026-08-27-secure-dual-channel-notifications-design.md](docs/superpowers/specs/2026-08-27-secure-dual-channel-notifications-design.md)
  for the approved feature and behavior specification.

Production invariants:

- trusted code, the venv, Playwright browser files, and root-invoked scripts are
  root-owned and service-read-only;
- mutable state and SQLite files live in `/var/lib/parking-monitor/data` and
  logs live in `/var/log/parking-monitor`;
- each network service has a distinct user and receives only its scoped
  credential file;
- either Telegram or Discord can be absent and independently reports
  `disabled` without stopping the other channel;
- state mutations use one cross-process lock and notification delivery uses
  durable per-channel rows.

Do not put tokens in source files, shell arguments, logs, screenshots, or issue
trackers. Use `sudo ./scripts/configure-secrets.sh` on the target host; it reads
tokens without echo, validates their shape, and writes root-only files.

Do not make `/opt/parking_monitor`, its `.git` directory, venv, scripts, or
Playwright browser directory writable by any service account. Use the installed
root-owned `sudo parking-monitor update` transaction for production updates.
