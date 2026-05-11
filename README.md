# SimpWatch

SimpWatch tracks `!simp` callouts from Twitch and `/simp` calls from Discord, then renders a public leaderboard with window filters.

## Features

- Public leaderboard page with windows: `24h`, `7d`, `30d`, `all`
- Prometheus metrics endpoints for web and Twitch worker
- Auto dark mode using system preference (`prefers-color-scheme`)
- Narc leaderboard (callout count by caller)
- Twitch command parsing:
  - `!simp` -> credits channel broadcaster
  - `@<bot_username> simp @username` -> credits exact username
  - optional reason: `@<bot_username> simp @username reason <text>`,
    `!simp reason <text>`, `@<bot_username> simp @username because <text>`,
    or `!simp because <text>`
  - bamder incidents: `!bamder`, `!bamder <text>`, or `!bamder reason <text>`

Examples:
- `!simp`
- `@simplympics simp @riikarii`
- `!simp reason gifted 10 subs`
- `@simplympics simp @riikarii reason gifted 10 subs`
- `!simp because sent another dono`
- `@simplympics simp @riikarii because sent another dono`
- `!bamder`
- `!bamder bad bean`
- `!bamder reason was out of pocket`
- Discord slash-only command:
  - `/simp target:<user> reason:<optional text>`
- Django admin for identity linking and score moderation
- Django admin bulk merge action for combining duplicate people records
- Configurable cooldown lever (default disabled)

## Quick Start with Docker

### Prerequisites

- Docker and Docker Compose
- Python 3.12+ (local dev optional; for running migrations/manage.py outside Docker)
- `uv` package manager (install via `curl -LsSf https://astral.sh/uv/install.sh | sh`)

### Steps

1. Copy env file:

```bash
cp .env.example .env
```

2. Fill in required vars in `.env`:

- `DJANGO_SECRET_KEY`
- `TWITCH_BOT_USERNAME`
- `TWITCH_CLIENT_ID`
- `TWITCH_CLIENT_SECRET`
- `TWITCH_BOT_ID`
- `TWITCH_BOT_ACCESS_TOKEN`
- `TWITCH_BOT_REFRESH_TOKEN`
- `TWITCH_CHANNELS` (comma-separated channel names)
- `DISCORD_BOT_TOKEN`
- `DISCORD_GUILD_ID` (optional; if set, slash command syncs to one guild)

For reverse proxy / HTTPS deployments (important for admin login CSRF):
- `DJANGO_CSRF_TRUSTED_ORIGINS` (comma-separated origins, e.g. `https://simp.example.com`)
- `DJANGO_TRUST_X_FORWARDED_PROTO=True` (if TLS terminates at proxy)
- `DJANGO_USE_X_FORWARDED_HOST=True` (if host header comes from proxy)

3. (Optional) Install dev dependencies locally for linting/testing:

```bash
uv sync
```

4. Start services:

```bash
docker compose up --build -d
```

5. Create Django admin user:

```bash
docker compose exec web python manage.py createsuperuser
```

6. Open:

- Leaderboard: `http://<server>:8000/`
- Admin: `http://<server>:8000/admin/`

## Local Development with uv

If you want to develop locally without Docker:

### Install Dependencies

```bash
# Install project with dev dependencies (pytest, pytest-django, etc.)
uv sync
```

### Set Environment

Create a `.env` file or export variables:

```bash
export DJANGO_SECRET_KEY='dev-secret-key'
export DATABASE_URL='postgresql://user:pass@localhost:5432/simpwatch'
export CACHE_URL='redis://localhost:6379/1'
```

Or use SQLite + local memory cache for quick local testing:

```bash
export DATABASE_URL='sqlite:///db.sqlite3'
export CACHE_URL='locmem://'
```

### Run Django Locally

```bash
cd web

# Run migrations
python manage.py migrate

# Create superuser
python manage.py createsuperuser

# Start dev server (default: http://localhost:8000)
python manage.py runserver
```

### Run Tests

```bash
# Run all tests
uv run pytest

# Run with coverage
uv run pytest --cov=web.simpwatch

# Run specific test module
uv run pytest web/simpwatch/tests/test_views.py
```

### Updating Dependencies

Edit `pyproject.toml` to add/update package versions, then sync:

```bash
uv sync
```

This updates `uv.lock` with the exact resolved versions. Always commit both files.

## Environment Variables

- `SIMP_DEFAULT_POINTS`: default `1`
- `SIMP_DEFAULT_COOLDOWN_SECONDS`: default `0` (disabled)
- `CACHE_URL`: default `redis://redis:6379/1`
- `LEADERBOARD_CACHE_TTL_SECONDS`: default `15` for near-real-time freshness
- `DJANGO_CSRF_TRUSTED_ORIGINS`: empty by default
- `DJANGO_TRUST_X_FORWARDED_PROTO`: `False` by default
- `DJANGO_USE_X_FORWARDED_HOST`: `False` by default
- `TWITCH_METRICS_ENABLED`: default `true`
- `TWITCH_METRICS_PORT`: default `9090`

Set cooldown later in admin via `ScoringConfig` without code changes.

## Prometheus Metrics

SimpWatch exposes metrics in Prometheus text format:

- Web app: `GET /metrics` on the web service port (`8000` by default)
- Twitch bot: `GET /metrics` on `TWITCH_METRICS_PORT` (`9090` by default)

When deploying with Helm, both web and Twitch services can include Prometheus
scrape annotations by enabling:

