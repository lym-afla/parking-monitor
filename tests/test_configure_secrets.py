import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path


INSTALLER = Path(__file__).resolve().parents[1] / "scripts" / "configure-secrets.sh"
GIT_BASH = Path(r"C:\Program Files\Git\bin\bash.exe")
BASH = str(GIT_BASH if GIT_BASH.exists() else shutil.which("bash") or "")


@unittest.skipUnless(Path(BASH).exists(), "Bash is required to test the installer")
class ConfigureSecretsTests(unittest.TestCase):
    def run_bash(self, program, *args):
        return subprocess.run(
            [BASH, "-c", program, "bash", str(INSTALLER), *map(str, args)],
            capture_output=True,
            text=True,
            check=False,
        )

    def test_atomic_install_renames_same_directory_staging_file(self):
        with tempfile.TemporaryDirectory() as directory:
            destination = Path(directory) / "parking-monitor.env"
            destination.write_text("old configuration\n", encoding="utf-8")

            result = self.run_bash(
                '''
                    source "$1"
                    chown() { :; }
                    mv() {
                        test "$(dirname "$1")" = "$(dirname "$2")" || return 42
                        command mv "$@"
                    }
                    install_environment_file "$2" "$3" "$4"
                ''',
                destination,
                "telegram-test-token",
                "discord-test-token",
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("TELEGRAM_BOT_TOKEN=telegram-test-token", destination.read_text())
            self.assertIn("DISCORD_BOT_TOKEN=discord-test-token", destination.read_text())

    def test_failed_atomic_install_preserves_existing_destination(self):
        with tempfile.TemporaryDirectory() as directory:
            destination = Path(directory) / "parking-monitor.env"
            destination.write_text("old configuration\n", encoding="utf-8")

            result = self.run_bash(
                '''
                    source "$1"
                    chown() { :; }
                    cat() { return 1; }
                    if install_environment_file "$2" "$3" "$4"; then
                        exit 1
                    fi
                    test "$(command cat "$2")" = "old configuration"
                    test -z "$(find "$(dirname "$2")" -maxdepth 1 -name "$(basename "$2").*" -print -quit)"
                ''',
                destination,
                "telegram-test-token",
                "discord-test-token",
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(destination.read_text(), "old configuration\n")


if __name__ == "__main__":
    unittest.main()
