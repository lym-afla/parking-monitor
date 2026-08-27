# Secure Dual-Channel Notifications Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Deliver and control parking alerts independently through private Telegram and Discord bots without storing credentials in source control.

**Architecture:** The monitor writes availability events to SQLite, a dedicated notifier delivers each event independently to Telegram and Discord, and two command front ends call one shared command service. Systemd loads root-owned secrets into four isolated application services.

**Tech Stack:** Python 3.10+, `sqlite3`, `python-telegram-bot`, `discord.py`, `httpx`, systemd, `unittest`

**Spec:** `docs/superpowers/specs/2026-08-27-secure-dual-channel-notifications-design.md`

## Global Constraints

- Telegram access is limited to user/chat `404346140`.
- Discord access is limited to application `1542514080810664018`, guild `1476852384826392628`, channel `1542511880659017792`, and user `1138419941926776893`.
- Discord commands are guild-scoped `/status`, `/stats`, and `/interval`; no privileged Gateway intents are enabled.
- The month-end five-minute schedule in `monitor.py` remains intact.
- Successful channel deliveries are never retried; failed channels retry independently.
- Tokens, authorization headers, and Discord webhook paths never enter logs, state, exceptions, or audit rows.
- Replacement credentials are entered only through a non-echoing production terminal prompt.

---

## File Structure

- `config.py`: parse and validate environment-only runtime configuration.
- `command_service.py`: shared status, stats, and normal-interval operations.
- `notification_store.py`: SQLite schema, event creation, claims, results, retries, health summaries, and legacy migration.
- `notifier.py`: Telegram/Discord REST adapters and the delivery worker loop.
- `telegram_bot.py`: authorized Telegram commands, callbacks, and polling startup.
- `discord_bot.py`: authorized guild slash commands and interactive buttons.
- `monitor.py`: enqueue an event on an unavailable-to-available transition.
- `scripts/configure-secrets.sh`: root-only, non-echoing atomic secret installation.
- `scripts/setup-service.sh`: install/update monitor, notifier, Telegram, and Discord systemd units.
- `requirements.txt`: explicit Discord and HTTP dependencies.
- `tests/`: focused unit/integration tests using temporary files and real SQLite.

---

### Task 1: Environment-Only Configuration and Secret Installation

**Files:**
- Modify: `config.py`
- Modify: `.gitignore`
- Create: `scripts/configure-secrets.sh`
- Create: `tests/test_config.py`
- Modify: `requirements.txt`

**Interfaces:**
- Produces: `RuntimeConfig` and `load_config(env: Mapping[str, str]) -> RuntimeConfig`; services explicitly load runtime configuration instead of creating it during module import.
- Produces: `/etc/parking-monitor.env` with mode `0600` and all required IDs/tokens.

- [ ] **Step 1: Write failing configuration tests**

```python
def test_load_config_requires_tokens():
    with self.assertRaisesRegex(ValueError, "TELEGRAM_BOT_TOKEN"):
        load_config({})

def test_config_repr_redacts_tokens():
    cfg = load_config(VALID_ENV)
    rendered = repr(cfg)
    self.assertNotIn(VALID_ENV["TELEGRAM_BOT_TOKEN"], rendered)
    self.assertNotIn(VALID_ENV["DISCORD_BOT_TOKEN"], rendered)

def test_rejects_non_numeric_authorization_ids():
    env = {**VALID_ENV, "DISCORD_AUTHORIZED_USER_ID": "not-a-number"}
    with self.assertRaisesRegex(ValueError, "DISCORD_AUTHORIZED_USER_ID"):
        load_config(env)
```

- [ ] **Step 2: Run tests and verify RED**

Run: `.\.venv\Scripts\python.exe -m unittest -v tests.test_config`

Expected: import failure because `RuntimeConfig` and `load_config` do not exist.

- [ ] **Step 3: Implement validated configuration**

