#!/usr/bin/env bash

set -euo pipefail
umask 077

TELEGRAM_TOKEN_PATTERN='^[0-9]{6,12}:[A-Za-z0-9_-]{30,}$'
DISCORD_TOKEN_PATTERN='^[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{6,}\.[A-Za-z0-9_-]{20,}$'

validate_bot_tokens() {
    local telegram_bot_token=$1
    local discord_bot_token=$2

    if [[ ! "$telegram_bot_token" =~ $TELEGRAM_TOKEN_PATTERN ]]; then
        echo "Telegram bot token has an invalid shape." >&2
        return 1
    fi
    if [[ ! "$discord_bot_token" =~ $DISCORD_TOKEN_PATTERN ]]; then
        echo "Discord bot token has an invalid shape." >&2
        return 1
    fi
}

install_scoped_environment_file() {
    local destination=$1
    local contents=$2
    local temp_file

    temp_file=$(mktemp "${destination}.XXXXXX") || return 1
    if ! printf '%s\n' "$contents" > "$temp_file"; then
        rm -f -- "$temp_file"
        return 1
    fi
    if ! chown root:root "$temp_file" || ! chmod 600 "$temp_file"; then
        rm -f -- "$temp_file"
        return 1
    fi
    if ! mv -- "$temp_file" "$destination"; then
        rm -f -- "$temp_file"
        return 1
    fi
}

install_environment_files() {
    local destination_directory=$1
    local telegram_bot_token=$2
    local discord_bot_token=$3

    validate_bot_tokens "$telegram_bot_token" "$discord_bot_token" || return 1
    if [ ! -d "$destination_directory" ]; then
        echo "Configuration directory is missing." >&2
        return 1
    fi

    install_scoped_environment_file \
        "$destination_directory/telegram-bot.env" \
        "TELEGRAM_BOT_TOKEN=${telegram_bot_token}
TELEGRAM_CHAT_ID=404346140
TELEGRAM_AUTHORIZED_USER_ID=404346140" || return 1

    install_scoped_environment_file \
        "$destination_directory/discord-bot.env" \
        "DISCORD_BOT_TOKEN=${discord_bot_token}
DISCORD_APPLICATION_ID=1542514080810664018
DISCORD_GUILD_ID=1476852384826392628
DISCORD_CHANNEL_ID=1542511880659017792
DISCORD_AUTHORIZED_USER_ID=1138419941926776893" || return 1

    install_scoped_environment_file \
        "$destination_directory/notifier-telegram.env" \
        "TELEGRAM_BOT_TOKEN=${telegram_bot_token}
TELEGRAM_CHAT_ID=404346140" || return 1

    install_scoped_environment_file \
        "$destination_directory/notifier-discord.env" \
        "DISCORD_BOT_TOKEN=${discord_bot_token}
DISCORD_CHANNEL_ID=1542511880659017792" || return 1
}

main() {
    if [[ "${EUID}" -ne 0 ]]; then
        echo "This script must be run as root." >&2
        return 1
    fi

    local telegram_bot_token
    local discord_bot_token

    cleanup_secrets() {
        telegram_bot_token=
        discord_bot_token=
    }
    trap cleanup_secrets EXIT HUP INT TERM

    read -r -s -p "Telegram bot token: " telegram_bot_token
    echo
    read -r -s -p "Discord bot token: " discord_bot_token
    echo

    validate_bot_tokens "$telegram_bot_token" "$discord_bot_token" || return 1
    install -d -o root -g root -m 0700 /etc/parking-monitor
    install_environment_files \
        /etc/parking-monitor \
        "$telegram_bot_token" \
        "$discord_bot_token"

    echo "Installed scoped files in /etc/parking-monitor with configured variable names."
}

if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
    main "$@"
fi
