# REO

Reo is a modern, multipurpose Discord bot featuring a seamless web dashboard, comprehensive server security, advanced music playback, and automated community management tools.

## Showcase

### Overview
<img width="1919" height="1079" alt="image" src="https://github.com/user-attachments/assets/e9bb032c-c96f-4fcf-a8c2-d6189da1e9ff" />

### Music

<img width="1919" height="1079" alt="image" src="https://github.com/user-attachments/assets/507f0aa3-9bc1-4074-a6e4-62cfaa20a58a" />


### Bot UI

<img width="658" height="848" alt="image" src="https://github.com/user-attachments/assets/00b7c7a6-39cf-4d42-9d10-b65607d6d4c8" />
<br>

<img width="748" height="548" alt="image" src="https://github.com/user-attachments/assets/68f9902c-01a8-42d5-a624-be8005fb604b" />


## Features

- Discord bot with hybrid commands
- MongoDB Database
- FastAPI dashboard
- Music controller with live artwork, queue, and activity
- Security and automod controls
- Tickets, giveaways, welcomer, and command management
- English and Hindi help menu support

## Stack

- Python
- `discord.py`
- `wavelink`
- FastAPI
- MongoDB

## Project Layout

- `main.py` - bot entry point
- `reo/src` - commands, events, and runtime modules
- `reo/surface` - dashboard and web routes
- `reo/engine` - bot core
- `storage` - Mongo-backed storage layer
- `.env` - local environment config

## Setup

### 1. Install dependencies

```bash
pip install -r requirements.txt
```

### 2. Create environment file

Create `.env`:

```env
TOKEN=""

PREFIX=""

SHARD_COUNT=""

BOT_NAME="Reo"

# Web / API Hosting
WEB_HOST="0.0.0.0"
WEB_PORT="25572"


# Automation Features
SYNC_EMOJIS="False"

# OAuth2 / Dashboard Configuration
DISCORD_CLIENT_ID=""
DISCORD_CLIENT_SECRET=""
DASHBOARD_SECRET=""
DASHBOARD_BASE_URL="http://localhost:25572"

# MongoDB Database Configuration
MONGO_URI=""
```

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

## License

MIT License

Copyright (c) 2026 Codex Development
