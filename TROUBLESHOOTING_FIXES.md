# Archived service-troubleshooting note

This historical note is superseded by [DEPLOYMENT.md](DEPLOYMENT.md) and
[SERVICE_ARCHITECTURE.md](SERVICE_ARCHITECTURE.md).

Do not relax the current service trust boundary or make checkout, venv,
Playwright browser, or root-invoked script paths service-writable. Runtime state
belongs in `/var/lib/parking-monitor/data`; logs belong in
`/var/log/parking-monitor`. Use `sudo parking-monitor test`, service status, and
the root-owned logs to diagnose failures without exposing scoped credentials.
