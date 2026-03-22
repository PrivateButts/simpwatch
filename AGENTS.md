# AGENTS.md

Operational guide for coding agents working in this repository.

This project is a Dockerized Django app with two bot workers:
- Twitch bot (`services/twitch_bot/main.py`)
- Discord bot (`services/discord_bot/main.py`)

Use this file as the default build/test/style contract unless a human request overrides it.

## Repository Layout

- `web/` Django project root (`manage.py`, app code, templates)
- `web/config/` Django settings/urls/asgi/wsgi
- `web/simpwatch/` core app (models, admin, views, scoring logic)
- `services/common_setup.py` shared Django bootstrap for workers
- `services/twitch_bot/` Twitch worker process
- `services/discord_bot/` Discord worker process
- `docker-compose.yml` local orchestration for db/web/workers

## Source of Truth

- Business rules for scoring belong in `web/simpwatch/scoring.py`.
- Data schema belongs in `web/simpwatch/models.py` + migrations.
- Presentation-only logic belongs in `web/simpwatch/views.py` and templates.
- Workers should be thin adapters that call scoring functions.

## Build / Run Commands

Run commands from repository root unless noted.

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

- Compile all Python files (fast syntax check):
  - `python3 -m compileall web services`

## Test Commands

There is currently no committed test suite in this repository.
When tests are added, use Django's test runner unless project instructions change.

- Run all tests:
  - `docker compose exec web python manage.py test`
- Run tests for one module:
  - `docker compose exec web python manage.py test simpwatch.tests.test_scoring`
  - `docker compose exec web python manage.py test simpwatch.tests.test_views`
  - `docker compose exec web python manage.py test simpwatch.tests.test_command_parsing`
- Run a single test case:
  - `docker compose exec web python manage.py test simpwatch.tests.test_scoring.ScoringTests`
- Run a single test method:
  - `docker compose exec web python manage.py test simpwatch.tests.test_scoring.ScoringTests.test_register_simp`

If running outside Docker, use the same `python manage.py test ...` commands from `web/`.

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
  - `TWITCH_OAUTH_TOKEN`
  - `DISCORD_BOT_TOKEN`
- Database is configured via `DATABASE_URL` or `POSTGRES_*` variables.
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

### Scoring and domain logic

- Centralize scoring logic in `scoring.py`.
- Do not duplicate scoring rules inside bot handlers.
- Preserve current command semantics:
  - Twitch `!simp` -> broadcaster target
  - Twitch `!simp @username` -> exact username target
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

## Cursor/Copilot Rules Check

- `.cursorrules`: not found
- `.cursor/rules/`: not found
- `.github/copilot-instructions.md`: not found

No repository-specific Cursor/Copilot rule files are currently present.