- `prometheus.scrape.enabled=true`

Useful Helm values:

- `web.metrics.enabled`
- `web.metrics.path`
- `twitchBot.metrics.enabled`
- `twitchBot.metrics.port`
- `twitchBot.metrics.service.enabled`
- `prometheus.scrape.interval`

## Bot Setup

### Twitch Bot

#### Database-Backed Token Management (Recommended)

The bot can now manage tokens in the database via the web admin interface, similar to the broadcaster onboarding flow. This approach provides:
- **Automatic token refresh** – tokens are refreshed on bot startup
- **Admin UI management** – rotate/revoke tokens from Django admin
- **Persistent storage** – tokens survive pod restarts
- **Encryption at rest** – tokens are encrypted in the database

**Setup:**

1. Create a Twitch account for the bot (recommended, separate from your main account).
2. Create a Twitch application and note the `CLIENT_ID` and `CLIENT_SECRET`.
3. Set web env vars:
   - `TWITCH_CLIENT_ID=<twitch client id>`
   - `TWITCH_CLIENT_SECRET=<twitch client secret>`
   - `TWITCH_TOKEN_REDIRECT_URI=https://<your-domain>/oauth/twitch/bot/callback`
   - `TWITCH_GRANT_ENCRYPTION_KEY=<random 32-byte base64 string>`
4. Start the web app and log into the admin panel with a staff user
5. Visit `/oauth/twitch/bot/start` to initiate the bot token setup flow
6. You'll be redirected to Twitch to authorize the bot account
7. After authorization, the bot token will be stored in the database automatically
8. Restart the Twitch bot worker – it will fetch and use the token from the database:

```bash
docker compose up -d --build bot_twitch
docker compose logs -f bot_twitch
```

Expected log: `Twitch bot ready user=<name> channels=[...]`.

**Token Expiration:**
When a token expires, the bot automatically refreshes it on startup using the refresh token. Refreshed tokens are automatically saved to the database, so no manual updates are needed.

#### Legacy: Environment Variable Tokens

If you prefer to manage tokens via env vars (not recommended for production), you can still use the original approach:

1. Create a Twitch account for the bot.
2. Create a Twitch application and note the `CLIENT_ID` and `CLIENT_SECRET`.
3. Generate a refreshable user token pair for the bot account with chat scopes (`user:read:chat`, `user:write:chat`, `user:bot`).
4. In `.env` or secrets, set:
   - `TWITCH_BOT_USERNAME=<bot account username>`
   - `TWITCH_CLIENT_ID=<twitch client id>`
   - `TWITCH_CLIENT_SECRET=<twitch client secret>`
   - `TWITCH_BOT_ID=<bot account user id>`
   - `TWITCH_BOT_ACCESS_TOKEN=<access token>`
   - `TWITCH_BOT_REFRESH_TOKEN=<refresh token>`
   - `TWITCH_CHANNELS=channel_one,channel_two`
5. Restart the bot worker

The bot will fall back to env vars if no database grant is found. Like the database approach, tokens are automatically refreshed on expiration.

### Broadcaster Onboarding (`channel:bot`)

If the bot is not a moderator in a broadcaster's channel, that broadcaster must
authorize your app with `channel:bot` so EventSub replies can be sent.

1. Configure web env vars:
  - `TWITCH_TOKEN_REDIRECT_URI=https://<your-domain>/oauth/twitch/callback`
  - `TWITCH_BROADCASTER_TOKEN_SCOPES=channel:bot`
  - `TWITCH_GRANT_ENCRYPTION_KEY=<long-random-value>`
2. Ensure the channel login is listed in `TWITCH_CHANNELS`.
  - Tokens are only persisted for logins in `TWITCH_CHANNELS` (case-insensitive).
3. Send broadcaster to:
  - `https://<your-domain>/oauth/twitch/start`
4. Broadcaster completes Twitch consent.
5. Verify stored grant in Django admin:
  - `Admin -> Simpwatch -> Twitch broadcaster grants`

Revocation endpoint (staff-only):

- `POST /oauth/twitch/revoke` with form field `username=<channel_login>`

When a grant is missing/inactive, the bot still records events but skips reply
sending for that channel.

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
  - `@<bot_username> simp @username` should credit the exact username target.
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

## Docker Build with uv

All Dockerfiles now use `uv` for fast, deterministic builds:

```dockerfile
# Install uv
RUN pip install --no-cache-dir uv

# Copy project config and install dependencies
COPY pyproject.toml uv.lock* /app/
RUN uv sync --frozen --no-dev

COPY . /app
```

Key points:
- `uv sync --frozen` ensures exact versions from `uv.lock`
- `--no-dev` skips dev dependencies in production builds
- `uv` is ~2-3x faster than pip for resolving and installing

The `uv.lock` file (generated by `uv sync`) is committed to the repo
for reproducible builds across all environments.

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
export CACHE_URL='redis://redis:6379/1'
export LEADERBOARD_CACHE_TTL_SECONDS='15'

export SIMP_DEFAULT_POINTS='1'
export SIMP_DEFAULT_COOLDOWN_SECONDS='0'

export TWITCH_BOT_USERNAME='your-bot-name'
export TWITCH_CLIENT_ID='your-client-id'
export TWITCH_CLIENT_SECRET='your-client-secret'
export TWITCH_BOT_ID='your-bot-user-id'
export TWITCH_BOT_ACCESS_TOKEN='your-access-token'
export TWITCH_BOT_REFRESH_TOKEN='your-refresh-token'
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
