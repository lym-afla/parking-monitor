import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path, PureWindowsPath


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SETUP_SCRIPT = PROJECT_ROOT / "scripts" / "setup-service.sh"
MANAGEMENT_SCRIPT = PROJECT_ROOT / "scripts" / "manage-parking-monitor.sh"
MONITOR_SCRIPT = PROJECT_ROOT / "scripts" / "monitor.sh"
DEPLOYMENT_RUNBOOK = PROJECT_ROOT / "DEPLOYMENT.md"
SERVICE_NAMES = (
    "parking-service-monitor",
    "parking-service-notifier",
    "parking-service-bot",
    "parking-service-discord",
)


def _git_bash() -> str:
    candidates = (
        shutil.which("bash"),
        r"C:\Program Files\Git\bin\bash.exe",
        r"C:\Program Files\Git\usr\bin\bash.exe",
    )
    for candidate in candidates:
        if candidate and "system32" not in candidate.lower() and Path(candidate).is_file():
            return candidate
    raise RuntimeError("Git Bash is required to test the service renderer")


def _bash_path(path: Path) -> str:
    if os.name != "nt":
        return str(path)
    windows_path = PureWindowsPath(path)
    drive = windows_path.drive.rstrip(":").lower()
    tail = "/".join(windows_path.parts[1:])
    return f"/{drive}/{tail}"


class RepositoryModeTests(unittest.TestCase):
    def test_service_scripts_are_committed_executable(self):
        tracked_scripts = (
            "scripts/configure-secrets.sh",
            "scripts/setup-service.sh",
            "scripts/manage-parking-monitor.sh",
            "scripts/monitor.sh",
        )
        result = subprocess.run(
            ["git", "ls-files", "-s", "--", *tracked_scripts],
            cwd=PROJECT_ROOT,
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)

        tracked_modes = {}
        for line in result.stdout.splitlines():
            metadata, path = line.split("\t", 1)
            tracked_modes[path] = metadata.split()[0]

        self.assertEqual(
            tracked_modes,
            {script: "100755" for script in tracked_scripts},
        )

    def test_monitor_helper_uses_current_four_service_and_log_layout(self):
        source = MONITOR_SCRIPT.read_text(encoding="utf-8")
        for component in ("monitor", "notifier", "bot", "discord"):
            self.assertIn(f'"$SERVICE_NAME-{component}"', source)
        for log_name in ("monitor.log", "notifier.log", "telegram.log", "discord.log"):
            self.assertIn(log_name, source)
        self.assertIn("/var/lib/parking-monitor/data/state.json", source)
        self.assertIn("/var/lib/parking-monitor/ms-playwright", source)
        self.assertNotIn("$LOG_PATH/bot.log", source)
        self.assertNotIn("$LOG_PATH/parking.log", source)
        self.assertNotIn("hasattr(config", source)


