from django.db.models.signals import post_delete, post_save
from django.dispatch import receiver

from .cache import (
    invalidate_bot_grant_cache,
    invalidate_broadcaster_grant_cache,
    invalidate_identity_cache,
    invalidate_scoring_config_cache,
)
from .models import (
    Identity,
    ScoreAdjustment,
    SimpEvent,
    ScoringConfig,
    TwitchBotGrant,
    TwitchBroadcasterGrant,
)
from .scoring import bump_leaderboard_cache_version


# Leaderboard cache invalidation
@receiver(post_save, sender=SimpEvent)
def simp_event_saved(sender, instance, created, **kwargs):
    bump_leaderboard_cache_version()


@receiver(post_delete, sender=SimpEvent)
def simp_event_deleted(sender, instance, **kwargs):
    bump_leaderboard_cache_version()


@receiver(post_save, sender=ScoreAdjustment)
def score_adjustment_saved(sender, instance, created, **kwargs):
    bump_leaderboard_cache_version()


@receiver(post_delete, sender=ScoreAdjustment)
def score_adjustment_deleted(sender, instance, **kwargs):
    bump_leaderboard_cache_version()


# Query cache invalidation
@receiver(post_save, sender=Identity)
def identity_saved(sender, instance, created, **kwargs):
    """Invalidate cache when an Identity is created or updated."""
    invalidate_identity_cache(instance)


@receiver(post_delete, sender=Identity)
def identity_deleted(sender, instance, **kwargs):
    """Invalidate cache when an Identity is deleted."""
    invalidate_identity_cache(instance)


@receiver(post_save, sender=ScoringConfig)
def scoring_config_saved(sender, instance, created, **kwargs):
    """Invalidate cache when ScoringConfig is updated (usually by admin)."""
    invalidate_scoring_config_cache()


@receiver(post_save, sender=TwitchBroadcasterGrant)
def broadcaster_grant_saved(sender, instance, created, **kwargs):
    """Invalidate cache when a broadcaster grant is created or updated."""
    invalidate_broadcaster_grant_cache(instance)


@receiver(post_delete, sender=TwitchBroadcasterGrant)
def broadcaster_grant_deleted(sender, instance, **kwargs):
    """Invalidate cache when a broadcaster grant is deleted."""
    invalidate_broadcaster_grant_cache(instance)


@receiver(post_save, sender=TwitchBotGrant)
def bot_grant_saved(sender, instance, created, **kwargs):
    """Invalidate cache when bot grant is created or updated."""
    invalidate_bot_grant_cache()


@receiver(post_delete, sender=TwitchBotGrant)
def bot_grant_deleted(sender, instance, **kwargs):
    """Invalidate cache when bot grant is deleted."""
    invalidate_bot_grant_cache()
