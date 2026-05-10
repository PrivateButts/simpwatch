import os
from pathlib import Path

import dj_database_url


BASE_DIR = Path(__file__).resolve().parent.parent


def _csv_env(name: str, default: str = "") -> list[str]:
    raw = os.getenv(name, default)
    return [value.strip() for value in raw.split(",") if value.strip()]


SECRET_KEY = os.getenv("DJANGO_SECRET_KEY", "dev-secret-key")
DEBUG = os.getenv("DJANGO_DEBUG", "False").lower() == "true"
ALLOWED_HOSTS = _csv_env("DJANGO_ALLOWED_HOSTS", "*")
CSRF_TRUSTED_ORIGINS = _csv_env("DJANGO_CSRF_TRUSTED_ORIGINS", "")

if os.getenv("DJANGO_TRUST_X_FORWARDED_PROTO", "False").lower() == "true":
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")

USE_X_FORWARDED_HOST = (
    os.getenv("DJANGO_USE_X_FORWARDED_HOST", "False").lower() == "true"
)

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "simpwatch",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"
ASGI_APPLICATION = "config.asgi.application"

DATABASE_URL = os.getenv("DATABASE_URL")
if DATABASE_URL:
    DATABASES = {"default": dj_database_url.parse(DATABASE_URL)}
else:
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.postgresql",
            "NAME": os.getenv("POSTGRES_DB", "simpwatch"),
            "USER": os.getenv("POSTGRES_USER", "simpwatch"),
            "PASSWORD": os.getenv("POSTGRES_PASSWORD", "simpwatch"),
            "HOST": os.getenv("POSTGRES_HOST", "db"),
            "PORT": os.getenv("POSTGRES_PORT", "5432"),
        }
    }

# Database connection pooling settings
DATABASES["default"].setdefault("CONN_MAX_AGE", 300)  # 5 minutes
DATABASES["default"].setdefault("OPTIONS", {})
DATABASES["default"]["OPTIONS"]["connect_timeout"] = 5
# Use server-side prepared statements to avoid connection issues
DATABASES["default"]["OPTIONS"]["options"] = "-c statement_timeout=30000"

CACHE_URL = os.getenv("CACHE_URL", "").strip()
if CACHE_URL:
    CACHES = {
        "default": {
            "BACKEND": "django_redis.cache.RedisCache",
            "LOCATION": CACHE_URL,
            "OPTIONS": {
                "CLIENT_CLASS": "django_redis.client.DefaultClient",
            },
        }
    }
else:
    CACHES = {
        "default": {
            "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
            "LOCATION": "simpwatch-default",
        }
    }

LEADERBOARD_CACHE_TTL_SECONDS = int(os.getenv("LEADERBOARD_CACHE_TTL_SECONDS", "15"))

AUTH_PASSWORD_VALIDATORS = [
    {
        "NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"
    },
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LANGUAGE_CODE = "en-us"
TIME_ZONE = "UTC"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
STATICFILES_STORAGE = "whitenoise.storage.CompressedManifestStaticFilesStorage"

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

SIMP_DEFAULT_POINTS = int(os.getenv("SIMP_DEFAULT_POINTS", "1"))
SIMP_DEFAULT_COOLDOWN_SECONDS = int(os.getenv("SIMP_DEFAULT_COOLDOWN_SECONDS", "0"))
TWITCH_CHANNELS = _csv_env("TWITCH_CHANNELS", "")
TWITCH_BOT_USERNAME = os.getenv("TWITCH_BOT_USERNAME", "")
TWITCH_CLIENT_ID = os.getenv("TWITCH_CLIENT_ID", "")
TWITCH_CLIENT_SECRET = os.getenv("TWITCH_CLIENT_SECRET", "")
TWITCH_BOT_ACCESS_TOKEN = os.getenv("TWITCH_BOT_ACCESS_TOKEN", "")
_oauth_token = os.getenv("TWITCH_OAUTH_TOKEN", "")
TWITCH_OAUTH_TOKEN = (
    _oauth_token
    if _oauth_token
    else (f"oauth:{TWITCH_BOT_ACCESS_TOKEN}" if TWITCH_BOT_ACCESS_TOKEN else "")
)
TWITCH_TOKEN_SCOPES = os.getenv("TWITCH_TOKEN_SCOPES", "chat:read chat:edit")
TWITCH_TOKEN_REDIRECT_URI = os.getenv("TWITCH_TOKEN_REDIRECT_URI", "").strip()
DISCORD_BOT_TOKEN = os.getenv("DISCORD_BOT_TOKEN", "")
