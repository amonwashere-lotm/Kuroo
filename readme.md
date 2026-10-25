# Kuroo

Kuroo is a modern, multipurpose Discord bot featuring a seamless web dashboard, comprehensive server security, advanced music playback, and automated community management tools.

## Credits

Kuroo is a fork of [Reo-Bot](https://github.com/RayExo/Reo-Bot), developed by
[RayExo](https://github.com/RayExo) / **Codex Development**. The original bot,
assets, and feature implementation belong to their original developers.
Imported from upstream commit `565eb77b6ee22db5e0b1878207c0803794cbf5bf`.
The original MIT copyright and license are preserved in [LICENSE](LICENSE).

Leveling and economy source research used the complete cogs in
[Programmify/Logiq](https://github.com/programmify/Logiq/tree/04bf128797b0200f596dc09784042fe5c644567a/cogs)
(MIT, Copyright 2025 Programmify). The notice is retained in
[third_party/Logiq-LICENSE](third_party/Logiq-LICENSE). Kuroo implements its own
atomic MongoDB operations, permissions, cooldowns, and Pillow layouts; it does not
require running a second bot or installing the reference project.

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
- Server leveling, rank/leaderboard images, global coins, banking and cosmetic titles
- Pillow welcome/goodbye cards and automatic application emoji synchronization
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

## Automatic help and command registration

Help discovers commands from the running bot's prefix registry and local Discord
application tree on every request and navigation step. It includes commands from
new cogs without help metadata, nested subcommands, standalone commands, and
slash-only commands. Hybrid commands appear once with both invocation styles.
Hidden commands, hidden parents, and hidden admin cogs remain hidden.

- `/help` or `?help`: choose a category or browse the complete paginated list.
- `/help command:rank` or `?help rank`: see usage, arguments and aliases.
- `?help tools subcommand`: look up a nested command (replace with an actual path).
- Categories and command lists are paginated, so Discord's 25-option limit does
  not hide new entries. The bot banner stays above every help page.
- Help uses the local registry and works even when Discord's remote command-list
  API is unavailable. Server prefixes and English/Hindi language preferences are retained.

At authenticated startup, application emojis synchronize, all cogs load, and then
all registered global slash commands synchronize to Discord. A failed slash sync
keeps prefix commands loaded and retries on the ready event. Startup logs report
loaded prefix paths, global slash roots, and synchronization failures.

Bot admins can retry with `?synccommands` (or the configured server prefix), even
when `/synccommands` is not yet visible in Discord. This command is private to bot
admins and confirms how many slash commands were published. A push to GitHub does
not restart a hosted bot: deploy the latest `main` and restart the host to run it.

## Leveling and economy

These are hybrid commands: use `/command` or your server prefix (default `?`).
Leveling starts enabled. Human messages containing at least three characters earn
**15 XP once per 60 seconds per member per server**. Bots, webhooks, DMs,
recognized commands, ignored channels/users, and bot-blacklisted users/servers do
not earn XP. Cooldowns survive restarts. Level `n` starts at `100 × n²` total XP.
Rank is server-specific; equal XP is ordered by user ID for stable positions.
Level-up announcements are off until a channel is selected.

| Command | What it does |
| --- | --- |
| `/rank [member]` | Show the member's Pillow rank card with avatar, level, rank, XP progress and equipped title. |
| `/lb kind:levels page:1` | Server XP leaderboard, 10 entries per image. Prefix alias: `leaderboard`. |
| `/lb kind:coins page:1` | Global wallet coin leaderboard. Bank savings are shown by `/balance`. |
| `/leveling on` / `/leveling off` | Manage Server or bot admins: enable/disable XP collection. Existing XP is retained. |
| `/levelchannel [channel]` | Manage Server or bot admins: select the level-up channel; omit it to disable announcements. |
| `/balance [member]` | Show global wallet and bank balances. Prefix aliases: `bal`, `wallet`. |
| `/daily` | Earn 250 coins once every 24 hours. |
| `/work` | Earn 80–160 coins once every hour. |
| `/deposit amount` / `/withdraw amount` | Move positive whole coins between wallet and bank; no fees or interest. |
| `/shop` | Browse cosmetic rank titles. |
| `/buy item` | Buy `night-owl` (500), `aurora` (1,500), or `after-hours` (3,000 coins), once each. |
| `/inventory` / `/equip item` | View owned titles and equip one on your rank card; `/equip none` clears it. |

Coins reuse existing global `users.balance` records, including the owner's
`root givebalance`, `root removebalance`, and `root setbalance` commands. Existing
balances and premium/no-prefix settings are retained. XP uses a separate
`member_levels` collection per server. New earnings, purchases, and bank moves are
atomic single-document updates, including their cooldown/ownership checks; no
replica set or transaction support is needed. New commands accept whole coin
amounts from 1 to 1,000,000,000,000. Coins and titles have no monetary value.
Startup requires unique user IDs and guild/member pairs; resolve any legacy
`users.user_id` duplicates before upgrading. No records are automatically deleted.

## Welcome and goodbye images

Rank, leaderboards, welcome, and goodbye cards are generated locally with Pillow:
a nearly black background, subtle violet/teal gradient, circular avatar, and
simple high-contrast type. This visual style is available to every server.
Discord message containers still have **no accent color** and use **Components V2**.
Avatar downloads have a timeout; a local silhouette is used when unavailable.
Pillow rendering runs off the event loop. No image API key is required.

- `/welcomecards on [channel]`: Manage Server or bot admins can enable welcome
  cards (defaults to the current text channel). Existing welcome content/media
  settings remain in effect. `/welcomecards off` hides only the generated card.
- Existing enabled welcome configurations receive the card automatically. The
  original `/welcome` settings menu continues to control custom text and media.
- `/leave on [channel]`: configure the goodbye card channel. It is disabled by
  default; `/leave off` disables it again. Membership removal events, including
  kicks and bans, produce this card when configured; moderation logs are separate.
- `/welcomepreview` and `/leavepreview`: preview cards before enabling delivery.
- The bot needs View Channel, Send Messages, Attach Files, and Embed Links in
  delivery channels, plus the **Server Members Intent** for join/leave events.
  Enable **Message Content Intent** for message XP and prefix commands.

## Automatic emoji sync

`SYNC_EMOJIS=True` is the default. After Discord login and before cogs/commands
load, Kuroo reuses matching application emojis by ID/name and uploads missing
icons from the source Discord CDN. Resolved icons are applied to the running
module immediately, including after config reload. Source files are not rewritten.
Discord's authenticated client handles API rate limits. Missing/unavailable
source images fall back to a small Unicode symbol; an application-list API failure
logs a warning and retains configured icons. `SYNC_EMOJIS=False` opts out.
Only the normal bot token is required. No live emoji upload is attempted by tests.

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
