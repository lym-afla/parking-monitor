import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path, PureWindowsPath


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SETUP_SCRIPT = PROJECT_ROOT / "scripts" / "setup-service.sh"
MANAGEMENT_SCRIPT = PROJECT_ROOT / "scripts" / "manage-parking-monitor.sh"
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
    def _run_sourced_command(self, shell_body: str) -> list[str]:
        with tempfile.TemporaryDirectory() as temporary_directory:
            call_log = Path(temporary_directory) / "calls.log"
            environment = os.environ.copy()
            environment["CALL_LOG"] = _bash_path(call_log)
            source_command = (
                f"source '{_bash_path(MANAGEMENT_SCRIPT)}' help >/dev/null\n"
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
            self.assertEqual(result.returncode, 0, result.stderr)
            return call_log.read_text(encoding="utf-8").splitlines()

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

    def test_service_logs_include_all_four_services(self):
        calls = self._run_sourced_command(
            """
check_root_for_command() { :; }
journalctl() { printf '%s\\n' "$*" >> "$CALL_LOG"; return 0; }
show_logs -t service >/dev/null
"""
        )
        logged_text = "\n".join(calls)
        for service_name in SERVICE_NAMES:
            self.assertIn(f"-u {service_name}", logged_text)


if __name__ == "__main__":
    unittest.main()
