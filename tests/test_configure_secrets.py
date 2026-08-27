import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path


INSTALLER = Path(__file__).resolve().parents[1] / "scripts" / "configure-secrets.sh"
GIT_BASH = Path(r"C:\Program Files\Git\bin\bash.exe")
BASH = str(GIT_BASH if GIT_BASH.exists() else shutil.which("bash") or "")
TELEGRAM_TOKEN = "123456789:abcdefghijklmnopqrstuvwxyzABCDE"
DISCORD_TOKEN = "ABCDEFGHIJKLMNOPQRSTUVWX.abcdef.ABCDEFGHIJKLMNOPQRSTUVWXY"


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
            destination = Path(directory) / "telegram-bot.env"
            destination.write_text("old configuration\n", encoding="utf-8")

            result = self.run_bash(
                '''
                    source "$1"
                    chown() { :; }
                    mv() {
                        if [ "$1" = -- ]; then shift; fi
                        test "$(dirname "$1")" = "$(dirname "$2")" || return 42
                        command mv "$@"
                    }
                    install_scoped_environment_file \
                        "$2" "TELEGRAM_BOT_TOKEN=$3"
                ''',
                destination,
                TELEGRAM_TOKEN,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn(f"TELEGRAM_BOT_TOKEN={TELEGRAM_TOKEN}", destination.read_text())

    def test_failed_atomic_install_preserves_existing_destination(self):
        with tempfile.TemporaryDirectory() as directory:
            destination = Path(directory) / "telegram-bot.env"
            destination.write_text("old configuration\n", encoding="utf-8")

            result = self.run_bash(
                '''
                    source "$1"
                    chown() { :; }
                    printf() { return 1; }
                    if install_scoped_environment_file \
                        "$2" "TELEGRAM_BOT_TOKEN=$3"; then
                        exit 1
                    fi
                    test "$(command cat "$2")" = "old configuration"
                    test -z "$(find "$(dirname "$2")" -maxdepth 1 -name "$(basename "$2").*" -print -quit)"
                ''',
                destination,
                TELEGRAM_TOKEN,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(destination.read_text(), "old configuration\n")

    def test_installs_four_service_scoped_environment_files(self):
        with tempfile.TemporaryDirectory() as directory:
            destination = Path(directory) / "parking-monitor"
            destination.mkdir()

            result = self.run_bash(
                '''
                    source "$1"
                    chown() { :; }
                    install_environment_files "$2" "$3" "$4"
                ''',
                destination,
                TELEGRAM_TOKEN,
                DISCORD_TOKEN,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            files = {
                path.name: path.read_text(encoding="utf-8")
                for path in destination.iterdir()
            }
            self.assertEqual(
                set(files),
                {
                    "telegram-bot.env",
                    "discord-bot.env",
                    "notifier-telegram.env",
                    "notifier-discord.env",
                },
            )
            self.assertIn("TELEGRAM_AUTHORIZED_USER_ID=404346140", files["telegram-bot.env"])
            self.assertNotIn("DISCORD_", files["telegram-bot.env"])
            self.assertIn("DISCORD_GUILD_ID=1476852384826392628", files["discord-bot.env"])
            self.assertNotIn("TELEGRAM_", files["discord-bot.env"])
            self.assertEqual(
                files["notifier-telegram.env"].splitlines(),
                [
                    f"TELEGRAM_BOT_TOKEN={TELEGRAM_TOKEN}",
                    "TELEGRAM_CHAT_ID=404346140",
                ],
            )
            self.assertEqual(
                files["notifier-discord.env"].splitlines(),
                [
                    f"DISCORD_BOT_TOKEN={DISCORD_TOKEN}",
                    "DISCORD_CHANNEL_ID=1542511880659017792",
                ],
            )

    def test_malformed_token_is_rejected_before_any_file_is_replaced(self):
        with tempfile.TemporaryDirectory() as directory:
            destination = Path(directory) / "parking-monitor"
            destination.mkdir()
            existing = destination / "telegram-bot.env"
            existing.write_text("old configuration\n", encoding="utf-8")
            malformed = "token-that-must-not-be-echoed"

            result = self.run_bash(
                '''
                    source "$1"
                    chown() { :; }
                    install_environment_files "$2" "$3" "$4"
                ''',
                destination,
                malformed,
                DISCORD_TOKEN,
            )

            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(existing.read_text(encoding="utf-8"), "old configuration\n")
            self.assertNotIn(malformed, result.stdout + result.stderr)

    def test_installer_exposes_no_aggregate_secret_file_helper(self):
        source = INSTALLER.read_text(encoding="utf-8")
        self.assertNotIn("install_environment_file()", source)


if __name__ == "__main__":
    unittest.main()
