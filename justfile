# simpwatch justfile — run `just` to list available recipes

# Default: list all recipes
default:
    @just --list

# ── Docker ────────────────────────────────────────────────────────────────────

# Build and start all services
up:
    docker compose up --build -d

# Start existing images (no rebuild)
start:
    docker compose up -d

# Stop all services
down:
    docker compose down

# Tail logs for all services
logs:
    docker compose logs -f

# Tail logs for a single service (e.g. just log web)
log service:
    docker compose logs -f {{ service }}

# Rebuild a single service image (e.g. just rebuild bot_twitch)
rebuild service:
    docker compose build {{ service }}

# ── Django management ─────────────────────────────────────────────────────────

# Run pending migrations
migrate:
    docker compose exec web python manage.py migrate

# Create a Django superuser
createsuperuser:
    docker compose exec web python manage.py createsuperuser

# Run Django system checks
check:
    docker compose exec web python manage.py check

# Collect static files
collectstatic:
    docker compose exec web python manage.py collectstatic --noinput

# Open a Django shell
shell:
    docker compose exec web python manage.py shell

# ── Tests ─────────────────────────────────────────────────────────────────────

# Run all tests (web tests in web container + twitch bot tests in bot_twitch)
test:
    docker compose up --build -d web bot_twitch
    docker compose exec web python manage.py test simpwatch.tests.test_scoring simpwatch.tests.test_views simpwatch.tests.test_command_parsing -v 2
    docker compose exec bot_twitch sh -lc 'PYTHONPATH=/app python web/manage.py test simpwatch.tests.test_twitch_bot -v 2'

# Run a specific test module, class, or method (e.g. just test-one simpwatch.tests.test_scoring)
test-one target:
    docker compose exec web python manage.py test {{ target }} -v 2

# Run scoring tests
test-scoring:
    docker compose exec web python manage.py test simpwatch.tests.test_scoring -v 2

# Run view tests
test-views:
    docker compose exec web python manage.py test simpwatch.tests.test_views -v 2

# Run command-parsing tests
test-commands:
    docker compose exec web python manage.py test simpwatch.tests.test_command_parsing -v 2

# Run Twitch bot tests
test-bot:
    docker compose up --build -d bot_twitch
    docker compose exec bot_twitch sh -lc 'PYTHONPATH=/app python web/manage.py test simpwatch.tests.test_twitch_bot -v 2'

# Run local Twitch OAuth helper (prints URL, does not auto-open browser)
twitch-token:
    uv run python services/twitch_bot/token_cli.py

# ── Lint / syntax ─────────────────────────────────────────────────────────────

# Compile-check all Python source (fast syntax validation, no Docker needed)
compile:
    python3 -m compileall web services

# ── Convenience ───────────────────────────────────────────────────────────────

# Bring services up, run migrations, then tail logs
fresh: up migrate logs

# Restart a single service (e.g. just restart bot_twitch)
restart service:
    docker compose restart {{ service }}
