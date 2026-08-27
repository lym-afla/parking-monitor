# Secure Dual-Channel Notifications Final Fix Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remove service write access to trusted code while making channel startup, state mutation, delivery recovery, and deployment rollback independently safe.

**Architecture:** Keep the checkout and virtual environment root-owned under `/opt/parking_monitor`; place shared mutable data under `/var/lib/parking-monitor/data`, Playwright browsers under the root-owned `/var/lib/parking-monitor/ms-playwright`, and logs under `/var/log/parking-monitor`. Four distinct service users share only the runtime group, receive narrowly scoped environment files, and use one locked JSON mutation layer plus the existing SQLite delivery store.

**Tech Stack:** Python 3.10+, `sqlite3`, `unittest`, Bash, systemd, Playwright, Git

**Spec:** `docs/superpowers/specs/2026-08-27-secure-dual-channel-notifications-design.md`, as corrected by `.superpowers/sdd/2026-08-27-secure-dual-channel-notifications/final-findings.md`

## Global Constraints

- Preserve Telegram user/chat `404346140` and Discord application/guild/channel/user IDs `1542514080810664018`, `1476852384826392628`, `1542511880659017792`, and `1138419941926776893`.
- No live tokens, token-bearing exception text, authorization headers, webhook URLs, or URL query strings may be logged or persisted.
- Do not push, deploy, access production, or use real credentials.
- Checkout, virtual environment, root-invoked scripts, installed units, and `/usr/local/bin/parking-monitor` remain root-owned and service-read-only.
- Mutable state/database files use `/var/lib/parking-monitor/data`; append logs use `/var/log/parking-monitor`.
- Each production behavior change begins with a focused failing test and an observed expected failure.

---

### Task 1: Scoped Configuration and Sanitized Startup

**Files:**
- Modify: `config.py`
- Modify: `telegram_bot.py`
- Modify: `discord_bot.py`
- Modify: `notifier.py`
- Modify: `scripts/configure-secrets.sh`
- Test: `tests/test_config.py`
- Test: `tests/test_configure_secrets.py`
- Test: `tests/test_telegram_bot.py`
- Test: `tests/test_discord_bot.py`

**Interfaces:**
- Produces: `load_telegram_delivery_config`, `load_discord_delivery_config`, `load_telegram_command_config`, and `load_discord_command_config`, each returning `None` only when that complete channel/service configuration is absent.
- Preserves: `RuntimeConfig` and `load_config` as compatibility composition for existing callers/tests.

- [ ] **Step 1: Write configuration and installer tests**

```python
def test_missing_telegram_does_not_disable_discord_delivery():
    assert load_telegram_delivery_config(DISCORD_ONLY_ENV) is None
    assert load_discord_delivery_config(DISCORD_ONLY_ENV).channel_id == 1542511880659017792

def test_installer_rejects_malformed_tokens_without_writing_destination():
    result = run_install("not-a-telegram-token", VALID_DISCORD_TOKEN)
    assert result.returncode != 0
    assert destination.read_text() == "old configuration\n"
```

- [ ] **Step 2: Run focused tests and verify RED**

Run: `D:\Project Y\Parking monitor\.venv\Scripts\python.exe -m unittest -v tests.test_config tests.test_configure_secrets tests.test_telegram_bot tests.test_discord_bot`

Expected: scoped loader imports/behavior and token-shape checks are absent; startup exceptions still escape.

- [ ] **Step 3: Implement minimal scoped loaders, non-echoing validation, and startup boundaries**

Implement token-shape validation without including values in errors. Telegram and Discord mains load only their command configuration, log only a generic message plus `type(exc).__name__`, and return a non-zero exit status without traceback text.

- [ ] **Step 4: Run focused tests and verify GREEN**

Run the Step 2 command; expected all selected tests pass.

---

### Task 2: Durable Channel State and Delivery Recovery

**Files:**
- Modify: `command_service.py`
- Modify: `notification_store.py`
- Modify: `notifier.py`
- Test: `tests/test_notification_store.py`
- Test: `tests/test_notifier.py`

**Interfaces:**
- Produces: `NotificationStore.set_channel_enabled(channel, enabled, now)` and `NotificationStore.requeue_failed(channel, now)`.
- Extends: `ChannelHealth` with `state` and `delivered_count` while retaining existing count fields.
- Changes: notifier adapters map contains configured channels only; missing channels remain durably `disabled` and are never claimed.

- [ ] **Step 1: Write failing lifecycle tests**

