# Discord Permissions Manager

A Discord bot for managing server permissions and role assignments at scale. Everything is configured through Discord slash commands — no external database or setup wizard required.

## What it does

- **Permission sync** — define permission levels and access rules inside Discord, then apply them to every category and channel in one command
- **Role bundles** — assign or remove a named group of roles from multiple members at once, with automatic exclusive-group conflict resolution (e.g. promoting Trial → Member auto-removes Trial)
- **Permission levels** — named access tiers (None / View / Chat / Mod / Admin) that are edited interactively inside Discord, no code changes needed
- **Access rules** — grant or deny a role a permission level for a specific category or channel

All configuration is stored as JSON files in a persistent volume. No Airtable, no external services.

---

## For server admins — getting started

1. Invite the bot to your server (ask the bot host for the invite link)
2. The bot will post a welcome message with a quick-start overview
3. Run `/status` at any time to see your current configuration

Typical first-time setup:
1. `/level list` — review the built-in permission levels (or `/level edit` to customise them)
2. `/category baseline-set` — set the `@everyone` permission level for each category
3. `/access-rule add-category` — grant specific roles elevated access to categories
4. `/sync-permissions` — apply everything to Discord

---

## Stack

- Python 3.11+
- [discord.py](https://discordpy.readthedocs.io/) v2
- [python-dotenv](https://pypi.org/project/python-dotenv/)

---

## Self-hosting

### 1. Create a Discord application

1. Go to [discord.com/developers/applications](https://discord.com/developers/applications) and click **New Application**
2. Give it a name, then click **Bot** in the left sidebar
3. Under **Privileged Gateway Intents**, enable **Server Members Intent**
4. Under **Token**, click **Reset Token** — copy the token (you only see it once)

> **Keep your token secret.** It gives full control of the bot. Never paste it into chat, GitHub, or anywhere public. Put it only in your `.env` file (which is gitignored).

### 2. Generate an invite URL

1. In the left sidebar, go to **OAuth2 → URL Generator**
2. Under **Scopes**, check `bot` and `applications.commands`
3. Under **Bot Permissions**, check `Administrator`
4. Copy the URL at the bottom, open it in your browser, and invite the bot to your server

### 3. Set up your environment

Copy `.env.example` to `.env` and fill in your token:

```
DISCORD_BOT_TOKEN=paste-your-token-here
```

`.env` is gitignored — it stays on your machine only.

### 4. Install and run

```bash
python -m venv .venv
source .venv/Scripts/activate   # Windows
# source .venv/bin/activate     # Mac/Linux
pip install -r requirements.txt
python main.py
```

The bot logs in and syncs slash commands globally. Commands appear in Discord within a few minutes.

> **Tip:** Add `DISCORD_GUILD_ID=your_server_id` to `.env` for instant command sync during development.

---

## Deploying to Railway

Railway is the recommended hosting platform — it handles deploys from GitHub and supports persistent volumes so your data survives restarts and redeployments.

### 1. Create a Railway project

1. Go to [railway.app](https://railway.app) and create an account
2. Click **New Project → Deploy from GitHub repo** and select this repository
3. Railway detects Python automatically via `requirements.txt`

### 2. Set environment variables

In your Railway project → **Variables**, add:

| Variable | Value |
|---|---|
| `DISCORD_BOT_TOKEN` | Your bot token from the Discord Developer Portal |
| `DATA_DIR` | `/data` |

### 3. Add a persistent volume

In your Railway project → **Volumes**, click **Add Volume**:

- **Mount path**: `/data`

This is where all per-guild config (permission levels, bundles, access rules, etc.) is stored. Without this, data is wiped on every redeploy.

### 4. Deploy

Railway deploys automatically on every push to your default branch. The `railway.toml` in this repo sets the start command and restart policy.

---

## Commands

### Permissions  *(admin only)*

| Command | Description |
|---|---|
| `/preview-permissions` | Show what `/sync-permissions` would change without applying anything |
| `/sync-permissions` | Apply all configured levels and access rules to Discord |

### Role assignment  *(manage roles)*

| Command | Description |
|---|---|
| `/assign <member> <bundle>` | Apply a role bundle to one or more members — auto-removes conflicting exclusive-group roles |
| `/remove <member> <bundle>` | Remove a role bundle from one or more members |

Both commands accept up to 5 members at once.

### Permission levels  *(admin only)*

| Command | Description |
|---|---|
| `/level list` | List all permission levels |
| `/level view <name>` | Show all permissions for a level |
| `/level edit <name>` | Interactive editor — pick group → pick permission → set Allow / Deny / Neutral |
| `/level set <name> <permission> <value>` | Set one permission directly (with autocomplete) |
| `/level create <name> [copy_from]` | Create a new level, optionally cloned from an existing one |
| `/level delete <name>` | Delete a level (with confirmation) |
| `/level reset-defaults` | Restore all levels to built-in defaults |

### Role bundles  *(admin only)*

| Command | Description |
|---|---|
| `/bundle list` | List all bundles and their roles |
| `/bundle view <name>` | Show roles in a bundle |
| `/bundle create <name>` | Create a new empty bundle |
| `/bundle delete <name>` | Delete a bundle (with confirmation) |
| `/bundle add-role <bundle> <role>` | Add a Discord role to a bundle (up to 5 roles at once) |
| `/bundle remove-role <bundle> <role>` | Remove a role from a bundle |

### Exclusive groups  *(admin only)*

Exclusive groups enforce a "pick one" constraint — assigning any bundle that contains a role from a group automatically removes the other roles in that group from the member.

| Command | Description |
|---|---|
| `/exclusive-group list` | List all groups and their roles |
| `/exclusive-group create <name>` | Create a new group |
| `/exclusive-group delete <name>` | Delete a group (with confirmation) |
| `/exclusive-group add-role <group> <role>` | Add a role to a group |
| `/exclusive-group remove-role <group> <role>` | Remove a role from a group |

### Category baselines  *(admin only)*

Sets the `@everyone` permission level for a category. This is the baseline that everyone inherits before any role-specific access rules are applied.

| Command | Description |
|---|---|
| `/category baseline-list` | List all configured baselines |
| `/category baseline-set <category> <level>` | Set `@everyone` baseline for a category |
| `/category baseline-clear <category>` | Remove the baseline from a category |

### Access rules  *(admin only)*

Access rules grant or deny a role a specific permission level on a category or channel, layered on top of the `@everyone` baseline.

| Command | Description |
|---|---|
| `/access-rule list` | List all rules |
| `/access-rule add-category <role> <category> <level> [overwrite]` | Rule targeting a whole category |
| `/access-rule add-channel <role> <channel> <level> [overwrite]` | Rule targeting a single channel |
| `/access-rule edit <id> [level] [overwrite]` | Change the level or Allow/Deny on an existing rule |
| `/access-rule remove <id>` | Delete a rule (with confirmation) |
| `/access-rule prune` | Remove stale rules and baselines referencing deleted roles or channels |

`overwrite` is `Allow` (default) or `Deny`. Deny rules flip every explicit Allow in the level to an explicit Deny, useful for blocking a role from a channel they'd otherwise inherit access to.

### Status

| Command | Description |
|---|---|
| `/status` | Overview of all configured items for this server |

---

## Permission levels

Five built-in levels are defined in `config.py`. Edits made via `/level` commands are saved to `data/{guild_id}/permission_levels.json` and take precedence over the defaults.

| Level | Effect |
|---|---|
| **None** | Channel is invisible |
| **View** | Can see and read history, cannot interact |
| **Chat** | Standard member — read, send, react, voice |
| **Mod** | Chat + manage messages/threads, mute/move/kick members, manage channels |
| **Admin** | Full Discord administrator |

---

## File structure

```
main.py                    # Bot entry point and slash command sync
config.py                  # Permission level defaults and permission groups
requirements.txt
.env.example
services/
  local_store.py           # Per-guild JSON persistence with atomic writes and locking
  sync.py                  # Builds permission plan and applies it to Discord
cogs/
  permissions.py           # /preview-permissions, /sync-permissions
  roles.py                 # /assign, /remove
  admin.py                 # /level, /bundle, /exclusive-group, /category, /access-rule, /status
data/                      # Runtime data — gitignored, auto-created on first run
  {guild_id}/
    permission_levels.json
    bundles.json
    exclusive_groups.json
    category_baselines.json
    access_rules.json
```
