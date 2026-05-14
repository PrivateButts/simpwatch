from __future__ import annotations

from django.conf import settings
from django.db import models


class Person(models.Model):
    name = models.CharField(max_length=255)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self) -> str:
        return self.name


class Identity(models.Model):
    class Platform(models.TextChoices):
        TWITCH = "twitch", "Twitch"
        DISCORD = "discord", "Discord"

    person = models.ForeignKey(
        Person, on_delete=models.CASCADE, related_name="identities"
    )
    platform = models.CharField(max_length=20, choices=Platform.choices)
    platform_user_id = models.CharField(max_length=255)
    username = models.CharField(max_length=255)
    display_name = models.CharField(max_length=255, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = (("platform", "platform_user_id"),)
        indexes = [
            models.Index(fields=["platform", "username"]),
        ]

    def __str__(self) -> str:
        return f"{self.platform}:{self.username}"


class SimpEvent(models.Model):
    class EventType(models.TextChoices):
        SIMP = "simp", "Simp"
        BAMDER = "bamder", "Bamder"
        DEATH = "death", "Death"

    class Platform(models.TextChoices):
        TWITCH = "twitch", "Twitch"
        DISCORD = "discord", "Discord"

    actor_identity = models.ForeignKey(
        Identity, on_delete=models.CASCADE, related_name="acted_events"
    )
    target_person = models.ForeignKey(
        Person, on_delete=models.CASCADE, related_name="received_events"
    )
    platform = models.CharField(max_length=20, choices=Platform.choices)
    event_type = models.CharField(
        max_length=20,
        choices=EventType.choices,
        default=EventType.SIMP,
    )
    source = models.CharField(max_length=255)
    points = models.IntegerField(default=1)
    raw_content = models.TextField(blank=True)
    reason = models.TextField(blank=True)
    game_id = models.CharField(max_length=255, blank=True)
    game_name = models.CharField(max_length=255, blank=True)
    message_id = models.CharField(max_length=255, blank=True)
    dedupe_key = models.CharField(max_length=255, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [
            models.Index(fields=["created_at"]),
            models.Index(fields=["platform", "created_at"]),
            models.Index(fields=["target_person", "created_at"]),
        ]

    def __str__(self) -> str:
        return (
            f"{self.platform}:{self.event_type}:{self.actor_identity}"
            f" -> {self.target_person} (+{self.points})"
        )


class ScoreAdjustment(models.Model):
    class AdjustmentType(models.TextChoices):
        SIMP = "simp", "Simp"
        BAMDER = "bamder", "Bamder"
        DEATH = "death", "Death"

    target_person = models.ForeignKey(
        Person, on_delete=models.CASCADE, related_name="score_adjustments"
    )
    adjustment_type = models.CharField(
        max_length=20,
        choices=AdjustmentType.choices,
        default=AdjustmentType.SIMP,
    )
    points_delta = models.IntegerField()
    reason = models.CharField(max_length=500)
    game_id = models.CharField(max_length=255, blank=True)
    game_name = models.CharField(max_length=255, blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [models.Index(fields=["created_at"])]

    def __str__(self) -> str:
        return f"{self.target_person} ({self.points_delta:+d})"


class ScoringConfig(models.Model):
    cooldown_seconds = models.PositiveIntegerField(default=0)
    default_points = models.IntegerField(default=1)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self) -> str:
        return f"ScoringConfig(cooldown={self.cooldown_seconds}, points={self.default_points})"


class TwitchBroadcasterGrant(models.Model):
    username = models.CharField(max_length=255, unique=True)
    broadcaster_user_id = models.CharField(max_length=255, unique=True)
    access_token = models.TextField()
    refresh_token = models.TextField()
    scopes = models.TextField(blank=True)
    expires_at = models.DateTimeField(null=True, blank=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        indexes = [
            models.Index(fields=["is_active", "username"]),
        ]

    def save(self, *args, **kwargs):
        self.username = self.username.strip().lower()
        super().save(*args, **kwargs)

    def __str__(self) -> str:
        status = "active" if self.is_active else "inactive"
        return f"twitch:{self.username} ({status})"


class TwitchBotGrant(models.Model):
    """Encrypted OAuth token for the Twitch bot account.

    This is typically a singleton row. Tokens are refreshed automatically
    when expired.
    """
    bot_username = models.CharField(max_length=255, unique=True)
    bot_user_id = models.CharField(max_length=255, unique=True)
    access_token = models.TextField()
    refresh_token = models.TextField()
    scopes = models.TextField(blank=True)
    expires_at = models.DateTimeField(null=True, blank=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        indexes = [
            models.Index(fields=["is_active"]),
        ]

    def save(self, *args, **kwargs):
        self.bot_username = self.bot_username.strip().lower()
        super().save(*args, **kwargs)

    def __str__(self) -> str:
        status = "active" if self.is_active else "inactive"
        return f"twitch-bot:{self.bot_username} ({status})"
