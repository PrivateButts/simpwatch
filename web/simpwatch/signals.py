from django.db.models.signals import post_delete, post_save
from django.dispatch import receiver

from .models import ScoreAdjustment, SimpEvent
from .scoring import bump_leaderboard_cache_version


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
