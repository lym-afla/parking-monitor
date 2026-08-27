#!/usr/bin/env bash

set -euo pipefail
umask 077

if [[ "${EUID}" -ne 0 ]]; then
    echo "This script must be run as root." >&2
    exit 1
fi

read -r -s -p "Telegram bot token: " telegram_bot_token
echo
read -r -s -p "Discord bot token: " discord_bot_token
echo

if [[ -z "${telegram_bot_token}" || -z "${discord_bot_token}" ]]; then
    echo "TELEGRAM_BOT_TOKEN and DISCORD_BOT_TOKEN must not be empty." >&2
    exit 1
fi

temp_file=$(mktemp /etc/parking-monitor.env.XXXXXX)
trap 'rm -f "${temp_file}"' EXIT
discord_token_variable_name=DISCORD_BOT_TOKEN

cat > "${temp_file}" <<EOF
TELEGRAM_BOT_TOKEN=${telegram_bot_token}
TELEGRAM_CHAT_ID=404346140
TELEGRAM_AUTHORIZED_USER_ID=404346140
${discord_token_variable_name}=${discord_bot_token}
DISCORD_APPLICATION_ID=1542514080810664018
DISCORD_GUILD_ID=1476852384826392628
DISCORD_CHANNEL_ID=1542511880659017792
DISCORD_AUTHORIZED_USER_ID=1138419941926776893
EOF

install -o root -g root -m 600 "${temp_file}" /etc/parking-monitor.env
echo "Installed /etc/parking-monitor.env with configured variable names."
