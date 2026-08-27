# Secure Dual-Channel Notifications Design

## Objective

Make parking availability notifications and remote control resilient to a
Telegram outage while containing the credential exposure discovered on 27
August 2026. Telegram and Discord must deliver independently, both command
interfaces must be private to the single authorized user, and no live
credential may exist in tracked source or application logs.

## Scope

This change covers:

- environment-only Telegram and Discord credentials;
- strict Telegram authorization for user/chat `404346140`;
- a guild-scoped Discord bot restricted to application `1542514080810664018`,
  server `1476852384826392628`, channel `1542511880659017792`, and user
  `1138419941926776893`;
- Discord slash commands and interactive alert controls mirroring Telegram;
- independent Telegram and Discord availability delivery;
- durable per-event and per-channel delivery state;
- bounded retry backoff and sanitized delivery auditing;
- channel health in both `/status` responses;
- secure interactive secret installation on the production server; and
- tests and production verification for both channels.

The adaptive parking-check schedule remains unchanged. Restoring the Telegram
bot's public name, description, and avatar remains a BotFather operation.
Rewriting Git history is out of scope because the exposed credentials have
been revoked and a history rewrite would disrupt deployment without improving
the security of replacement credentials.

## Security Boundaries

### Secrets

`config.py` will read the following environment variables and will contain no
credential literals:

- `TELEGRAM_BOT_TOKEN`
- `TELEGRAM_CHAT_ID`
- `TELEGRAM_AUTHORIZED_USER_ID`
- `DISCORD_BOT_TOKEN`
- `DISCORD_APPLICATION_ID`
- `DISCORD_GUILD_ID`
- `DISCORD_CHANNEL_ID`
- `DISCORD_AUTHORIZED_USER_ID`

Production secrets will be stored in `/etc/parking-monitor.env`, owned by
`root:root` with mode `0600`. The relevant systemd services may read the file
through `EnvironmentFile`; the file itself remains unreadable to the service
account. Logs, exceptions, status responses, and audit records must not contain
tokens, webhook URLs, or URL query strings.

An interactive root-only script will prompt without echo for the Telegram and
Discord bot tokens, validate the shape of all values, write a temporary file
with restrictive permissions, and atomically replace
`/etc/parking-monitor.env`. It will never accept secrets as command-line
arguments. The non-secret Discord identifiers above are written by the script
from reviewed defaults and may be overridden interactively.

The old revoked Telegram token can remain in historical commits but will be
removed from the current tracked tree. The regenerated Discord webhook is not
used by this design. The Discord bot token must never be pasted into chat or
committed.

### Telegram authorization

Only Telegram user ID `404346140` in chat ID `404346140` may execute commands,
use callback buttons, or receive application responses. Authorization occurs
before reading or mutating application state. Unauthorized commands and
callbacks receive no response and create a sanitized security log containing
only numeric user/chat IDs and the attempted action.

At polling startup the application calls Telegram's webhook-deletion method
with pending updates discarded, then begins long polling. This prevents a stale
webhook from silently disabling command processing. A replacement token makes
the previously configured unknown webhook invalid.

Disabling group addition through BotFather is recommended operationally. The
application-level allowlist remains mandatory even if BotFather privacy
settings are changed.

## Architecture

### Components

1. `monitor.py` continues scraping parking availability. On a transition from
   unavailable to available it creates one durable notification event instead
   of setting a shared Boolean alert.
2. A notification store owns events, per-channel delivery state, attempts, and
   health summaries. SQLite is used for atomic concurrent access by the monitor
   and bot/notifier processes.
3. A notifier worker claims pending channel deliveries and invokes the
   Telegram and Discord adapters independently.
4. The Telegram application continues serving authorized commands and callback
   buttons. Its `/status` response reads monitoring state plus notification
   health.
5. A Discord bot serves guild-scoped slash commands and interactive alert
   controls through the Gateway. Both command front ends call the same shared
   status, statistics, and interval operations.
6. Channel adapters expose one delivery interface and return a structured,
   sanitized result. They never decide event lifecycle or retry policy.

### SQLite schema

The database lives at `/opt/parking_monitor/notifications.sqlite3` and contains:

`notification_events`

- `id`: generated event identifier;
- `event_type`: initially `parking_available`;
- `created_at`: UTC timestamp;
- `payload_json`: non-secret event data; and
- `source_check`: monitor check number for traceability.

`notification_deliveries`

- `event_id` and `channel`: composite unique key;
- `status`: `pending`, `retry`, `delivered`, or `failed`;
- `attempt_count`;
- `next_attempt_at`;
- `last_attempt_at`;
- `delivered_at`; and
- `last_error`: sanitized class/category and bounded message.

Each new event creates one delivery row for Telegram and one for Discord. A
channel is considered configured only when its credential and destination are
present and valid in shape. Missing configuration is logged as disabled, not
as a delivery failure.

### Delivery lifecycle

The worker selects due `pending` or `retry` rows and atomically claims one row
before network I/O. Delivery results update only that channel's row:

