import unittest

from config import (
    RUNTIME_DATA_DIR,
    STATE_FILE,
    RuntimeConfig,
    load_config,
    load_discord_command_config,
    load_discord_delivery_config,
    load_telegram_command_config,
    load_telegram_delivery_config,
)


VALID_ENV = {
    "TELEGRAM_BOT_TOKEN": "123456789:abcdefghijklmnopqrstuvwxyzABCDE",
    "TELEGRAM_CHAT_ID": "404346140",
    "TELEGRAM_AUTHORIZED_USER_ID": "404346140",
    "DISCORD_BOT_TOKEN": "ABCDEFGHIJKLMNOPQRSTUVWX.abcdef.ABCDEFGHIJKLMNOPQRSTUVWXY",
    "DISCORD_APPLICATION_ID": "1542514080810664018",
    "DISCORD_GUILD_ID": "1476852384826392628",
    "DISCORD_CHANNEL_ID": "1542511880659017792",
    "DISCORD_AUTHORIZED_USER_ID": "1138419941926776893",
}


class LoadConfigTests(unittest.TestCase):
    def test_mutable_runtime_paths_are_outside_the_checkout(self):
        self.assertEqual(RUNTIME_DATA_DIR, "/var/lib/parking-monitor/data")
        self.assertEqual(STATE_FILE, "/var/lib/parking-monitor/data/state.json")

    def test_load_config_requires_tokens(self):
        with self.assertRaisesRegex(ValueError, "TELEGRAM_BOT_TOKEN"):
            load_config({})

    def test_config_repr_redacts_tokens(self):
        cfg = load_config(VALID_ENV)

        rendered = repr(cfg)

        self.assertNotIn(VALID_ENV["TELEGRAM_BOT_TOKEN"], rendered)
        self.assertNotIn(VALID_ENV["DISCORD_BOT_TOKEN"], rendered)

    def test_rejects_non_numeric_authorization_ids(self):
        env = {**VALID_ENV, "DISCORD_AUTHORIZED_USER_ID": "not-a-number"}

        with self.assertRaisesRegex(ValueError, "DISCORD_AUTHORIZED_USER_ID"):
            load_config(env)

    def test_delivery_channels_load_independently_when_sibling_is_absent(self):
        telegram_only = {
            "TELEGRAM_BOT_TOKEN": VALID_ENV["TELEGRAM_BOT_TOKEN"],
            "TELEGRAM_CHAT_ID": VALID_ENV["TELEGRAM_CHAT_ID"],
        }
        discord_only = {
            "DISCORD_BOT_TOKEN": VALID_ENV["DISCORD_BOT_TOKEN"],
            "DISCORD_CHANNEL_ID": VALID_ENV["DISCORD_CHANNEL_ID"],
        }

        self.assertEqual(
            load_telegram_delivery_config(telegram_only).telegram_chat_id,
            404346140,
        )
        self.assertIsNone(load_discord_delivery_config(telegram_only))
        self.assertEqual(
            load_discord_delivery_config(discord_only).discord_channel_id,
            1542511880659017792,
        )
        self.assertIsNone(load_telegram_delivery_config(discord_only))

    def test_command_channels_load_independently_when_sibling_is_absent(self):
        telegram_env = {
            name: VALID_ENV[name]
            for name in (
                "TELEGRAM_BOT_TOKEN",
                "TELEGRAM_CHAT_ID",
                "TELEGRAM_AUTHORIZED_USER_ID",
            )
        }
        discord_env = {
            name: VALID_ENV[name]
            for name in (
                "DISCORD_BOT_TOKEN",
                "DISCORD_APPLICATION_ID",
                "DISCORD_GUILD_ID",
                "DISCORD_CHANNEL_ID",
                "DISCORD_AUTHORIZED_USER_ID",
            )
        }

        self.assertEqual(
            load_telegram_command_config(telegram_env).telegram_authorized_user_id,
            404346140,
        )
        self.assertIsNone(load_discord_command_config(telegram_env))
        self.assertEqual(
            load_discord_command_config(discord_env).discord_guild_id,
            1476852384826392628,
        )
        self.assertIsNone(load_telegram_command_config(discord_env))

    def test_partial_channel_configuration_is_rejected_without_echoing_values(self):
        secret = VALID_ENV["DISCORD_BOT_TOKEN"]

        with self.assertRaises(ValueError) as captured:
            load_discord_delivery_config({"DISCORD_BOT_TOKEN": secret})

        self.assertIn("DISCORD_CHANNEL_ID", str(captured.exception))
        self.assertNotIn(secret, str(captured.exception))

    def test_scoped_loaders_reject_malformed_token_shapes_without_echoing_values(self):
        malformed = "definitely-not-a-token"

        with self.assertRaises(ValueError) as telegram_error:
            load_telegram_delivery_config(
                {
                    "TELEGRAM_BOT_TOKEN": malformed,
                    "TELEGRAM_CHAT_ID": "404346140",
                }
            )
        with self.assertRaises(ValueError) as discord_error:
            load_discord_delivery_config(
                {
                    "DISCORD_BOT_TOKEN": malformed,
                    "DISCORD_CHANNEL_ID": "1542511880659017792",
                }
            )

        self.assertNotIn(malformed, str(telegram_error.exception))
        self.assertNotIn(malformed, str(discord_error.exception))


if __name__ == "__main__":
    unittest.main()