```python
@dataclass(frozen=True, repr=False)
class RuntimeConfig:
    telegram_bot_token: str
    telegram_chat_id: int
    telegram_authorized_user_id: int
    discord_bot_token: str
    discord_application_id: int
    discord_guild_id: int
    discord_channel_id: int
    discord_authorized_user_id: int

    def __repr__(self):
        return "RuntimeConfig(tokens=<redacted>, destinations=configured)"

def load_config(env=os.environ):
    required = (...)
    missing = [name for name in required if not env.get(name)]
    if missing:
        raise ValueError(f"Missing required environment variable: {missing[0]}")
    return RuntimeConfig(...)
```

Keep non-secret site/address/state constants in `config.py`. Remove both hard-coded Telegram values. Add `discord.py>=2.4,<3` and `httpx>=0.27,<1` to `requirements.txt`.

- [ ] **Step 4: Add the interactive secret installer**

The script must require root, use `read -r -s` for both tokens, use fixed reviewed defaults for all IDs, validate non-empty tokens, set `umask 077`, write a `mktemp` file, and use `install -o root -g root -m 600` to atomically replace `/etc/parking-monitor.env`. Its output may list variable names but never values.

- [ ] **Step 5: Verify GREEN and secret absence**

Run:

```powershell
.\.venv\Scripts\python.exe -m unittest -v tests.test_config
rg -n "8134011188:|discord\.com/api/webhooks|DISCORD_BOT_TOKEN\s*=" -g "*.py" -g "*.md" -g "*.sh"
git diff --check
```

Expected: tests pass; secret scan returns no live credential literal (the spec may contain variable names only).

- [ ] **Step 6: Commit**

```bash
git add config.py .gitignore requirements.txt scripts/configure-secrets.sh tests/test_config.py
git commit -m "Secure runtime notification configuration"
```

---

### Task 2: Shared Command Operations

**Files:**
- Create: `command_service.py`
- Create: `tests/test_command_service.py`
- Modify: `monitor.py`

**Interfaces:**
- Produces: `CommandService.status(now=None) -> StatusSnapshot`.
- Produces: `CommandService.stats() -> StatsSnapshot`.
- Produces: `CommandService.set_normal_interval(seconds: int) -> StatusSnapshot`.
- Consumes later: both Telegram and Discord handlers format these snapshots for their platforms.

- [ ] **Step 1: Write failing shared-operation tests**

```python
def test_status_reports_month_end_override_without_changing_normal_interval():
    service = CommandService(state_path, health_provider=lambda: EMPTY_HEALTH)
    status = service.status(datetime(2026, 8, 27, 12, 0, tzinfo=MSK))
    self.assertEqual(status.polling_mode, "month-end")
    self.assertEqual(status.effective_interval_seconds, 300)
    self.assertEqual(status.normal_interval_seconds, 1800)

def test_set_interval_atomically_updates_normal_interval():
    status = service.set_normal_interval(900)
    self.assertEqual(status.normal_interval_seconds, 900)
    self.assertEqual(json.loads(state_path.read_text())["interval"], 900)

def test_rejects_interval_outside_allowed_range():
    with self.assertRaisesRegex(ValueError, "60.*86400"):
        service.set_normal_interval(30)
```

- [ ] **Step 2: Run tests and verify RED**

Run: `.\.venv\Scripts\python.exe -m unittest -v tests.test_command_service`

Expected: import failure for `command_service`.

- [ ] **Step 3: Implement snapshots and atomic state writes**

Use frozen dataclasses for `StatusSnapshot`, `StatsSnapshot`, and channel health. Read `state.json` once per operation. Write interval changes to a sibling temporary file, `flush`, `os.fsync`, then `os.replace`. Reuse `get_polling_schedule` from `monitor.py`; do not duplicate calendar calculations.

- [ ] **Step 4: Run shared and schedule tests**

Run:

```powershell
.\.venv\Scripts\python.exe -m unittest -v tests.test_command_service test_monitor_schedule.py
```

Expected: all tests pass and the five-minute override remains unchanged.

- [ ] **Step 5: Commit**

```bash
git add command_service.py monitor.py tests/test_command_service.py
git commit -m "Share parking command operations"
```

---

### Task 3: Durable Notification Store

**Files:**
- Create: `notification_store.py`
- Create: `tests/test_notification_store.py`

