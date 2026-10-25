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

## Development checks

```bash
python -m unittest discover -s tests -v
python -m compileall -q main.py kuroo storage tests
```

`discord.py==2.6.4` is pinned because the shared V2 renderer wraps its message
serializers. Run the transport tests before changing that dependency. Gateway
login, slash command sync, and real Discord rendering require your own test bot
and server; the automated tests use mocked HTTP transports.

## License

MIT License

Copyright (c) 2026 Codex Development