- success becomes `delivered` and is never attempted again;
- a transient network, timeout, throttling, or 5xx error becomes `retry`;
- an authentication, forbidden, invalid destination, or malformed request
  error becomes `failed` until credentials/configuration change; and
- an unexpected error becomes `retry` with a sanitized error record.

Transient retries use delays of 30 seconds, 2 minutes, 10 minutes, 30 minutes,
and then 60 minutes, capped at 60 minutes. There is no terminal retry count for
transient channel outages because an availability event must survive a
prolonged Telegram block. Restarting either service does not duplicate a
delivery already marked `delivered`.

If one channel succeeds and the other fails, the successful channel remains
complete while only the failed channel retries.

### Discord

The Discord bot uses guild installation and registers guild-scoped slash
commands so updates appear immediately and are unavailable outside the private
server. It does not request Message Content, Guild Members, Presence, or other
privileged Gateway intents.

Every interaction must match all three runtime boundaries before any state is
read or changed:

- guild ID `1476852384826392628`;
- channel ID `1542511880659017792`; and
- user ID `1138419941926776893`.

Unauthorized interactions receive an ephemeral denial and create a sanitized
security log. Commands are also configured with restrictive default Discord
permissions, but runtime ID validation is authoritative because server
administrators can bypass Discord command visibility restrictions.

Discord mirrors these Telegram operations:

- `/status`: last parking check, availability, polling mode/interval, next
  expected check, and delivery-channel health;
- `/stats`: total checks, availability hits, last check, and notification
  delivery counts; and
- `/interval`: view or change the normal polling interval while leaving the
  five-minute month-end override intact.

Availability alerts are posted to the configured parking channel using the bot
token and include interactive Status and Stats buttons. Button interactions use
the same authorization and shared operations as slash commands. The Discord
bot token and authorization headers are redacted from every error before
logging or persistence.

### Telegram

Telegram sends the existing availability message and inline status buttons to
the configured private chat. Command handlers and callback handlers share one
authorization function. Its `/status`, `/stats`, and `/interval` behavior is
moved behind the same shared operations used by Discord so both interfaces
report and mutate the same state consistently. `/status` includes:

- last parking check and availability;
- active polling interval/mode;
- last successful Telegram delivery;
- last successful Discord delivery; and
- pending/retrying/failed delivery counts by channel.

## Migration

The existing `state.json` remains the monitoring state during this change.
Before deploying the new notifier, if `state.json.alert` is true, deployment
creates one notification event and clears the legacy flag only after the event
is committed. If it is false, no historical event is synthesized. New monitor
code no longer writes the Boolean alert.

Database initialization is idempotent. Existing monitor statistics and the
adaptive schedule are preserved.

## Error Handling and Observability

Application logs include event ID, channel, attempt number, result, next retry,
and sanitized error category. They exclude request authorization headers,
Telegram and Discord bot tokens, payload credentials, and environment values.

The notifier emits a periodic health summary and an immediate log when a
channel changes between healthy, retrying, failed, disabled, and recovered.
The systemd services retain restart-on-failure behavior.

## Testing

Automated tests will cover:

- missing or malformed required environment configuration;
- rejection of unauthorized Telegram commands and callbacks before state
  access;
- authorization of the single configured user/chat;
- Discord guild, channel, and user authorization before state access;
- guild-scoped registration and handling of `/status`, `/stats`, and
  `/interval`;
- shared command results across Telegram and Discord;
- Discord status/stats alert-button authorization;
- webhook deletion before polling;
- creation of one event with one row per configured channel;
- independent channel success and failure;
- no redelivery of a successful channel;
- retry schedule and persistence across process restart;
- permanent authentication failure classification;
- Telegram and Discord bot-token redaction from logs and stored errors;
- legacy `alert` migration; and
- existing adaptive polling tests.

Tests use fake channel adapters at the network boundary and real temporary
SQLite databases for event and retry behavior.

## Deployment and Verification

1. Run the full test suite locally.
2. Commit and push code without credentials.
3. Pull the tested revision on `cloudru-server`.
4. Install/update systemd units for the notifier, Telegram bot, Discord bot,
   and the root-only environment-file reference.
5. Have the user run the interactive secret installer directly in an SSH
   terminal and enter the replacement Telegram and Discord bot tokens without
   echo.
6. Disable Telegram group addition through BotFather and restore the desired
   bot profile.
7. Restart the notifier, Telegram bot, and Discord bot services, then the
   monitor only if its event-writing code changed.
8. Verify webhook deletion, authorized Telegram `/status`, Discord guild command
   registration, authorized Discord `/status`, automated unauthorized-access
   rejection for both interfaces, and active service health.
9. Create an explicit test notification event and confirm one successful audit
   row for Telegram and one for Discord. The test event must be clearly labeled
   and must not alter parking availability statistics.
10. Confirm logs and the SQLite audit contain no credential fragments.

Production verification is incomplete until both channel deliveries are
observed or a channel is explicitly accepted as unavailable with its failure
recorded and the other channel proven successful.
