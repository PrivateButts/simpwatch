from django.contrib import admin
from django.contrib import messages

from .models import (
    Identity,
    Person,
    ScoreAdjustment,
    ScoringConfig,
    SimpEvent,
    TwitchBotGrant,
    TwitchBroadcasterGrant,
)
from .scoring import bump_leaderboard_cache_version, merge_people


@admin.register(Person)
class PersonAdmin(admin.ModelAdmin):
    list_display = ("id", "name", "created_at")
    search_fields = ("name",)
    actions = ("merge_selected_people",)

    @admin.action(description="Merge selected people into the first selected")
    def merge_selected_people(self, request, queryset):
        people = list(queryset.order_by("id"))
        if len(people) < 2:
            self.message_user(
                request,
                "Select at least two people to merge.",
                level=messages.WARNING,
            )
            return

        target = people[0]
        deleted_count = merge_people(target=target, sources=people[1:])
        self.message_user(
            request,
            f"Merged {len(people) - 1} people into '{target.name}'. Removed {deleted_count} source record(s).",
            level=messages.SUCCESS,
        )


@admin.register(Identity)
class IdentityAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "platform",
        "username",
        "display_name",
        "platform_user_id",
        "person",
        "created_at",
    )
    list_filter = ("platform",)
    search_fields = ("username", "display_name", "platform_user_id", "person__name")


@admin.register(SimpEvent)
class SimpEventAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "event_type",
        "platform",
        "actor_identity",
        "target_person",
        "points",
        "game_id",
        "game_name",
        "reason",
        "source",
        "created_at",
    )
    list_filter = ("event_type", "platform", "created_at")
    search_fields = (
        "actor_identity__username",
        "target_person__name",
        "source",
        "message_id",
        "game_id",
        "game_name",
        "reason",
    )
    autocomplete_fields = ("actor_identity", "target_person")


@admin.register(ScoreAdjustment)
class ScoreAdjustmentAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "target_person",
        "adjustment_type",
        "points_delta",
        "game_id",
        "game_name",
        "reason",
        "created_by",
        "created_at",
    )
    list_filter = ("adjustment_type", "created_at")
    search_fields = ("target_person__name", "reason", "game_id", "game_name")
    autocomplete_fields = ("target_person", "created_by")

    def save_model(self, request, obj, form, change):
        if not obj.created_by_id:
            obj.created_by = request.user
        if obj.adjustment_type not in (
            ScoreAdjustment.AdjustmentType.DEATH,
            ScoreAdjustment.AdjustmentType.CRIMINAL,
        ):
            obj.game_id = ""
            obj.game_name = ""
        super().save_model(request, obj, form, change)
        bump_leaderboard_cache_version()

    def delete_model(self, request, obj):
        super().delete_model(request, obj)
        bump_leaderboard_cache_version()

    def delete_queryset(self, request, queryset):
        super().delete_queryset(request, queryset)
        bump_leaderboard_cache_version()


@admin.register(ScoringConfig)
class ScoringConfigAdmin(admin.ModelAdmin):
    list_display = ("id", "cooldown_seconds", "default_points", "updated_at")


@admin.register(TwitchBroadcasterGrant)
class TwitchBroadcasterGrantAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "username",
        "broadcaster_user_id",
        "is_active",
        "created_at",
        "updated_at",
    )
    list_filter = ("is_active",)
    search_fields = ("username", "broadcaster_user_id")
    actions = ("deactivate_grants", "activate_grants")

    @admin.action(description="Deactivate selected broadcaster grants")
    def deactivate_grants(self, request, queryset):
        count = queryset.update(is_active=False)
        self.message_user(
            request,
            f"Deactivated {count} broadcaster grant(s).",
            level=messages.SUCCESS,
        )

    @admin.action(description="Activate selected broadcaster grants")
    def activate_grants(self, request, queryset):
        count = queryset.update(is_active=True)
        self.message_user(
            request,
            f"Activated {count} broadcaster grant(s).",
            level=messages.SUCCESS,
        )


@admin.register(TwitchBotGrant)
class TwitchBotGrantAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "bot_username",
        "bot_user_id",
        "is_active",
        "created_at",
        "updated_at",
    )
    list_filter = ("is_active",)
    search_fields = ("bot_username", "bot_user_id")
    actions = ("deactivate_grant", "activate_grant", "refresh_token")

    @admin.action(description="Deactivate bot grant")
    def deactivate_grant(self, request, queryset):
        count = queryset.update(is_active=False)
        self.message_user(
            request,
            f"Deactivated {count} bot grant(s).",
            level=messages.SUCCESS,
        )

    @admin.action(description="Activate bot grant")
    def activate_grant(self, request, queryset):
        count = queryset.update(is_active=True)
        self.message_user(
            request,
            f"Activated {count} bot grant(s).",
            level=messages.SUCCESS,
        )

    @admin.action(description="Manually refresh token (updates in next bot restart)")
    def refresh_token(self, request, queryset):
        # Note: actual refresh happens on bot startup
        self.message_user(
            request,
            "Bot token will be refreshed on next startup. Check bot logs for refresh status.",
            level=messages.INFO,
        )