**Interfaces:**
- Produces: `NotificationStore.create_event(event_type, payload, source_check, channels, event_key=None) -> int`.
- Produces: `NotificationStore.claim_due(channel, now) -> DeliveryClaim | None`.
- Produces: `mark_delivered(claim, now)`, `mark_retry(claim, error, now)`, and `mark_failed(claim, error, now)`.
- Produces: `health_summary() -> dict[str, ChannelHealth]` and `migrate_legacy_alert(state_path) -> int | None`.

- [ ] **Step 1: Write failing store tests using a real temporary SQLite database**

```python
def test_event_creates_one_delivery_per_channel():
    event_id = store.create_event("parking_available", {"test": False}, 42, ("telegram", "discord"))
    rows = store.deliveries_for(event_id)
    self.assertEqual([(r.channel, r.status) for r in rows], [("discord", "pending"), ("telegram", "pending")])

def test_successful_channel_is_not_claimed_again_when_other_channel_retries():
    telegram = store.claim_due("telegram", NOW)
    store.mark_delivered(telegram, NOW)
    discord = store.claim_due("discord", NOW)
    store.mark_retry(discord, "timeout", NOW)
    self.assertIsNone(store.claim_due("telegram", NOW + timedelta(days=1)))

def test_retry_delays_are_30_120_600_1800_then_3600_seconds():
    observed = exercise_retries(store, attempts=6)
    self.assertEqual(observed, [30, 120, 600, 1800, 3600, 3600])

def test_restart_releases_expired_claim_without_duplicate_delivery():
    claim = store.claim_due("discord", NOW)
    reopened = NotificationStore(db_path)
    self.assertIsNotNone(reopened.claim_due("discord", NOW + CLAIM_TIMEOUT))
```

- [ ] **Step 2: Run tests and verify RED**

Run: `.\.venv\Scripts\python.exe -m unittest -v tests.test_notification_store`

Expected: import failure for `notification_store`.

- [ ] **Step 3: Implement schema and transactional claims**

Use WAL mode, foreign keys, UTC ISO timestamps, parameterized SQL, and `BEGIN IMMEDIATE` for claims. Add an internal `claimed` status and `claim_expires_at` so a killed worker cannot strand a delivery. Validate channel names against `telegram` and `discord`. Store sanitized errors capped at 500 characters.

- [ ] **Step 4: Implement idempotent legacy migration**

If `state.json.alert` is true, create a single event with deterministic migration key `legacy-alert:<last_check>`, commit it, then atomically clear the Boolean. A unique event key prevents duplicates if deployment is interrupted.

- [ ] **Step 5: Run store tests and inspect schema**

Run: `.\.venv\Scripts\python.exe -m unittest -v tests.test_notification_store`

Expected: all store, retry, restart, and migration cases pass.

- [ ] **Step 6: Commit**

```bash
git add notification_store.py tests/test_notification_store.py
git commit -m "Add durable notification delivery store"
```

---

### Task 4: Independent Delivery Worker

**Files:**
- Create: `notifier.py`
- Create: `tests/test_notifier.py`

**Interfaces:**
- Consumes: `NotificationStore` claim/result methods and `RuntimeConfig`.
- Produces: `TelegramAdapter.send(claim) -> DeliveryResult`, `DiscordAdapter.send(claim) -> DeliveryResult`, and `Notifier.run_once(now) -> int`.

- [ ] **Step 1: Write failing adapter/worker tests**

```python
async def test_one_channel_failure_does_not_block_other_channel():
    worker = Notifier(store, {"telegram": FailingAdapter("timeout"), "discord": SuccessfulAdapter()})
    await worker.run_once(NOW)
    self.assertEqual(store.delivery(event_id, "telegram").status, "retry")
    self.assertEqual(store.delivery(event_id, "discord").status, "delivered")

def test_sanitize_error_removes_tokens_urls_and_authorization_headers():
    raw = f"Authorization: Bot {TOKEN} POST {WEBHOOK_LIKE_URL} failed"
    cleaned = sanitize_error(raw, secrets=(TOKEN,))
    self.assertNotIn(TOKEN, cleaned)
    self.assertNotIn("/api/webhooks/", cleaned)
    self.assertNotIn("Authorization", cleaned)

def test_authentication_error_is_permanent():
    self.assertEqual(classify_http_failure(401), "failed")
    self.assertEqual(classify_http_failure(429), "retry")
```

