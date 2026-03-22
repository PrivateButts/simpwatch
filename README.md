# SimpWatch

SimpWatch tracks `!simp` callouts from Twitch and `/simp` calls from Discord, then renders a public leaderboard with window filters.

## Features

- Public leaderboard page with windows: `24h`, `7d`, `30d`, `all`
- Auto dark mode using system preference (`prefers-color-scheme`)
- Narc leaderboard (callout count by caller)
- Twitch command parsing:
  - `!simp` -> credits channel broadcaster
  - `!simp @username` -> credits exact username
  - optional reason: `!simp @username reason <text>`, `!simp reason <text>`,
    `!simp @username because <text>`, or `!simp because <text>`

Examples:
- `!simp`
- `!simp @riikarii`
- `!simp reason gifted 10 subs`
- `!simp @riikarii reason gifted 10 subs`
- `!simp because sent another dono`
- `!simp @riikarii because sent another dono`
- Discord slash-only command:
  - `/simp target:<user> reason:<optional text>`
- Django admin for identity linking and score moderation
- Django admin bulk merge action for combining duplicate people records
- Configurable cooldown lever (default disabled)

## Quick Start

1. Copy env file:

```bash
cp .env.example .env
```

2. Fill in required vars in `.env`:

- `DJANGO_SECRET_KEY`
- `TWITCH_BOT_USERNAME`
- `TWITCH_OAUTH_TOKEN`
- `TWITCH_CHANNELS` (comma-separated channel names)
- `DISCORD_BOT_TOKEN`
- `DISCORD_GUILD_ID` (optional; if set, slash command syncs to one guild)

For reverse proxy / HTTPS deployments (important for admin login CSRF):
- `DJANGO_CSRF_TRUSTED_ORIGINS` (comma-separated origins, e.g. `https://simp.example.com`)
- `DJANGO_TRUST_X_FORWARDED_PROTO=True` (if TLS terminates at proxy)
- `DJANGO_USE_X_FORWARDED_HOST=True` (if host header comes from proxy)

3. Start services:

```bash
docker compose up --build -d
```

4. Create Django admin user:

```bash
docker compose exec web python manage.py createsuperuser
```

5. Open:

- Leaderboard: `http://<server>:8000/`
- Admin: `http://<server>:8000/admin/`

## Environment Variables

- `SIMP_DEFAULT_POINTS`: default `1`
- `SIMP_DEFAULT_COOLDOWN_SECONDS`: default `0` (disabled)
- `DJANGO_CSRF_TRUSTED_ORIGINS`: empty by default
- `DJANGO_TRUST_X_FORWARDED_PROTO`: `False` by default
- `DJANGO_USE_X_FORWARDED_HOST`: `False` by default

Set cooldown later in admin via `ScoringConfig` without code changes.

## Bot Setup

### Twitch Bot

1. Create a Twitch account for the bot (recommended, separate from your main account).
2. Generate an OAuth token for that bot account with chat scopes (`chat:read` and `chat:edit`).
   - The token should look like `oauth:...`.
3. In `.env`, set:
   - `TWITCH_BOT_USERNAME=<bot account username>`
   - `TWITCH_OAUTH_TOKEN=<oauth token>`
   - `TWITCH_CHANNELS=channel_one,channel_two`
4. Restart only the Twitch worker:

```bash
docker compose up -d --build bot_twitch
docker compose logs -f bot_twitch
```

Expected log after successful auth: `Twitch bot ready: <name>`.

### Discord Bot

1. Go to the Discord Developer Portal and create an application.
2. Add a bot user under the application.
3. Enable bot permissions needed for slash commands in your server.
4. Invite the bot with scopes:
   - `bot`
   - `applications.commands`
5. Copy the bot token into `.env`:
   - `DISCORD_BOT_TOKEN=<token>`
6. (Recommended) Set one test guild for faster command sync:
   - `DISCORD_GUILD_ID=<your server id>`
7. Restart only the Discord worker:

```bash
docker compose up -d --build bot_discord
docker compose logs -f bot_discord
```

