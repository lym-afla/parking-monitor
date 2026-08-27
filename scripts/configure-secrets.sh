#!/usr/bin/env bash

set -euo pipefail
umask 077

install_environment_file() {
    local destination=$1
    local telegram_bot_token=$2
    local discord_bot_token=$3
    local temp_file
    local discord_token_variable_name=DISCORD_BOT_TOKEN

    temp_file=$(mktemp "${destination}.XXXXXX") || return 1

    if ! cat > "${temp_file}" <<EOF
TELEGRAM_BOT_TOKEN=${telegram_bot_token}
TELEGRAM_CHAT_ID=404346140
TELEGRAM_AUTHORIZED_USER_ID=404346140
${discord_token_variable_name}=${discord_bot_token}
DISCORD_APPLICATION_ID=1542514080810664018
DISCORD_GUILD_ID=1476852384826392628
DISCORD_CHANNEL_ID=1542511880659017792
DISCORD_AUTHORIZED_USER_ID=1138419941926776893
EOF
    then
        rm -f "${temp_file}"
        return 1
    fi

    if ! chown root:root "${temp_file}" || ! chmod 600 "${temp_file}"; then
        rm -f "${temp_file}"
        return 1
    fi

    if ! mv "${temp_file}" "${destination}"; then
        rm -f "${temp_file}"
        return 1
    fi
}


main() {
    if [[ "${EUID}" -ne 0 ]]; then
        echo "This script must be run as root." >&2
        return 1
    fi

    local telegram_bot_token
    local discord_bot_token

    read -r -s -p "Telegram bot token: " telegram_bot_token
    echo
    read -r -s -p "Discord bot token: " discord_bot_token
    echo

    if [[ -z "${telegram_bot_token}" || -z "${discord_bot_token}" ]]; then
        echo "TELEGRAM_BOT_TOKEN and DISCORD_BOT_TOKEN must not be empty." >&2
        return 1
    fi

    install_environment_file \
        /etc/parking-monitor.env \
        "${telegram_bot_token}" \
        "${discord_bot_token}"

    echo "Installed /etc/parking-monitor.env with configured variable names."
}

if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
    main "$@"
fi