- [ ] **Step 2: Run tests and verify RED**

Run: `.\.venv\Scripts\python.exe -m unittest -v tests.test_notifier`

Expected: import failure for `notifier`.

- [ ] **Step 3: Implement adapters at the HTTP boundary**

Use one `httpx.AsyncClient` with explicit connect/read/write/pool timeouts. Telegram posts to `sendMessage` with inline callback data `status` and `stats`. Discord posts a bot-authenticated channel message with an embed and two component buttons. Treat 2xx as success; 401/403/404 as permanent configuration failure; 408/429/5xx and network exceptions as retryable.

- [ ] **Step 4: Implement fair worker iteration**

`run_once` attempts at most one due delivery per channel, records results independently, and returns the number attempted. `main` initializes/migrates the store, logs sanitized health transitions, and waits five seconds when no row is due.

- [ ] **Step 5: Run tests and a credential mutation check**

Run:

```powershell
.\.venv\Scripts\python.exe -m unittest -v tests.test_notifier tests.test_notification_store
```

Expected: independent delivery, classification, and redaction tests pass.

- [ ] **Step 6: Commit**

```bash
git add notifier.py tests/test_notifier.py
git commit -m "Deliver alerts independently by channel"
```

---

### Task 5: Harden and Refactor Telegram Commands

**Files:**
- Modify: `telegram_bot.py`
- Create: `tests/test_telegram_bot.py`

**Interfaces:**
- Consumes: `CommandService` and `RuntimeConfig`.
- Produces: `is_authorized(update, config) -> bool` and authorized handlers for `/start`, `/status`, `/stats`, `/interval`, plus `status`/`stats` callbacks.

- [ ] **Step 1: Write failing authorization and startup tests**

```python
async def test_unauthorized_command_does_not_read_state_or_reply():
    update = telegram_update(user_id=999, chat_id=999)
    await handlers.status(update, context)
    state_reader.assert_not_called()
    update.message.reply_text.assert_not_called()

async def test_authorized_command_uses_shared_status():
    update = telegram_update(user_id=404346140, chat_id=404346140)
    await handlers.status(update, context)
    command_service.status.assert_called_once()

async def test_startup_deletes_stale_webhook_before_polling():
    await prepare_telegram_application(app)
    app.bot.delete_webhook.assert_awaited_once_with(drop_pending_updates=True)
```

- [ ] **Step 2: Run tests and verify RED**

Run: `.\.venv\Scripts\python.exe -m unittest -v tests.test_telegram_bot`

Expected: unauthorized handler currently accesses/replies, and startup helper is absent.

- [ ] **Step 3: Add authorization-first handlers and shared formatting**

Construct handlers with injected `CommandService`. Check both numeric IDs before any call to the service. Unauthorized callbacks must answer the callback without exposing data; unauthorized commands remain silent. Remove the background alert thread because `notifier.py` owns outbound availability delivery. Remove every token/chat print.

- [ ] **Step 4: Delete stale webhook before polling**

Use the framework initialization hook to await `delete_webhook(drop_pending_updates=True)` once, then run polling. Log only success/failure category.

- [ ] **Step 5: Run Telegram and shared-operation tests**

Run:

```powershell
.\.venv\Scripts\python.exe -m unittest -v tests.test_telegram_bot tests.test_command_service
```

- [ ] **Step 6: Commit**

```bash
git add telegram_bot.py tests/test_telegram_bot.py
git commit -m "Restrict Telegram bot commands"
```

---

### Task 6: Private Discord Bot with Mirrored Commands

**Files:**
- Create: `discord_bot.py`
- Create: `tests/test_discord_bot.py`

**Interfaces:**
- Consumes: `CommandService` and `RuntimeConfig`.
- Produces: guild-scoped `/status`, `/stats`, `/interval`, and handlers for `parking:status` and `parking:stats` buttons.

- [ ] **Step 1: Write failing Discord authorization tests**