class ServiceTemplateTests(unittest.TestCase):
    def setUp(self):
        self._temporary_directory = tempfile.TemporaryDirectory()
        self.output_directory = Path(self._temporary_directory.name)

        render_result = subprocess.run(
            [
                _git_bash(),
                _bash_path(SETUP_SCRIPT),
                "--render-only",
                _bash_path(self.output_directory),
            ],
            cwd=PROJECT_ROOT,
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(render_result.returncode, 0, render_result.stderr)

        self.units = {
            name: (self.output_directory / f"{name}.service").read_text(encoding="utf-8")
            for name in SERVICE_NAMES
        }

    def tearDown(self):
        self._temporary_directory.cleanup()

    def test_renders_exactly_four_application_services_without_a_secret_file(self):
        rendered_files = {path.name for path in self.output_directory.iterdir()}
        self.assertEqual(
            rendered_files,
            {f"{name}.service" for name in SERVICE_NAMES},
        )
        self.assertNotIn("parking-monitor.env", rendered_files)

    def test_units_load_root_managed_environment_without_embedding_tokens(self):
        all_units = "\n".join(self.units.values())

        self.assertNotIn("EnvironmentFile=", self.units["parking-service-monitor"])
        self.assertIn(
            "EnvironmentFile=-/etc/parking-monitor/notifier-telegram.env",
            self.units["parking-service-notifier"],
        )
        self.assertIn(
            "EnvironmentFile=-/etc/parking-monitor/notifier-discord.env",
            self.units["parking-service-notifier"],
        )
        self.assertNotIn("telegram-bot.env", self.units["parking-service-notifier"])
        self.assertNotIn("discord-bot.env", self.units["parking-service-notifier"])
        self.assertIn(
            "EnvironmentFile=-/etc/parking-monitor/telegram-bot.env",
            self.units["parking-service-bot"],
        )
        self.assertNotIn("discord-bot.env", self.units["parking-service-bot"])
        self.assertIn(
            "EnvironmentFile=-/etc/parking-monitor/discord-bot.env",
            self.units["parking-service-discord"],
        )
        self.assertNotIn("telegram-bot.env", self.units["parking-service-discord"])

        self.assertNotIn("TELEGRAM_BOT_TOKEN=", all_units)
        self.assertNotIn("DISCORD_BOT_TOKEN=", all_units)
        self.assertNotIn("/opt/parking_monitor/.env", all_units)

    def test_units_run_as_four_distinct_service_users(self):
        expected_users = {
            "parking-service-monitor": "parking-monitor-monitor",
            "parking-service-notifier": "parking-monitor-notifier",
            "parking-service-bot": "parking-monitor-telegram",
            "parking-service-discord": "parking-monitor-discord",
        }

        for name, user in expected_users.items():
            with self.subTest(service=name):
                self.assertIn(f"User={user}", self.units[name])
                self.assertIn("Group=parking-monitor", self.units[name])

    def test_units_execute_each_component_with_restart_and_dedicated_logs(self):
        expected = {
            "parking-service-monitor": ("monitor.py", "monitor.log"),
            "parking-service-notifier": ("notifier.py", "notifier.log"),
            "parking-service-bot": ("telegram_bot.py", "telegram.log"),
            "parking-service-discord": ("discord_bot.py", "discord.log"),
        }

        for name, (program, log_name) in expected.items():
            with self.subTest(service=name):
                unit = self.units[name]
                self.assertIn(
                    f"ExecStart=/opt/parking_monitor/venv/bin/python /opt/parking_monitor/{program}",
                    unit,
                )
                self.assertIn("Restart=on-failure", unit)
                self.assertIn("RestartSec=10", unit)
                self.assertIn(
                    f"StandardOutput=append:/var/log/parking-monitor/{log_name}",
                    unit,
                )
                self.assertIn(
                    f"StandardError=append:/var/log/parking-monitor/{log_name}",
                    unit,
                )

    def test_services_write_only_runtime_data_not_checkout_or_browser_tree(self):
        for name, unit in self.units.items():
            with self.subTest(service=name):
                self.assertIn("ProtectSystem=strict", unit)
                self.assertIn("NoNewPrivileges=true", unit)
                self.assertIn("ReadWritePaths=/var/lib/parking-monitor/data", unit)
                self.assertNotIn("ReadWritePaths=/opt/parking_monitor", unit)
                self.assertNotIn(
                    "ReadWritePaths=/var/lib/parking-monitor/ms-playwright",
                    unit,
                )
                self.assertIn("UMask=0007", unit)

    def test_monitor_uses_explicit_read_only_playwright_browser_location(self):
        monitor_unit = self.units["parking-service-monitor"]
        other_units = "\n".join(
            unit
            for name, unit in self.units.items()
            if name != "parking-service-monitor"
        )

        self.assertIn(
            "Environment=PLAYWRIGHT_BROWSERS_PATH=/var/lib/parking-monitor/ms-playwright",
            monitor_unit,
        )
        self.assertNotIn("PLAYWRIGHT_BROWSERS_PATH", other_units)


class ServiceManagementTests(unittest.TestCase):
    def _run_sourced_script(
        self,
        script: Path,
        shell_body: str,
        *,
        extra_environment: dict[str, str] | None = None,
    ) -> tuple[subprocess.CompletedProcess[str], list[str]]:
        with tempfile.TemporaryDirectory() as temporary_directory:
            call_log = Path(temporary_directory) / "calls.log"
            environment = os.environ.copy()
            environment["CALL_LOG"] = _bash_path(call_log)
            environment["TEST_ROOT"] = _bash_path(Path(temporary_directory))
            if extra_environment:
                environment.update(extra_environment)
            source_command = (
                f"source '{_bash_path(script)}' help >/dev/null\n"
                f"{shell_body}"
            )
            result = subprocess.run(
                [_git_bash(), "-c", source_command],
                cwd=PROJECT_ROOT,
                env=environment,
                check=False,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
            )
            calls = (
                call_log.read_text(encoding="utf-8").splitlines()
                if call_log.exists()
                else []
            )
            return result, calls

    def _run_sourced_command(self, shell_body: str) -> list[str]:
        result, calls = self._run_sourced_script(MANAGEMENT_SCRIPT, shell_body)
        self.assertEqual(result.returncode, 0, result.stderr)
        return calls

    def test_stop_manages_all_four_services(self):
        calls = self._run_sourced_command(
            """
check_root_for_command() { :; }
check_service_exists() { :; }
status_service() { :; }
systemctl() { printf '%s\\n' "$*" >> "$CALL_LOG"; return 0; }
stop_service >/dev/null
"""
        )
        stopped = {line.removeprefix("stop ") for line in calls if line.startswith("stop ")}
        self.assertEqual(stopped, set(SERVICE_NAMES))

    def test_missing_telegram_config_does_not_block_discord_or_monitor_start(self):
        result, calls = self._run_sourced_script(
            MANAGEMENT_SCRIPT,
            r'''
check_root_for_command() { :; }
check_service_exists() { :; }
status_service() { :; }
CONFIG_DIR="$TEST_ROOT/config"
mkdir -p "$CONFIG_DIR"
touch "$CONFIG_DIR/discord-bot.env"
declare -A started
systemctl() {
    if [ "$1" = is-active ]; then
        local unit="$3"
        if [ "$unit" = parking-service-bot ]; then return 1; fi
        [ "${started[$unit]:-}" = yes ]
        return
    fi
    if [ "$1" = start ]; then
        started[$2]=yes
        printf 'start %s\n' "$2" >> "$CALL_LOG"
        return 0
    fi
    return 0
}
sleep() { :; }
start_service >/dev/null
''',
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            {call.removeprefix("start ") for call in calls},
            set(SERVICE_NAMES),
        )

    def test_default_logs_read_all_four_dedicated_append_files(self):
        result, calls = self._run_sourced_script(
            MANAGEMENT_SCRIPT,
            """
check_root_for_command() { :; }
LOG_DIR="$TEST_ROOT/logs"
mkdir -p "$LOG_DIR"
touch "$LOG_DIR/monitor.log" "$LOG_DIR/notifier.log" \\
      "$LOG_DIR/telegram.log" "$LOG_DIR/discord.log"
tail() { printf 'tail %s\\n' "$*" >> "$CALL_LOG"; return 0; }
journalctl() { printf 'journalctl %s\\n' "$*" >> "$CALL_LOG"; return 0; }
show_logs -t service >/dev/null
"""
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        logged_text = "\n".join(calls)
        for log_name in ("monitor.log", "notifier.log", "telegram.log", "discord.log"):
            self.assertIn(log_name, logged_text)
        self.assertNotIn("journalctl ", logged_text)

    def test_component_logs_read_the_component_append_file(self):
        expected = {
            "monitor": "monitor.log",
            "notifier": "notifier.log",
            "bot": "telegram.log",
            "discord": "discord.log",
        }
        for component, log_name in expected.items():
            with self.subTest(component=component):
                result, calls = self._run_sourced_script(
                    MANAGEMENT_SCRIPT,
                    f"""
check_root_for_command() {{ :; }}
LOG_DIR="$TEST_ROOT/logs"
mkdir -p "$LOG_DIR"
touch "$LOG_DIR/{log_name}"
tail() {{ printf 'tail %s\\n' "$*" >> "$CALL_LOG"; return 0; }}
journalctl() {{ printf 'journalctl %s\\n' "$*" >> "$CALL_LOG"; return 0; }}
show_logs -t {component} >/dev/null
""",
                )
                self.assertEqual(result.returncode, 0, result.stderr)
                logged_text = "\n".join(calls)
                self.assertIn(log_name, logged_text)
                self.assertNotIn("journalctl ", logged_text)

    def test_setup_keeps_checkout_root_owned_and_scopes_runtime_group_write(self):
        result, calls = self._run_sourced_script(
            SETUP_SCRIPT,
            """
APP_DIR="$TEST_ROOT/app"
VENV_DIR="$APP_DIR/venv"
RUNTIME_ROOT="$TEST_ROOT/runtime"
DATA_DIR="$RUNTIME_ROOT/data"
PLAYWRIGHT_BROWSERS_PATH="$RUNTIME_ROOT/ms-playwright"
LOG_DIR="$TEST_ROOT/logs"
CONFIG_DIR="$TEST_ROOT/config"
RUNTIME_GROUP=parking-monitor
mkdir -p "$VENV_DIR/bin" "$APP_DIR/.git" "$APP_DIR/scripts/nested" \
         "$APP_DIR/tests/nested" "$APP_DIR/docs/nested"
touch "$VENV_DIR/bin/pip" "$APP_DIR/scripts/manage-parking-monitor.sh" \
      "$APP_DIR/scripts/nested/helper.sh" "$APP_DIR/tests/nested/test_helper.py" \
      "$APP_DIR/docs/nested/runbook.md"
chown() { printf 'chown %s\\n' "$*" >> "$CALL_LOG"; return 0; }
chmod() { printf 'chmod %s\\n' "$*" >> "$CALL_LOG"; return 0; }
git() { return 0; }
setup_permissions >/dev/null
""",
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(
            any(
                line.startswith("chown -R root:root ")
                and line.endswith("/app")
                for line in calls
            ),
            calls,
        )
        self.assertTrue(
            any(
                line.startswith("chown root:parking-monitor ")
                and "/runtime/data" in line
                for line in calls
            ),
            calls,
        )
        self.assertTrue(
            any(
                line.startswith("chmod -R go-w ") and line.endswith("/app")
                for line in calls
            ),
            calls,
        )

    def test_setup_refuses_legacy_runtime_migration_while_service_is_active(self):
        result, calls = self._run_sourced_script(
            SETUP_SCRIPT,
            """
APP_DIR="$TEST_ROOT/app"
RUNTIME_ROOT="$TEST_ROOT/runtime"
DATA_DIR="$RUNTIME_ROOT/data"
PLAYWRIGHT_BROWSERS_PATH="$RUNTIME_ROOT/ms-playwright"
LOG_DIR="$TEST_ROOT/logs"
CONFIG_DIR="$TEST_ROOT/config"
mkdir -p "$APP_DIR"
touch "$APP_DIR/notifications.sqlite3"
systemctl() {
    printf 'systemctl %s\\n' "$*" >> "$CALL_LOG"
    if [ "$1" = is-active ] && [ "$3" = parking-service-notifier ]; then
        return 0
    fi
    return 1
}
chown() { :; }
chmod() { :; }
if setup_permissions >/dev/null; then exit 41; fi
test -f "$APP_DIR/notifications.sqlite3"
test ! -e "$DATA_DIR/notifications.sqlite3"
""",
        )
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        self.assertTrue(any("parking-service-notifier" in call for call in calls))

    def test_management_command_is_installed_as_root_owned_copy_not_symlink(self):
        result, calls = self._run_sourced_script(
            SETUP_SCRIPT,
            """
APP_DIR="$TEST_ROOT/app"
SYMLINK_PATH="$TEST_ROOT/bin/parking-monitor"
mkdir -p "$APP_DIR/scripts" "$(dirname "$SYMLINK_PATH")"
touch "$APP_DIR/scripts/manage-parking-monitor.sh"
chmod +x "$APP_DIR/scripts/manage-parking-monitor.sh"
install() {
    printf 'install %s\\n' "$*" >> "$CALL_LOG"
    command cp "$APP_DIR/scripts/manage-parking-monitor.sh" "$SYMLINK_PATH"
    command chmod +x "$SYMLINK_PATH"
}
create_management_command >/dev/null
""",
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(len(calls), 1)
        self.assertIn("-o root -g root -m 0755", calls[0])
        self.assertIn("manage-parking-monitor.sh", calls[0])

    def test_setup_creates_four_non_login_users_in_the_runtime_group(self):
        result, calls = self._run_sourced_script(
            SETUP_SCRIPT,
            """
getent() { return 1; }
id() { return 1; }
groupadd() { printf 'groupadd %s\\n' "$*" >> "$CALL_LOG"; }
useradd() { printf 'useradd %s\\n' "$*" >> "$CALL_LOG"; }
create_service_identities >/dev/null
""",
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("groupadd --system parking-monitor", calls)
        user_calls = [call for call in calls if call.startswith("useradd ")]
        self.assertEqual(len(user_calls), 4)
        for user in (
            "parking-monitor-monitor",
            "parking-monitor-notifier",
            "parking-monitor-telegram",
            "parking-monitor-discord",
        ):
            self.assertTrue(any(call.endswith(f" {user}") for call in user_calls))
        for call in user_calls:
            self.assertIn("--shell /usr/sbin/nologin", call)
            self.assertIn("--gid parking-monitor", call)

    def test_setup_stops_on_stage_failure_without_false_success(self):
        result, _ = self._run_sourced_script(
            SETUP_SCRIPT,
            """
check_root() { :; }
validate_environment() { :; }
create_service_identities() { :; }
setup_permissions() { :; }
install_dependencies() { :; }
create_service_file() { return 23; }
create_management_command() { printf 'management-command-called\\n'; }
enable_service() { printf 'enable-called\\n'; }
show_summary() { printf 'summary-called\\n'; }
main
""",
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn("Setup completed successfully", result.stdout)
        self.assertNotIn("management-command-called", result.stdout)

    def test_setup_removes_obsolete_aggregate_after_stop_and_disable(self):
        result, calls = self._run_sourced_script(
            SETUP_SCRIPT,
            """
SYSTEMD_DIR="$TEST_ROOT/systemd"
mkdir -p "$SYSTEMD_DIR"
touch "$SYSTEMD_DIR/parking-service.service"
systemctl() {
    printf '%s\\n' "$*" >> "$CALL_LOG"
    if [ "$1" = is-active ]; then return 0; fi
    return 0
}
remove_legacy_aggregate_service
test ! -e "$SYSTEMD_DIR/parking-service.service"
""",
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("stop parking-service.service", calls)
        self.assertIn("disable parking-service.service", calls)

    def test_update_helpers_stop_and_restore_every_active_service(self):
        calls = self._run_sourced_command(
            """
check_service_exists() { :; }
systemctl() {
    printf '%s\\n' "$*" >> "$CALL_LOG"
    return 0
}
capture_and_stop_running_services
restore_running_services
"""
        )
        for service_name in SERVICE_NAMES:
            self.assertIn(f"stop {service_name}", calls)
            self.assertIn(f"start {service_name}", calls)

    def test_update_transaction_stages_before_stop_and_snapshots_after_all_stops(self):
        calls = self._run_sourced_command(
            """
prepare_update_stage() { printf 'stage\\n' >> "$CALL_LOG"; }
capture_and_stop_running_services() { printf 'stop-all\\n' >> "$CALL_LOG"; }
create_runtime_snapshot() { printf 'snapshot\\n' >> "$CALL_LOG"; }
cutover_staged_update() { printf 'cutover\\n' >> "$CALL_LOG"; }
restore_running_services() { printf 'restart\\n' >> "$CALL_LOG"; }
finalize_update() { printf 'finalize\\n' >> "$CALL_LOG"; }
cleanup_update_stage() { printf 'cleanup\\n' >> "$CALL_LOG"; }
run_update_transaction old-revision new-revision /stage /backup
"""
        )

        self.assertEqual(
            calls,
            [
                "stage",
                "stop-all",
                "snapshot",
                "cutover",
                "restart",
                "finalize",
                "cleanup",
            ],
        )

    def test_update_stage_fails_closed_on_dependency_install_error(self):
        result, calls = self._run_sourced_script(
            MANAGEMENT_SCRIPT,
            """
stage="$TEST_ROOT/stage"
mkdir -p "$stage/venv/bin" "$stage/source/scripts"
cat > "$stage/venv/bin/python" <<'SH'
#!/bin/sh
printf 'python %s\\n' "$*" >> "$CALL_LOG"
case "$*" in
    *"pip install -r"*) exit 23 ;;
esac
exit 0
SH
chmod +x "$stage/venv/bin/python"
cat > "$stage/source/scripts/setup-service.sh" <<'SH'
#!/bin/sh
printf 'render reached\\n' >> "$CALL_LOG"
exit 0
SH
chmod +x "$stage/source/scripts/setup-service.sh"
git() { printf 'git %s\\n' "$*" >> "$CALL_LOG"; return 0; }
python3() { return 0; }
bash() { printf 'bash reached\\n' >> "$CALL_LOG"; return 0; }
systemd-analyze() { printf 'verify reached\\n' >> "$CALL_LOG"; return 0; }
chown() { printf 'chown reached\\n' >> "$CALL_LOG"; return 0; }
if prepare_update_stage old new "$stage"; then exit 41; fi
""",
        )
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        self.assertFalse(any("render reached" in call for call in calls), calls)
        self.assertFalse(any("chown reached" in call for call in calls), calls)

    def test_runtime_snapshot_fails_closed_when_a_snapshot_copy_fails(self):
        result, _ = self._run_sourced_script(
            MANAGEMENT_SCRIPT,
            """
DATA_DIR="$TEST_ROOT/data"
STATE_PATH="$DATA_DIR/state.json"
DATABASE_PATH="$DATA_DIR/notifications.sqlite3"
SYSTEMD_DIR="$TEST_ROOT/systemd"
mkdir -p "$DATA_DIR" "$SYSTEMD_DIR"
touch "$STATE_PATH"
systemctl() {
    case "$1" in
        is-active) return 1 ;;
        is-enabled) return 0 ;;
    esac
    return 0
}
install() { command mkdir -p "${@: -1}"; }
cp() { return 23; }
if create_runtime_snapshot "$TEST_ROOT/backup"; then exit 41; fi
""",
        )
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)

    def test_partial_stop_failure_restarts_services_stopped_for_update(self):
        result, calls = self._run_sourced_script(
            MANAGEMENT_SCRIPT,
            """
prepare_update_stage() { printf 'stage\\n' >> "$CALL_LOG"; }
capture_and_stop_running_services() {
    RUNNING_BEFORE_UPDATE=(monitor notifier)
    printf 'partial-stop\\n' >> "$CALL_LOG"
    return 23
}
restore_running_services() { printf 'restore\\n' >> "$CALL_LOG"; }
cleanup_update_stage() { printf 'cleanup\\n' >> "$CALL_LOG"; }
if run_update_transaction old new /stage /backup; then exit 41; fi
""",
        )
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        self.assertEqual(calls, ["stage", "partial-stop", "restore", "cleanup"])

    def test_failed_cutover_rolls_back_revision_environment_and_runtime_snapshot(self):
        calls = self._run_sourced_command(
            """
prepare_update_stage() { printf 'stage\\n' >> "$CALL_LOG"; }
capture_and_stop_running_services() { printf 'stop-all\\n' >> "$CALL_LOG"; }
create_runtime_snapshot() { printf 'snapshot\\n' >> "$CALL_LOG"; }
cutover_staged_update() { printf 'cutover-failed\\n' >> "$CALL_LOG"; return 1; }
rollback_update() { printf 'rollback %s\\n' "$*" >> "$CALL_LOG"; }
cleanup_update_stage() { printf 'cleanup\\n' >> "$CALL_LOG"; }
if run_update_transaction old-revision new-revision /stage /backup; then exit 41; fi
"""
        )

        self.assertEqual(
            calls,
            [
                "stage",
                "stop-all",
                "snapshot",
                "cutover-failed",
                "rollback old-revision /backup",
                "cleanup",
            ],
        )

    def test_fast_forward_checkout_uses_pull_ff_only(self):
        calls = self._run_sourced_command(
            """
git() {
    printf 'git %s\\n' "$*" >> "$CALL_LOG"
    if [ "$1" = rev-parse ]; then printf 'new-revision\\n'; fi
}
BRANCH_NAME=main
fast_forward_checkout new-revision
"""
        )

        self.assertIn("git pull --ff-only origin main", calls)
        self.assertIn("git rev-parse HEAD", calls)

    def test_runtime_snapshot_refuses_backup_while_any_database_user_is_active(self):
        result, calls = self._run_sourced_script(
            MANAGEMENT_SCRIPT,
            """
CONFIG_DIR="$TEST_ROOT/config"
mkdir -p "$CONFIG_DIR"
touch "$CONFIG_DIR/telegram-bot.env" "$CONFIG_DIR/discord-bot.env"
systemctl() {
    printf 'systemctl %s\\n' "$*" >> "$CALL_LOG"
    if [ "$1" = is-active ] && [ "$3" = parking-service-bot ]; then return 0; fi
    if [ "$1" = is-active ]; then return 1; fi
    return 0
}
install() { printf 'install %s\\n' "$*" >> "$CALL_LOG"; }
if create_runtime_snapshot "$TEST_ROOT/backup"; then exit 42; fi
""",
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(any(call.startswith("install ") for call in calls))
        self.assertTrue(any("parking-service-bot" in call for call in calls))

    def test_runtime_snapshot_uses_sqlite_backup_with_uncheckpointed_wal(self):
        result, _ = self._run_sourced_script(
            MANAGEMENT_SCRIPT,
            r'''
set -e
VENV_DIR="$TEST_ROOT/venv"
DATA_DIR="$TEST_ROOT/data"
DATABASE_PATH="$DATA_DIR/notifications.sqlite3"
STATE_PATH="$DATA_DIR/state.json"
SYSTEMD_DIR="$TEST_ROOT/systemd"
mkdir -p "$VENV_DIR/bin" "$DATA_DIR" "$SYSTEMD_DIR"
cat > "$VENV_DIR/bin/python" <<EOF
#!/bin/sh
exec "$PYTHON_EXE" "\$@"
EOF
chmod +x "$VENV_DIR/bin/python"
for component in "${SERVICE_COMPONENTS[@]}"; do
    printf '[Unit]\nDescription=test\n' \
        > "$SYSTEMD_DIR/${SERVICE_NAME}-${component}.service"
done
systemctl() {
    if [ "$1" = is-active ]; then return 1; fi
    if [ "$1" = is-enabled ]; then printf 'enabled\n'; fi
    return 0
}
install() {
    local destination="${@: -1}"
    mkdir -p "$destination"
}
"$VENV_DIR/bin/python" - "$DATABASE_PATH" "$TEST_ROOT/writer-ready" <<'PY' &
import pathlib
import sqlite3
import sys
import time

connection = sqlite3.connect(sys.argv[1])
connection.execute("PRAGMA journal_mode=WAL")
connection.execute("PRAGMA wal_autocheckpoint=0")
connection.execute("CREATE TABLE evidence(value INTEGER)")
connection.execute("INSERT INTO evidence VALUES (42)")
connection.commit()
pathlib.Path(sys.argv[2]).touch()
time.sleep(10)
PY
writer_pid=$!
while [ ! -f "$TEST_ROOT/writer-ready" ]; do sleep 0.05; done
create_runtime_snapshot "$TEST_ROOT/backup"
kill "$writer_pid" 2>/dev/null || true
wait "$writer_pid" 2>/dev/null || true
value=$("$VENV_DIR/bin/python" - "$TEST_ROOT/backup/notifications.sqlite3" <<'PY'
import sqlite3
import sys
print(sqlite3.connect(sys.argv[1]).execute("SELECT value FROM evidence").fetchone()[0])
PY
)
test "$value" = 42
''',
            extra_environment={"PYTHON_EXE": _bash_path(Path(sys.executable))},
        )

        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)

    def test_restore_runtime_snapshot_preserves_recorded_absence(self):
        result, _ = self._run_sourced_script(
            MANAGEMENT_SCRIPT,
            """
DATA_DIR="$TEST_ROOT/data"
STATE_PATH="$DATA_DIR/state.json"
DATABASE_PATH="$DATA_DIR/notifications.sqlite3"
APP_DIR="$TEST_ROOT/app"
mkdir -p "$DATA_DIR" "$APP_DIR" "$TEST_ROOT/backup"
touch "$TEST_ROOT/backup/state.absent" \
      "$TEST_ROOT/backup/database.absent" \
      "$STATE_PATH" "$DATABASE_PATH" \
      "$DATABASE_PATH-wal" "$DATABASE_PATH-shm"
restore_runtime_snapshot "$TEST_ROOT/backup"
test ! -e "$STATE_PATH"
test ! -e "$DATABASE_PATH"
test ! -e "$DATABASE_PATH-wal"
test ! -e "$DATABASE_PATH-shm"
""",
        )
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)

    def test_snapshot_restores_legacy_checkout_data_after_migration_rollback(self):
        result, _ = self._run_sourced_script(
            MANAGEMENT_SCRIPT,
            r'''
set -e
APP_DIR="$TEST_ROOT/app"
VENV_DIR="$TEST_ROOT/venv"
DATA_DIR="$TEST_ROOT/data"
STATE_PATH="$DATA_DIR/state.json"
DATABASE_PATH="$DATA_DIR/notifications.sqlite3"
SYSTEMD_DIR="$TEST_ROOT/systemd"
mkdir -p "$APP_DIR" "$VENV_DIR/bin" "$DATA_DIR" "$SYSTEMD_DIR"
cat > "$VENV_DIR/bin/python" <<EOF
#!/bin/sh
exec "$PYTHON_EXE" "\$@"
EOF
chmod +x "$VENV_DIR/bin/python"
printf '{"interval": 321}\n' > "$APP_DIR/state.json"
"$VENV_DIR/bin/python" - "$APP_DIR/notifications.sqlite3" <<'PY'
import sqlite3
import sys
connection = sqlite3.connect(sys.argv[1])
connection.execute("CREATE TABLE evidence(value INTEGER)")
connection.execute("INSERT INTO evidence VALUES (42)")
connection.commit()
connection.close()
PY
for component in "${SERVICE_COMPONENTS[@]}"; do
    printf '[Unit]\nDescription=test\n' \
        > "$SYSTEMD_DIR/${SERVICE_NAME}-${component}.service"
done
systemctl() {
    if [ "$1" = is-active ]; then return 1; fi
    return 0
}
install() {
    local destination="${@: -1}"
    mkdir -p "$destination"
}
create_runtime_snapshot "$TEST_ROOT/backup"
mv "$APP_DIR/state.json" "$STATE_PATH"
mv "$APP_DIR/notifications.sqlite3" "$DATABASE_PATH"
restore_runtime_snapshot "$TEST_ROOT/backup"
test -f "$APP_DIR/state.json"
test -f "$APP_DIR/notifications.sqlite3"
test ! -e "$STATE_PATH"
test ! -e "$DATABASE_PATH"
test "$("$VENV_DIR/bin/python" - "$APP_DIR/notifications.sqlite3" <<'PY'
import sqlite3
import sys
print(sqlite3.connect(sys.argv[1]).execute("SELECT value FROM evidence").fetchone()[0])
PY
)" = 42
''',
            extra_environment={"PYTHON_EXE": _bash_path(Path(sys.executable))},
        )
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)

    def test_service_test_helper_fails_when_any_current_service_is_inactive(self):
        result, calls = self._run_sourced_script(
            MANAGEMENT_SCRIPT,
            """
CONFIG_DIR="$TEST_ROOT/config"
mkdir -p "$CONFIG_DIR"
touch "$CONFIG_DIR/telegram-bot.env" "$CONFIG_DIR/discord-bot.env"
systemctl() {
    printf '%s\\n' "$*" >> "$CALL_LOG"
    if [ "$1" = is-active ] && [ "$3" = parking-service-discord ]; then
        return 1
    fi
    return 0
}
test_service_units
""",
        )
        self.assertNotEqual(result.returncode, 0)
        called_text = "\n".join(calls)
        for service_name in SERVICE_NAMES:
            self.assertIn(service_name, called_text)


class OperatorDocumentationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.runbook = DEPLOYMENT_RUNBOOK.read_text(encoding="utf-8")

    def test_runbook_uses_root_owned_transaction_not_service_user_git(self):
        update_section = self.runbook.split("## Safe update", 1)[1]
        update_section = update_section.split("## Channel recovery", 1)[0]

        self.assertIn("sudo parking-monitor update", update_section)
        self.assertIn("git pull --ff-only", update_section)
        self.assertIn("SQLite backup API", update_section)
        self.assertIn("restores the recorded Git revision", update_section)
        self.assertNotIn("sudo -u parking-monitor-", update_section)
        self.assertNotIn("sudo -u parking_user", self.runbook)

    def test_runbook_has_exact_four_unit_verification_command(self):
        self.assertIn(
            "sudo systemd-analyze verify /etc/systemd/system/"
            "parking-service-{monitor,notifier,bot,discord}.service",
            self.runbook,
        )
        self.assertIn("Linux TTY", self.runbook)


if __name__ == "__main__":
    unittest.main()
