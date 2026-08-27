import unittest

from config import RuntimeConfig, load_config


VALID_ENV = {
    "TELEGRAM_BOT_TOKEN": "telegram-test-token",
    "TELEGRAM_CHAT_ID": "404346140",
    "TELEGRAM_AUTHORIZED_USER_ID": "404346140",
    "DISCORD_BOT_TOKEN": "discord-test-token",
    "DISCORD_APPLICATION_ID": "1542514080810664018",
    "DISCORD_GUILD_ID": "1476852384826392628",
    "DISCORD_CHANNEL_ID": "1542511880659017792",
    "DISCORD_AUTHORIZED_USER_ID": "1138419941926776893",
}


class LoadConfigTests(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