```python
def test_authorization_requires_exact_guild_channel_and_user():
    self.assertTrue(is_authorized(interaction(1476852384826392628, 1542511880659017792, 1138419941926776893), CONFIG))
    self.assertFalse(is_authorized(interaction(1476852384826392628, 1542511880659017792, 999), CONFIG))
    self.assertFalse(is_authorized(interaction(999, 1542511880659017792, 1138419941926776893), CONFIG))

async def test_unauthorized_interval_never_mutates_state():
    await commands.interval(interaction(user_id=999), minutes=15)
    command_service.set_normal_interval.assert_not_called()

async def test_status_uses_shared_service_and_ephemeral_response():
    await commands.status(authorized_interaction())
    command_service.status.assert_called_once()
    interaction.response.send_message.assert_awaited_once_with(ANY, ephemeral=True)
```

- [ ] **Step 2: Run tests and verify RED**

Run: `.\.venv\Scripts\python.exe -m unittest -v tests.test_discord_bot`

Expected: import failure for `discord_bot`.

- [ ] **Step 3: Implement a minimal-intents bot**

Use `discord.Intents.none()` with `guilds=True`. Create guild-scoped commands with a `discord.Object` guild ID and synchronize only that guild in `setup_hook`. Set restrictive default permissions and enforce runtime ID checks before service calls. Responses to commands are ephemeral; availability messages remain visible in the parking channel.

- [ ] **Step 4: Implement persistent Status/Stats buttons**

Register a persistent `discord.ui.View(timeout=None)` using stable custom IDs `parking:status` and `parking:stats`. Each callback performs the same authorization check and returns an ephemeral shared-service result.

- [ ] **Step 5: Run Discord tests**

Run: `.\.venv\Scripts\python.exe -m unittest -v tests.test_discord_bot tests.test_command_service`

Expected: exact-boundary authorization and all mirrored commands pass.

- [ ] **Step 6: Commit**

```bash
git add discord_bot.py tests/test_discord_bot.py
git commit -m "Add private Discord command bot"
```

---

### Task 7: Monitor Event Integration and Migration

**Files:**
- Modify: `monitor.py`
- Create: `tests/test_monitor_events.py`

**Interfaces:**
- Consumes: `NotificationStore.create_event`.
- Produces: exactly one `parking_available` event per false-to-true transition, with source check and non-secret payload.

- [ ] **Step 1: Write failing transition tests**

```python
def test_false_to_true_creates_one_dual_channel_event():
    state = {"last_enabled": False, "checks": 41}
    updated = apply_check_result(state, enabled=True, store=store)
    self.assertEqual(store.event_count(), 1)
    self.assertEqual({row.channel for row in store.deliveries_for(1)}, {"telegram", "discord"})

def test_repeated_true_does_not_create_duplicate_event():
    state = {"last_enabled": True, "checks": 42}
    apply_check_result(state, enabled=True, store=store)
    self.assertEqual(store.event_count(), 0)
```

- [ ] **Step 2: Run tests and verify RED**

Run: `.\.venv\Scripts\python.exe -m unittest -v tests.test_monitor_events`

Expected: `apply_check_result` is absent and current code sets only `alert`.

- [ ] **Step 3: Extract and implement transition application**

Move state mutation after each scrape into `apply_check_result`. Commit the SQLite event before saving `last_enabled=True`; use a deterministic event key containing the transition check number so a retry cannot duplicate it. Stop writing `state["alert"]` for new events.

- [ ] **Step 4: Run monitor, migration, and schedule tests**

Run:

```powershell
.\.venv\Scripts\python.exe -m unittest -v tests.test_monitor_events tests.test_notification_store test_monitor_schedule.py
```

- [ ] **Step 5: Commit**

```bash
git add monitor.py tests/test_monitor_events.py
git commit -m "Queue durable parking availability events"
```

---

### Task 8: Systemd Services, Full Verification, and Production Deployment

**Files:**
- Modify: `scripts/setup-service.sh`
- Modify: `scripts/manage-parking-monitor.sh`
- Modify: `README.md`
- Modify: `DEPLOYMENT.md`
- Create: `tests/test_service_templates.py`