Expected log after successful auth: `Discord bot ready: ...`.

### Verifying Commands

- Twitch:
  - `!simp` in a configured channel should credit that channel broadcaster.
  - `!simp @username` should credit the exact username target.
- Discord:
  - Use `/simp target:<member>` in the server where bot is installed.

Then confirm updates at:
- `http://<server>:8000/`
- `http://<server>:8000/api/leaderboard?window=all`

### Merging Duplicate People in Admin

When someone is auto-registered separately across Twitch/Discord, you can merge them:

1. Open `Admin -> Simpwatch -> People`.
2. Select 2+ rows that represent the same person.
3. Choose action: `Merge selected people into the first selected`.
4. Run action.

Behavior:
- The first selected row (lowest ID) is kept as the canonical `Person`.
- All selected source rows are merged into it.
- Related `Identity.person`, `SimpEvent.target_person`, and `ScoreAdjustment.target_person` are reassigned.
- Source `Person` rows are deleted after reassignment.

### Common Issues

- `Twitch bot auth failed`: token invalid/expired or missing `oauth:` prefix.
- `Discord bot auth failed`: invalid token in `DISCORD_BOT_TOKEN`.
- Discord slash command not visible yet:
  - if `DISCORD_GUILD_ID` is unset, global command sync can take time.
  - set `DISCORD_GUILD_ID` and restart `bot_discord` for quick sync.

## Deployment Images (GitHub Actions)

This repo includes a workflow at `.github/workflows/docker-images.yml` that builds container images for:

- `web`
- `bot_twitch`
- `bot_discord`

Behavior:
- On pull requests: builds images (no push).
- On pushes to `main`: builds and pushes to GHCR.
- On tags `v*`: builds and pushes tag-based images.

Published image naming:
- `ghcr.io/<owner>/simpwatch-web`
- `ghcr.io/<owner>/simpwatch-bot-twitch`
- `ghcr.io/<owner>/simpwatch-bot-discord`

Default tags include branch/tag/sha, plus `latest` for the default branch.

To use these in Docker Swarm, reference the GHCR image tags in your stack file instead of local `build:` blocks.

## Docker Swarm Deployment

Use `docker-compose.swarm.yml` for Swarm stacks.

Important Swarm note:
- `env_file` is not used by `docker stack deploy`, so the stack file defines explicit `environment` keys.

### 1) Export environment variables on your manager node

Example:

```bash
export DJANGO_SECRET_KEY='replace-me'
export DJANGO_DEBUG='False'
export DJANGO_ALLOWED_HOSTS='example.com,localhost,127.0.0.1'

export POSTGRES_DB='simpwatch'
export POSTGRES_USER='simpwatch'
export POSTGRES_PASSWORD='replace-me'
export POSTGRES_HOST='db'
export POSTGRES_PORT='5432'
export DATABASE_URL="postgresql://${POSTGRES_USER}:${POSTGRES_PASSWORD}@${POSTGRES_HOST}:${POSTGRES_PORT}/${POSTGRES_DB}"

export SIMP_DEFAULT_POINTS='1'
export SIMP_DEFAULT_COOLDOWN_SECONDS='0'

export TWITCH_BOT_USERNAME='your-bot-name'
export TWITCH_OAUTH_TOKEN='oauth:your-token'
export TWITCH_CHANNELS='channel_one,channel_two'

export DISCORD_BOT_TOKEN='your-discord-token'
export DISCORD_GUILD_ID='your-guild-id'

export WEB_IMAGE='ghcr.io/<owner>/simpwatch-web:latest'
export BOT_TWITCH_IMAGE='ghcr.io/<owner>/simpwatch-bot-twitch:latest'
export BOT_DISCORD_IMAGE='ghcr.io/<owner>/simpwatch-bot-discord:latest'
```

### 2) Deploy stack

```bash
docker stack deploy -c docker-compose.swarm.yml simpwatch
```

### 3) Verify

```bash
docker stack services simpwatch
docker service logs -f simpwatch_web
docker service logs -f simpwatch_bot_twitch
docker service logs -f simpwatch_bot_discord
```