```python
def test_requeue_failed_makes_only_selected_channel_due_immediately():
    store.requeue_failed("telegram", NOW)
    assert store.claim_due("telegram", NOW) is not None
    assert store.claim_due("discord", NOW) is None

def test_disabled_channel_state_survives_store_reopen():
    store.set_channel_enabled("telegram", False, NOW)
    assert NotificationStore(db_path).health_summary()["telegram"].state == "disabled"
```

Add worker tests for a token-bearing unexpected exception, post-network retry time, five-second idle sleep, and periodic health summaries.

- [ ] **Step 2: Run store/notifier tests and verify RED**

Run: `D:\Project Y\Parking monitor\.venv\Scripts\python.exe -m unittest -v tests.test_notification_store tests.test_notifier`

Expected: missing store methods/health fields and current pre-network retry timestamp fail.

- [ ] **Step 3: Implement schema migration, startup requeue, disabled adapters, and safe worker timing**

Persist one row per supported channel, compute delivery counts, sanitize unexpected exceptions before persistence, timestamp results after adapter completion, and emit cadence/transition summaries without secret text.

- [ ] **Step 4: Run focused tests and verify GREEN**

Run the Step 2 command; expected all selected tests pass.

---

### Task 3: Cross-Process State Mutation and Command Detail

**Files:**
- Create: `state_store.py`
- Modify: `monitor.py`
- Modify: `command_service.py`
- Modify: `notification_store.py`
- Modify: `telegram_bot.py`
- Modify: `discord_bot.py`
- Test: `tests/test_state_store.py`
- Test: `tests/test_monitor_events.py`
- Test: `tests/test_command_service.py`

**Interfaces:**
- Produces: `read_json_state(path, defaults)` and `mutate_json_state(path, mutator, defaults)` using one stable adjacent lock file and atomic replacement.
- Changes: monitor result/error writes and command interval writes merge only their owned fields under the same lock.
- Extends: `StatusSnapshot.next_expected_check`; formatted stats expose per-channel delivered counts.

- [ ] **Step 1: Write synchronized contention and snapshot tests**

```python
def test_monitor_and_command_mutations_preserve_every_owned_field_under_contention():
    # Start synchronized worker processes against one state path.
    # Assert interval, checks, hits, last_check, error, and last_enabled all survive.
```

Add literal expectations for next expected check and delivered counts in both front ends.

- [ ] **Step 2: Run state/monitor/command/bot tests and verify RED**

Run: `D:\Project Y\Parking monitor\.venv\Scripts\python.exe -m unittest -v tests.test_state_store tests.test_monitor_events tests.test_command_service tests.test_telegram_bot tests.test_discord_bot`

Expected: shared lock module and extended snapshot fields are absent.

- [ ] **Step 3: Implement locked merge protocol and formatting**

Use `fcntl.flock` on POSIX and `msvcrt.locking` on Windows plus an in-process path mutex. Keep the lock across read, callback mutation, fsync, and `os.replace`; monitor callbacks update check-owned fields while `CommandService` updates only `interval`.

- [ ] **Step 4: Run focused tests and verify GREEN**

Run the Step 2 command; expected all selected tests pass, including synchronized contention.

---

### Task 4: Root-Owned Runtime and Scoped systemd Units

**Files:**
- Modify: `scripts/setup-service.sh`
- Modify: `scripts/configure-secrets.sh`
- Modify: `scripts/manage-parking-monitor.sh`
- Modify: `scripts/monitor.sh`
- Remove: `scripts/parking-service-monitor.service.fixed`
- Test: `tests/test_service_templates.py`
- Test: `tests/test_configure_secrets.py`

**Interfaces:**
- Produces: four distinct users, shared `parking-monitor` runtime group, root-owned code/venv/browser tree, service-specific environment files, and a copied `/usr/local/bin/parking-monitor`.
- Units: monitor receives no `EnvironmentFile`; Telegram and Discord receive only their command files; notifier receives only two delivery files; only `/var/lib/parking-monitor/data` is writable.

- [ ] **Step 1: Write failing rendered-unit and setup behavior tests**

```python
def test_units_use_distinct_users_and_scoped_environment_files():
    assert "User=parking-monitor-monitor" in monitor_unit
    assert "EnvironmentFile=" not in monitor_unit
    assert "notifier-telegram.env" in notifier_unit
    assert "discord-bot.env" not in notifier_unit

def test_installed_management_command_is_a_root_owned_copy():
    assert install_call == "install -o root -g root -m 0755 ... /usr/local/bin/parking-monitor"
```

Add exact assertions for runtime/log paths, Playwright environment/path, root checkout ownership, and absence of checkout write paths.

