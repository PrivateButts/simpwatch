# AGENTS.md

Operational guide for coding agents working in this repository.

This project is a Dockerized Django app with two bot workers:
- Twitch bot (`services/twitch_bot/main.py`)
- Discord bot (`services/discord_bot/main.py`)

Use this file as the default build/test/style contract unless a human request overrides it.

## Repository Layout

- `web/` Django project root (`manage.py`, app code, templates)
- `web/config/` Django settings/urls/asgi/wsgi
- `web/simpwatch/` core app (models, admin, views, scoring logic, signals)
- `services/common_setup.py` shared Django bootstrap for workers
- `services/twitch_bot/` Twitch worker process
- `services/discord_bot/` Discord worker process
- `docker-compose.yml` local orchestration for db/web/workers

## Source of Truth

- Business rules for scoring belong in `web/simpwatch/scoring.py`.
- Data schema belongs in `web/simpwatch/models.py` + migrations.
  - `Person` / `Identity` — cross-platform identity graph.
  - `SimpEvent` — immutable event log (simp and bamder event types).
  - `ScoreAdjustment` — admin-applied manual point corrections.
  - `ScoringConfig` — singleton row for cooldown/points configuration.
- Presentation-only logic belongs in `web/simpwatch/views.py` and templates.
- `signals.py` bumps the leaderboard cache version on any `SimpEvent` or `ScoreAdjustment` save/delete.
- Workers should be thin adapters that call scoring functions.
- `scoring.merge_people(target, sources)` collapses duplicate `Person` rows; use from admin only.

## Build / Run Commands

Run commands from repository root unless noted.

### Setup

This project uses `uv` as the Python package manager for fast, reproducible builds:

```bash
# Install uv (macOS/Linux):
curl -LsSf https://astral.sh/uv/install.sh | sh

# Or on any platform with pip:
pip install uv
```

After cloning, generate dependencies:

```bash
uv sync
```

This creates `uv.lock` with exact resolved versions.

### Docker (preferred)

- Build and start all services:
  - `docker compose up --build -d`
- Start existing images:
  - `docker compose up -d`
- Stop services:
  - `docker compose down`
- View logs for all services:
  - `docker compose logs -f`
- View one service logs:
  - `docker compose logs -f web`
  - `docker compose logs -f bot_twitch`
  - `docker compose logs -f bot_discord`

### Django management

- Run migrations:
  - `docker compose exec web python manage.py migrate`
- Create admin user:
  - `docker compose exec web python manage.py createsuperuser`
- Django system checks:
  - `docker compose exec web python manage.py check`
- Collect static files (if needed in deployment):
  - `docker compose exec web python manage.py collectstatic --noinput`

### Local Python (without Docker)

Before running local commands, install dependencies:

```bash
uv sync
```

Then run commands with `uv run`:

- Compile all Python files (fast syntax check):
  - `uv run python3 -m compileall web services`
- Run Django commands:
  - `cd web && uv run python manage.py migrate`
  - `cd web && uv run python manage.py test simpwatch.tests.test_views`
  - `cd web && uv run python manage.py runserver`
- Run tests with pytest:
  - `uv run pytest`
  - `uv run pytest --cov=web/simpwatch`

Or activate the environment and run directly:

```bash
. .venv/bin/activate  # if uv created a .venv
cd web && python manage.py migrate
```

## Test Commands

Tests live in `web/simpwatch/tests/` and use Django's test runner.

With Docker:
- Run all tests:
  - `docker compose exec web python manage.py test`
- Run tests for one module:
  - `docker compose exec web python manage.py test simpwatch.tests.test_scoring`

Locally (requires `uv sync` first):
- Run all tests:
  - `uv run pytest`
- Run with coverage:
  - `uv run pytest --cov=web.simpwatch --cov-report=term-missing`
- Run specific test module:
  - `uv run pytest web/simpwatch/tests/test_views.py`

## Lint / Format / Type Checking

No linter/type-checker config is currently committed (`pyproject.toml`, `ruff.toml`, `mypy.ini` absent).

Until tooling is added:
- Keep code Black-compatible formatting (88-char-ish line discipline is fine).
- Use `python3 -m compileall web services` before handing off changes.
- Prefer small, readable functions and explicit names over dense one-liners.

If you add lint/type tools, document exact commands in this file.

