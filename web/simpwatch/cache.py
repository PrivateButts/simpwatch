"""Cache utilities for query optimization.

This module provides helpers to cache frequently-accessed, rarely-changed data
using Django's cache framework (Redis in production, LocMemCache in dev).

All cache keys follow a consistent pattern for easy invalidation via signals.
"""

from __future__ import annotations

from django.core.cache import cache

from .models import Identity, ScoringConfig, TwitchBroadcasterGrant, TwitchBotGrant


# Cache keys and TTLs
IDENTITY_CACHE_TTL = 60 * 60  # 60 minutes
SCORING_CONFIG_CACHE_TTL = None  # Indefinite (manual invalidation only)
BROADCASTER_GRANT_CACHE_TTL = 24 * 60 * 60  # 24 hours
BOT_GRANT_CACHE_TTL = 24 * 60 * 60  # 24 hours


def _identity_cache_key(platform: str, platform_user_id: str) -> str:
    """Generate cache key for an Identity lookup."""
    return f"identity:{platform}:{platform_user_id}"


def _broadcaster_grant_cache_key(username: str) -> str:
    """Generate cache key for a TwitchBroadcasterGrant lookup."""
    return f"grant:broadcaster:{username.lower()}"


def _bot_grant_cache_key() -> str:
    """Generate cache key for TwitchBotGrant lookup."""
    return "grant:bot"


def get_cached_identity(platform: str, platform_user_id: str) -> Identity | None:
    """Get Identity from cache or database.

    Args:
        platform: Identity.Platform choice (e.g., 'twitch', 'discord')
        platform_user_id: The platform's user ID

    Returns:
        Identity object or None if not found.
    """
    key = _identity_cache_key(platform, platform_user_id)
    identity = cache.get(key)
    if identity is not None:
        return identity

    # Cache miss; fetch from DB
    identity = Identity.objects.filter(
        platform=platform,
        platform_user_id=platform_user_id,
    ).first()

    if identity is not None:
        cache.set(key, identity, IDENTITY_CACHE_TTL)

    return identity


def invalidate_identity_cache(identity: Identity) -> None:
    """Invalidate cache for a specific identity."""
    key = _identity_cache_key(identity.platform, identity.platform_user_id)
    cache.delete(key)


def get_cached_scoring_config() -> ScoringConfig:
    """Get ScoringConfig from cache or database.

    Returns:
        ScoringConfig singleton row (creates default if none exists).
    """
    key = "scoring_config"
    config = cache.get(key)
    if config is not None:
        return config

    # Cache miss; fetch from DB
    config = ScoringConfig.objects.first()
    if config is None:
        # Should not happen in normal operation, but be defensive
        from django.conf import settings
        config = ScoringConfig.objects.create(
            cooldown_seconds=getattr(settings, "SIMP_DEFAULT_COOLDOWN_SECONDS", 0),
            default_points=getattr(settings, "SIMP_DEFAULT_POINTS", 1),
        )

    cache.set(key, config, SCORING_CONFIG_CACHE_TTL)
    return config


def invalidate_scoring_config_cache() -> None:
    """Invalidate cache for ScoringConfig."""
    cache.delete("scoring_config")


def get_cached_broadcaster_grant(username: str) -> TwitchBroadcasterGrant | None:
    """Get TwitchBroadcasterGrant from cache or database.

    Args:
        username: Twitch broadcaster username (case-insensitive)

    Returns:
        TwitchBroadcasterGrant object or None if not found or inactive.
    """
    username_lower = username.lower().strip()
    key = _broadcaster_grant_cache_key(username_lower)
    grant = cache.get(key)
    if grant is not None:
        return grant

    # Cache miss; fetch from DB
    grant = TwitchBroadcasterGrant.objects.filter(
        username=username_lower,
        is_active=True,
    ).first()

    if grant is not None:
        cache.set(key, grant, BROADCASTER_GRANT_CACHE_TTL)

    return grant


def invalidate_broadcaster_grant_cache(grant: TwitchBroadcasterGrant) -> None:
    """Invalidate cache for a specific broadcaster grant."""
    key = _broadcaster_grant_cache_key(grant.username)
    cache.delete(key)


def get_cached_bot_grant() -> TwitchBotGrant | None:
    """Get active TwitchBotGrant from cache or database.

    Returns:
        Active TwitchBotGrant object or None if not found.
    """
    key = _bot_grant_cache_key()
    grant = cache.get(key)
    if grant is not None:
        return grant

    # Cache miss; fetch from DB
    grant = TwitchBotGrant.objects.filter(is_active=True).first()

    if grant is not None:
        cache.set(key, grant, BOT_GRANT_CACHE_TTL)

    return grant


def invalidate_bot_grant_cache() -> None:
    """Invalidate cache for TwitchBotGrant."""
    cache.delete(_bot_grant_cache_key())
