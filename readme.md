# Kuroo

Kuroo is a modern, multipurpose Discord bot featuring a seamless web dashboard, comprehensive server security, advanced music playback, and automated community management tools.

## Credits

Kuroo is a fork of [Reo-Bot](https://github.com/RayExo/Reo-Bot), developed by
[RayExo](https://github.com/RayExo) / **Codex Development**. The original bot,
assets, and feature implementation belong to their original developers.
Imported from upstream commit `565eb77b6ee22db5e0b1878207c0803794cbf5bf`.
The original MIT copyright and license are preserved in [LICENSE](LICENSE).

## Kuroo changes

- All Discord message output uses **Components V2**, with no container accent color.
- Commands, logs, DMs, replies, interaction responses, follow-ups, and webhooks share
  the same renderer. Native V2 help, music, and ticket layouts are preserved.
- Older command modules use embeds only as internal document objects; the shared
  renderer converts them to text displays, sections, and media before transmission.
- Buttons and selects keep their callbacks. Partial edits retain the message body.
- Long documents beyond Discord's V2 limits are supplied as `kuroo-details.txt`.
- Presence starts with **Kuroo Devlopment**, rotates through a prefix hint and `/help`,
  server/user statistics, and the website, and always stays in **Do Not Disturb**.
- The source bot's `.env` is excluded. Configure your own account and developer IDs.

## Upstream showcase

See the [original project](https://github.com/RayExo/Reo-Bot) for its screenshots.

## Features

- Discord bot with hybrid commands
- MongoDB Database
- FastAPI dashboard
- Music controller with live artwork, queue, and activity
- Security and automod controls
- Tickets, giveaways, welcomer, and command management
- English and Hindi help menu support

## Stack

- Python 3.12 or newer
- `discord.py`
- `wavelink`
- FastAPI
- MongoDB

## Project Layout

- `main.py` - bot entry point
- `kuroo/src` - commands, events, and runtime modules
- `kuroo/surface` - dashboard and web routes
- `kuroo/engine` - bot core
- `storage` - Mongo-backed storage layer
- `.env.example` - configuration template (copy to `.env`)

## Setup

### 1. Install dependencies

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 2. Create environment file

Copy the template and fill in your own values:

```bash
cp .env.example .env
```

Set `TOKEN`, `DISCORD_CLIENT_ID`, `MONGO_URI`, and `DEVELOPER_IDS` (comma-separated Discord user IDs).
Enable the privileged member and message content intents in the Developer Portal
for prefix commands and moderation events. Invite links use your configured or
logged-in application ID. Set `WEBSITE`, `SUPPORT_SERVER`, and `VOTE_URL` to
customize the public links. `PREFIX` defaults to `?`, so the hints
are `?help` and `/help`; servers can configure their own prefix.

For the dashboard, also set `DISCORD_CLIENT_ID`, `DISCORD_CLIENT_SECRET`, and a
random `DASHBOARD_SECRET`, or set `DASHBOARD_ENABLED=False` for a bot-only run.
Music requires a reachable Lavalink node configured in `kuroo/bridge/lavalink.py`.

### 3. Discord application setup

In the Discord Developer Portal:

- copy your `Application ID` into `DISCORD_CLIENT_ID`
- copy your `Client Secret` into `DISCORD_CLIENT_SECRET`
- add this redirect URL:

```text
http://localhost:25572/dashboard/auth/callback
```

If you use a custom domain, replace the localhost URL with your real dashboard domain in both the portal and `DASHBOARD_BASE_URL`.

### 4. Start the bot

```bash
python main.py
```

### 5. Open the dashboard

```text
http://localhost:25572/dashboard
```

Login with Discord, choose a server you manage, and configure it from the dashboard.

## Dashboard Areas

- Overview
- Security
- Moderation
- Music
- Commands
- Logs
- Giveaways
- Tickets
- Welcomer

## Notes

- Only guild owners, administrators, or members with manage server access can manage a server in the dashboard.
- The music page updates live while the bot is running.
- The server language setting affects the help menu output.

## Premium and bot admin commands

All commands below support both slash commands and the configured prefix (default `?`).
Bot admins are the IDs in `DEVELOPER_IDS`, plus stored users with the bot's `admin`
or `owner` type. A Discord server's Administrator permission does not make someone
a bot admin.

| Command | Access | Behavior |
| --- | --- | --- |
| `/premiumcode` | Bot admins | Generate a single-use Diamond server premium code. Redeem within 12 hours; premium lasts 24 hours from redemption. Prefix invocation delivers the code by DM. |
| `/premiumgrant guild_id:<server ID> days:1` | Bot admins | Grant Diamond premium to any server ID, for 1–3650 days. |
| `/premiumrevoke guild_id:<server ID>` | Bot admins | Revoke that server's premium and restore free-tier limits. |
| `/redeem` | Manage Server or bot admins for new one-day server codes | Open a private code-entry modal. An optional `code` argument is also supported. Legacy codes retain their existing access rules. |
| `/vote` | Everyone | Show the Top.gg vote link and reward instructions. |
| `/votepremium` | Manage Server or bot admins | Claim the latest verified vote for this server. One vote rewards one server, until 12 hours after the vote. |
| `/noprefix off` / `/noprefix on` | Bot admins or active no-prefix subscribers | Disable or enable your own no-prefix preference. With no argument, toggle it. |

Bot admins have no-prefix access by default, including in DMs. Their explicit
preference survives restarts. Prefix and slash commands remain available when
no-prefix is disabled. Existing paid no-prefix users keep their subscription rules.

New rewards guarantee premium through their stated expiry; they do not stack on
an existing longer subscription or downgrade a higher/lifetime tier. Existing
legacy codes retain their original tier, duration, and lifetime behavior. Premium
checks treat expired cached subscriptions as free immediately; a 30-second sweep
persists expiration and restores free-tier limits. An admin revocation also blocks
retries of rewards claimed before that revocation.

### Top.gg vote setup

1. Set the correct `DISCORD_CLIENT_ID` and list the bot on Top.gg.
2. In the Top.gg project's **Webhooks** settings, add the public HTTPS endpoint
   `https://your-host/webhooks/topgg`, using **v1** webhooks.
3. Copy the generated `whs_...` signing secret into `TOPGG_WEBHOOK_SECRET` in `.env`.
4. Restart the bot. The web server runs when this secret is configured, including
   when `DASHBOARD_ENABLED=False`. Expose the configured `WEB_PORT` through your HTTPS host.
5. Send a Top.gg webhook test to check reachability. Test events never award premium.
   After a real vote, use `/votepremium` in the server to receive the reward.

The handler verifies the `x-topgg-signature` HMAC against the raw body, rejects
stale signatures and votes for another bot, and deduplicates vote IDs in MongoDB.
Weekend vote weight does not increase the 12-hour reward. Legacy v0 shared-secret
webhooks are not supported by this endpoint.

The old HTTP `/redeem/generate/` endpoint is disabled (410); use the bot-admin-only
`/premiumcode` command. The original owner-only `generate` menu remains available
for legacy subscription code types.

### Help banner

Upload a banner for the bot's Discord profile, or set `BOT_BANNER_URL` to a public
HTTPS image URL. Help fetches the bot's banner and places it above every help page
as a Components V2 media gallery. If no banner exists, help still works without it.
Restart the bot after changing an uploaded banner or the override.

### Database upgrade

Guild premium updates require one database record per `guild_id`. Startup creates
a unique index and fails if an older database contains duplicate guild records;
resolve duplicates before starting the upgraded bot. No records are automatically deleted.

## Development checks

```bash
python -m unittest discover -s tests -v
python -m compileall -q main.py kuroo storage tests

# Include real MongoDB integration tests using a disposable local MongoDB 8 instance:
KUROO_TEST_MONGO_URI=mongodb://127.0.0.1:27018 python -m unittest discover -s tests -v
```

Mongo tests create and delete random `kuroo_test_*` databases, using synthetic
identities. Without `KUROO_TEST_MONGO_URI`, those integration tests are skipped.

`discord.py==2.6.4` is pinned because the shared V2 renderer wraps its message
serializers. Run the transport tests before changing that dependency. Gateway
login, slash command sync, and real Discord rendering require your own test bot
and server; the automated tests use mocked HTTP transports.

## License

MIT License

Copyright (c) 2026 Codex Development