## Environment and Config

- Copy `.env.example` to `.env` for local runs.
- Required runtime secrets include:
  - `DJANGO_SECRET_KEY`
  - `TWITCH_CLIENT_ID`, `TWITCH_CLIENT_SECRET`, `TWITCH_BOT_ID`, `TWITCH_BOT_ACCESS_TOKEN`, `TWITCH_BOT_REFRESH_TOKEN`, `TWITCH_BOT_USERNAME`
  - `DISCORD_BOT_TOKEN`, `DISCORD_GUILD_ID`
- Database is configured via `DATABASE_URL` or `POSTGRES_*` variables.
- Cache requires Redis; configure via `CACHE_URL` (e.g. `redis://redis:6379/1`).
  - `LEADERBOARD_CACHE_TTL_SECONDS` controls how long leaderboard responses are cached (default 15).
- Scoring defaults come from env + `ScoringConfig` row:
  - `SIMP_DEFAULT_POINTS`
  - `SIMP_DEFAULT_COOLDOWN_SECONDS`

## Code Style Guidelines

### Python version and general style

- Target Python 3.12 semantics used by Docker images.
- Use 4-space indentation and UTF-8 text files.
- Keep files ASCII unless non-ASCII is clearly needed.
- Prefer explicit, deterministic code over implicit magic.

### Imports

- Group imports in this order: stdlib, third-party, local.
- Keep one import per line unless naturally grouped.
- Use absolute imports for cross-package usage when practical.
- Worker files may require late imports after `setup_django()`; keep `# noqa: E402` only where required.

### Formatting

- Use trailing commas in multi-line literals/calls.
- Break long calls across lines with clear hanging indents.
- Keep template/CSS formatting consistent with existing style in `leaderboard.html`.

### Types

- Add type hints for public functions and non-trivial private helpers.
- Use `X | None` rather than `Optional[X]` in modern code.
- Use dataclasses for structured payloads where appropriate (see `IdentityInput`).
- Favor concrete return types for service-layer functions.

### Naming conventions

- `snake_case` for functions, variables, modules.
- `PascalCase` for classes and Django models.
- Constants in `UPPER_SNAKE_CASE` (e.g., `WINDOWS`).
- Keep platform enum values stable (`twitch`, `discord`) to protect stored data.

### Django models and migrations

- All schema changes require a migration file.
- Add indexes for fields used in frequent filtering/order operations.
- Prefer `related_name` on FKs for clarity in query usage.
- Keep model `__str__` concise and debug-friendly.

### Views and API responses

- Keep views thin and predictable.
- Validate query params and fall back to safe defaults.
- Return stable JSON keys; avoid backwards-incompatible response shape changes.
- Avoid expensive N+1 patterns; use `select_related`/`prefetch_related` where needed.
- The leaderboard API (`GET /api/leaderboard?window=<24h|7d|30d|all>`) is cached; results are invalidated automatically via signals.

### Scoring and domain logic

- Centralize scoring logic in `scoring.py`.
- Do not duplicate scoring rules inside bot handlers.
- Preserve current command semantics:
  - Twitch `!simp` -> broadcaster target
  - Twitch `!simp @username` -> exact username target
  - Twitch `!bamder` -> records bamder incident on canonical `pamder` target
  - Twitch `!bamder reason <text>` -> same with optional reason
  - Discord `/simp target:<member>` -> selected target
- Keep cooldown behavior controlled by configuration, not hardcoded checks.

### Error handling and resilience

- Fail safely on malformed chat/command input.
- Prefer early returns for guard clauses.
- Log enough context to trace failures without leaking secrets.
- Do not swallow exceptions silently; either handle explicitly or let process-level logging surface them.

### Security and secrets

- Never commit `.env` or credentials.
- Do not print OAuth/bot tokens in logs.
- Keep admin-only operations in Django admin or authenticated paths.

### Testing expectations for new changes

- Add or update tests for any scoring rule changes.
- For bug fixes, include a regression test where practical.
- Validate migration integrity when models change.

## Agent Workflow Expectations

- Read related files before editing.
- Make minimal, focused diffs; avoid unrelated refactors.
- Preserve existing behavior unless a task explicitly changes it.
- After changes, run relevant checks/tests and report what was run.
- If tooling is missing, state that explicitly in handoff.


