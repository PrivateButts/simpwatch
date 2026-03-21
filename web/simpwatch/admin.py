from django.contrib import admin
from django.contrib import messages

from .models import Identity, Person, ScoreAdjustment, ScoringConfig, SimpEvent
from .scoring import merge_people


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
        "platform",
        "actor_identity",
        "target_person",
        "points",
        "source",
        "created_at",
    )
    list_filter = ("platform", "created_at")
    search_fields = (
        "actor_identity__username",
        "target_person__name",
        "source",
        "message_id",
    )
    autocomplete_fields = ("actor_identity", "target_person")


@admin.register(ScoreAdjustment)
class ScoreAdjustmentAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "target_person",
        "points_delta",
        "reason",
        "created_by",
        "created_at",
    )
    list_filter = ("created_at",)
    search_fields = ("target_person__name", "reason")
    autocomplete_fields = ("target_person", "created_by")

    def save_model(self, request, obj, form, change):
        if not obj.created_by_id:
            obj.created_by = request.user
        super().save_model(request, obj, form, change)


@admin.register(ScoringConfig)
class ScoringConfigAdmin(admin.ModelAdmin):
    list_display = ("id", "cooldown_seconds", "default_points", "updated_at")
