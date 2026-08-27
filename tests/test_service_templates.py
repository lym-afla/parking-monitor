import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path, PureWindowsPath


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SETUP_SCRIPT = PROJECT_ROOT / "scripts" / "setup-service.sh"
MANAGEMENT_SCRIPT = PROJECT_ROOT / "scripts" / "manage-parking-monitor.sh"
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

        for unit in self.units.values():
            self.assertIn("EnvironmentFile=/etc/parking-monitor.env", unit)

        self.assertNotIn("TELEGRAM_BOT_TOKEN=", all_units)
        self.assertNotIn("DISCORD_BOT_TOKEN=", all_units)
        self.assertNotIn("/opt/parking_monitor/.env", all_units)

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
                    f"StandardOutput=append:/opt/parking_monitor/logs/{log_name}",
                    unit,
                )
                self.assertIn(
                    f"StandardError=append:/opt/parking_monitor/logs/{log_name}",
                    unit,
                )

    def test_all_services_can_persist_shared_state_inside_the_app_boundary(self):
        for name, unit in self.units.items():
            with self.subTest(service=name):
                self.assertIn("ProtectSystem=strict", unit)
                self.assertIn("NoNewPrivileges=true", unit)
                self.assertIn("ReadWritePaths=/opt/parking_monitor", unit)


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

    def test_setup_adopts_entire_checkout_without_chmodding_tree_or_venv(self):
        result, calls = self._run_sourced_script(
            SETUP_SCRIPT,
            """
APP_DIR="$TEST_ROOT/app"
VENV_DIR="$APP_DIR/venv"
LOG_DIR="$APP_DIR/logs"
CONFIG_DIR="$APP_DIR/config"
APP_USER=parking_user
mkdir -p "$VENV_DIR/bin" "$APP_DIR/.git" "$APP_DIR/scripts/nested" \
         "$APP_DIR/tests/nested" "$APP_DIR/docs/nested"
touch "$VENV_DIR/bin/pip" "$APP_DIR/scripts/manage-parking-monitor.sh" \
      "$APP_DIR/scripts/nested/helper.sh" "$APP_DIR/tests/nested/test_helper.py" \
      "$APP_DIR/docs/nested/runbook.md"
chown() { printf 'chown %s\\n' "$*" >> "$CALL_LOG"; return 0; }
chmod() { printf 'chmod %s\\n' "$*" >> "$CALL_LOG"; return 0; }
sudo() {
    if [ "$1" = -u ]; then shift 2; fi
    "$@"
}
git() { return 0; }
setup_permissions >/dev/null
""",
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(
            any(
                line.startswith("chown -R parking_user:parking_user ")
                and line.endswith("/app")
                for line in calls
            ),
            calls,
        )
        for line in calls:
            if line.startswith("chmod "):
                self.assertNotIn("/venv", line)
                self.assertNotIn("/scripts", line)
                self.assertNotIn("/tests", line)
                self.assertNotIn("/docs", line)
                self.assertNotIn(" -R ", f" {line} ")

    def test_symlink_install_does_not_change_tracked_script_modes(self):
        setup_source = SETUP_SCRIPT.read_text(encoding="utf-8")
        symlink_function = setup_source.split("create_symlink() {", 1)[1]
        symlink_function = symlink_function.split("# Enable services", 1)[0]

        self.assertNotIn('chmod +x "$script_path"', symlink_function)
        self.assertNotIn('chmod +x "$SYMLINK_PATH"', symlink_function)
        self.assertIn('[ ! -x "$script_path" ]', symlink_function)

    def test_setup_stops_on_stage_failure_without_false_success(self):
        result, _ = self._run_sourced_script(
            SETUP_SCRIPT,
            """
check_root() { :; }
validate_environment() { :; }
detect_and_configure_user() { :; }
setup_permissions() { :; }
install_dependencies() { :; }
create_service_file() { return 23; }
create_symlink() { printf 'symlink-called\\n'; }
enable_service() { printf 'enable-called\\n'; }
show_summary() { printf 'summary-called\\n'; }
main
""",
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn("Setup completed successfully", result.stdout)
        self.assertNotIn("symlink-called", result.stdout)

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

    def test_service_test_helper_fails_when_any_current_service_is_inactive(self):
        result, calls = self._run_sourced_script(
            MANAGEMENT_SCRIPT,
            """
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

    def test_upgrade_uses_parking_user_and_install_units_only(self):
        deploy_section = self.runbook.split("## Deploy code without restarting", 1)[1]
        deploy_section = deploy_section.split("## Install credentials privately", 1)[0]

        self.assertIn("sudo -u parking_user git pull --ff-only", deploy_section)
        self.assertIn(
            "sudo -u parking_user venv/bin/python -m pip install",
            deploy_section,
        )
        self.assertIn(
            "sudo ./scripts/setup-service.sh --install-units",
            deploy_section,
        )
        self.assertNotIn("\nsudo ./scripts/setup-service.sh\n", deploy_section)

    def test_unit_backup_is_fail_closed_and_rollback_preflights_it(self):
        predeployment = self.runbook.split("## Pre-deployment record", 1)[1]
        predeployment = predeployment.split("## Deploy code without restarting", 1)[0]
        rollback = self.runbook.split("## Rollback", 1)[1]

        self.assertNotIn("2>/dev/null || true", predeployment)
        for unit in ("parking-service-monitor.service", "parking-service-bot.service"):
            self.assertIn(unit, predeployment)
            self.assertIn(f'test -s "$UNIT_BACKUP/{unit}"', predeployment)
            self.assertIn(f'test -r "$UNIT_BACKUP/{unit}"', predeployment)
            self.assertIn(f'test -s "$UNIT_BACKUP/{unit}"', rollback)
            self.assertIn(f'test -r "$UNIT_BACKUP/{unit}"', rollback)
        self.assertIn("if sudo test -e", predeployment)
        self.assertIn('test -s "$UNIT_BACKUP/enablement.txt"', predeployment)
        self.assertIn('test -r "$UNIT_BACKUP/enablement.txt"', predeployment)
        self.assertIn('test -s "$UNIT_BACKUP/enablement.txt"', rollback)
        self.assertIn('test -r "$UNIT_BACKUP/enablement.txt"', rollback)
        self.assertLess(
            rollback.index('test -s "$UNIT_BACKUP/enablement.txt"'),
            rollback.index("systemctl disable --now"),
        )
        self.assertLess(
            rollback.index('test -s "$UNIT_BACKUP/enablement.txt"'),
            rollback.index("sudo rm -f"),
        )


if __name__ == "__main__":
    unittest.main()