- [ ] **Step 2: Run service/installer tests and verify RED**

Run: `D:\Project Y\Parking monitor\.venv\Scripts\python.exe -m unittest -v tests.test_service_templates tests.test_configure_secrets`

Expected: current shared UID/env file, checkout/log paths, symlink, and service-owned code fail.

- [ ] **Step 3: Implement identities, paths, browser installation, and copied command**

Create directories with explicit owner/group/mode, install dependencies and Chromium as root, render exact scoped units, and never grant services write access to checkout/venv/scripts/browser files.

- [ ] **Step 4: Run focused tests and verify GREEN**

Run the Step 2 command; expected all selected tests pass.

---

### Task 5: Staged Fast-Forward Update and Coherent Rollback

**Files:**
- Modify: `scripts/manage-parking-monitor.sh`
- Modify: `scripts/setup-service.sh`
- Test: `tests/test_service_templates.py`

**Interfaces:**
- Produces: pre-cutover staged dependency/unit/test verification, `git pull --ff-only`, all-four-service stop before SQLite backup, SQLite backup API snapshots, virtual-environment/unit/browser rollback, recorded-revision restore, and coherent state/database restore.

- [ ] **Step 1: Write failing orchestration tests**

```python
def test_update_stages_and_verifies_before_pull_and_stops_all_database_users_before_backup():
    assert calls.index("verify-stage") < calls.index("git pull --ff-only origin main")
    assert max(stop_positions) < calls.index("sqlite-backup")

def test_failed_cutover_restores_revision_venv_units_and_runtime_snapshot():
    assert "git reset --hard <recorded>" in calls
    assert "restore-runtime-snapshot" in calls
```

- [ ] **Step 2: Run management tests and verify RED**

Run: `D:\Project Y\Parking monitor\.venv\Scripts\python.exe -m unittest -v tests.test_service_templates.ServiceManagementTests`

Expected: non-ff pull and no staged/rollback transaction fail.

- [ ] **Step 3: Implement minimal staged update transaction**

Reject a dirty checkout, stage the fetched revision in a detached worktree, build a fresh venv/browser set, run tests/compilation/Bash/unit verification, stop all services, take SQLite/state/unit/environment snapshots, cut over, and invoke one rollback function on every post-stop failure.

- [ ] **Step 4: Run focused tests and verify GREEN**

Run the Step 2 command; expected all selected tests pass.

---

### Task 6: Runbooks, Full Verification, Report, and Commit

**Files:**
- Modify: `DEPLOYMENT.md`
- Modify: `README.md`
- Modify: `SERVICE_ARCHITECTURE.md`
- Modify: `.superpowers/sdd/2026-08-27-secure-dual-channel-notifications/final-fix-report.md`

- [ ] **Step 1: Update operator documentation**

Document the four identities and environment files, `/var/lib` and `/var/log` paths, root ownership boundary, explicit target command `systemd-analyze verify /etc/systemd/system/parking-service-{monitor,notifier,bot,discord}.service`, staged update, coherent rollback, and Linux-only no-echo/signal cleanup test debt.

- [ ] **Step 2: Run exact verification**

```powershell
& 'D:\Project Y\Parking monitor\.venv\Scripts\python.exe' -m unittest discover -v
& 'D:\Project Y\Parking monitor\.venv\Scripts\python.exe' -m py_compile config.py state_store.py command_service.py notification_store.py notifier.py telegram_bot.py discord_bot.py monitor.py
& 'C:\Program Files\Git\bin\bash.exe' -n scripts/configure-secrets.sh scripts/setup-service.sh scripts/manage-parking-monitor.sh scripts/monitor.sh
& 'C:\Program Files\Git\bin\bash.exe' scripts/setup-service.sh --render-only <temporary-directory>
git ls-files -s -- scripts/configure-secrets.sh scripts/setup-service.sh scripts/manage-parking-monitor.sh scripts/monitor.sh
git diff --check
```

Run a synthetic mutation scan that injects fake Telegram/Discord token shapes into adapter exceptions and asserts neither captured logs nor SQLite values contain them. Run the tracked-tree credential scan separately and verify zero live-secret matches.

- [ ] **Step 3: Self-review against findings and spec**

Confirm every Critical/Important item has a test and implementation; list any deferred minor/Linux-only debt explicitly.

- [ ] **Step 4: Write evidence report and commit**

Record RED/GREEN commands and outputs, files, final verification, self-review, and residual concerns in `final-fix-report.md`, then stage all intended changes and commit without pushing.