**Interfaces:**
- Produces services: `parking-service-monitor`, `parking-service-notifier`, `parking-service-bot`, and `parking-service-discord`.
- All services consume `/etc/parking-monitor.env`; only monitor/notifier write the app directory/database.

- [ ] **Step 1: Write failing service-template tests**

Render service definitions into a temporary directory and assert exact unit properties:

```python
self.assertIn("EnvironmentFile=/etc/parking-monitor.env", notifier_unit)
self.assertIn("ExecStart=/opt/parking_monitor/venv/bin/python /opt/parking_monitor/notifier.py", notifier_unit)
self.assertIn("ExecStart=/opt/parking_monitor/venv/bin/python /opt/parking_monitor/discord_bot.py", discord_unit)
self.assertNotIn("DISCORD_BOT_TOKEN=", all_units)
```

- [ ] **Step 2: Run tests and verify RED**

Run: `.\.venv\Scripts\python.exe -m unittest -v tests.test_service_templates`

Expected: notifier/Discord service definitions and external environment file are absent.

- [ ] **Step 3: Implement service installation and management**

Create four units with `Restart=on-failure`, `RestartSec=10`, root-readable `EnvironmentFile`, dedicated append logs, and minimum writable paths. Management `start`, `stop`, `restart`, `status`, and `logs` must include all four services. Setup must not create or overwrite the secret file.

- [ ] **Step 4: Update operator documentation**

Document Discord IDs as non-secret, tokens as secrets, the exact `sudo ./scripts/configure-secrets.sh` command, BotFather group/privacy controls, Discord private-bot installation, health queries, test-event semantics, and recovery/rollback commands.

- [ ] **Step 5: Run complete local verification**

Run:

```powershell
.\.venv\Scripts\python.exe -m unittest discover -v
.\.venv\Scripts\python.exe -m py_compile config.py command_service.py notification_store.py notifier.py telegram_bot.py discord_bot.py monitor.py
git diff --check
rg -n "8134011188:|discord\.com/api/webhooks|Bot\s+[A-Za-z0-9._-]{20,}" -g "*.py" -g "*.sh" -g "*.md"
```

Expected: all tests and compilation pass; secret scan finds no credential literal.

- [ ] **Step 6: Review, commit, and push**

```bash
git add scripts/setup-service.sh scripts/manage-parking-monitor.sh README.md DEPLOYMENT.md tests/test_service_templates.py
git commit -m "Deploy secure dual-channel parking notifications"
git push origin main
```

- [ ] **Step 7: Deploy code without restarting services**

On `cloudru-server`, record the current revision and service start timestamps, fast-forward pull, install dependencies, run the full remote test suite, and install the updated unit files. Do not restart until the secret file exists and validates.

- [ ] **Step 8: User enters credentials privately**

Have the user open their own SSH terminal and run:

```bash
ssh cloudru-server
cd /opt/parking_monitor
sudo ./scripts/configure-secrets.sh
```

They enter the replacement Telegram token and Discord bot token at non-echoing prompts. Verify only file owner/mode and variable names; never print values.

- [ ] **Step 9: Start and verify all services**

Restart notifier, Telegram bot, Discord bot, then monitor. Verify active states and unchanged monitoring statistics. Confirm Telegram reports no webhook, Discord registers three guild commands, and logs contain no authentication/configuration errors.

- [ ] **Step 10: Send a clearly labeled test event**

Insert one `test_notification` event through an administrative Python entry point that does not change `state.json` checks, hits, `last_enabled`, or `last_check`. Confirm one `delivered` row per channel and visible test messages in Telegram and Discord.

- [ ] **Step 11: Verify commands and audit**

The user runs Telegram `/status` and Discord `/status`, `/stats`, and `/interval`. Query SQLite for channel/status/attempt timestamps only, scan logs/database errors for token fragments, and capture final service revisions/start times.

- [ ] **Step 12: Rollback criterion**

If the monitor or notifier cannot start, stop only the new notifier/Discord services, restore the previous Git revision with `git switch --detach <recorded-revision>` only after preserving the database and state files, restart the former monitor/Telegram units, and report which verification gate failed. Never delete the SQLite database or secret file during rollback.
