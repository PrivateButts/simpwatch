from datetime import timedelta

from django.conf import settings
from django.core.cache import cache
from django.db.models import Count
from django.db.models import Sum
from django.http import JsonResponse
from django.shortcuts import render
from django.utils import timezone

from .models import Person, ScoreAdjustment, SimpEvent
from .scoring import current_leaderboard_cache_version


WINDOWS = {
    "24h": timedelta(hours=24),
    "7d": timedelta(days=7),
    "30d": timedelta(days=30),
    "all": None,
}


def _cache_ttl() -> int:
    return max(int(getattr(settings, "LEADERBOARD_CACHE_TTL_SECONDS", 15)), 1)


def _cache_key(kind: str, window: str) -> str:
    version = current_leaderboard_cache_version()
    return f"leaderboard:v{version}:{kind}:window:{window}"


def _get_since(window: str):
    delta = WINDOWS.get(window, WINDOWS["all"])
    if delta is None:
        return None
    return timezone.now() - delta


def _leaderboard_rows(window: str):
    since = _get_since(window)
    event_qs = SimpEvent.objects.filter(event_type=SimpEvent.EventType.SIMP)
    adjustment_qs = ScoreAdjustment.objects.all()
    if since is not None:
        event_qs = event_qs.filter(created_at__gte=since)
        adjustment_qs = adjustment_qs.filter(created_at__gte=since)

    event_totals = {
        row["target_person"]: row["total"]
        for row in event_qs.values("target_person").annotate(total=Sum("points"))
    }
    adjustment_totals = {
        row["target_person"]: row["total"]
        for row in adjustment_qs.values("target_person").annotate(
            total=Sum("points_delta")
        )
    }
    person_ids = sorted(set(event_totals.keys()) | set(adjustment_totals.keys()))
    people = {p.id: p for p in Person.objects.filter(id__in=person_ids)}

    rows = []
    for person_id in person_ids:
        total = (event_totals.get(person_id) or 0) + (
            adjustment_totals.get(person_id) or 0
        )
        if total == 0:
            continue
        person = people.get(person_id)
        if not person:
            continue
        rows.append({"person": person, "points": total})
    rows.sort(key=lambda r: r["points"], reverse=True)
    return rows


def _recent_events(window: str):
    since = _get_since(window)
    qs = (
        SimpEvent.objects.filter(event_type=SimpEvent.EventType.SIMP)
        .select_related("actor_identity", "target_person")
        .order_by("-created_at")
    )
    if since is not None:
        qs = qs.filter(created_at__gte=since)
    return qs[:50]


def _narc_rows(window: str):
    since = _get_since(window)
    event_qs = SimpEvent.objects.filter(
        event_type=SimpEvent.EventType.SIMP
    ).select_related("actor_identity__person")
    if since is not None:
        event_qs = event_qs.filter(created_at__gte=since)

    counts = (
        event_qs.values("actor_identity__person")
        .annotate(callout_count=Count("id"))
        .order_by("-callout_count")
    )
    person_ids = [row["actor_identity__person"] for row in counts]
    people = {person.id: person for person in Person.objects.filter(id__in=person_ids)}

    rows = []
    for row in counts:
        person = people.get(row["actor_identity__person"])
        if not person:
            continue
        rows.append(
            {
                "person": person,
                "callout_count": row["callout_count"],
            }
        )
    return rows


def _bamder_total(window: str) -> int:
    since = _get_since(window)
    qs = SimpEvent.objects.filter(event_type=SimpEvent.EventType.BAMDER)
    if since is not None:
        qs = qs.filter(created_at__gte=since)
    return qs.count()


def _bamder_recent_events(window: str):
    since = _get_since(window)
    qs = (
        SimpEvent.objects.filter(event_type=SimpEvent.EventType.BAMDER)
        .select_related("actor_identity", "target_person")
        .order_by("-created_at")
    )
    if since is not None:
        qs = qs.filter(created_at__gte=since)
    return qs[:25]


def leaderboard_page(request):
    window = request.GET.get("window", "all")
    if window not in WINDOWS:
        window = "all"
    key = _cache_key("page", window)
    context = cache.get(key)
    if context is None:
        context = {
            "window": window,
            "windows": list(WINDOWS.keys()),
            "rows": _leaderboard_rows(window),
            "narc_rows": _narc_rows(window),
            "bamder_total": _bamder_total(window),
            "bamder_recent_events": _bamder_recent_events(window),
            "recent_events": _recent_events(window),
        }
        cache.set(key, context, _cache_ttl())
    return render(request, "simpwatch/leaderboard.html", context)


def leaderboard_api(request):
    window = request.GET.get("window", "all")
    if window not in WINDOWS:
        window = "all"
    key = _cache_key("api", window)
    payload = cache.get(key)
    if payload is None:
        rows = _leaderboard_rows(window)
        narc_rows = _narc_rows(window)
        bamder_total = _bamder_total(window)
        bamder_events = _bamder_recent_events(window)
        events = _recent_events(window)
        payload = {
            "window": window,
            "leaderboard": [
                {
                    "person_id": row["person"].id,
                    "name": row["person"].name,
                    "points": row["points"],
                }
                for row in rows
            ],
            "narc_leaderboard": [
                {
                    "person_id": row["person"].id,
                    "name": row["person"].name,
                    "callout_count": row["callout_count"],
                }
                for row in narc_rows
            ],
            "bamder_total": bamder_total,
            "bamder_recent_events": [
                {
                    "id": event.id,
                    "actor": event.actor_identity.username,
                    "reason": event.reason,
                    "source": event.source,
                    "created_at": event.created_at.isoformat(),
                }
                for event in bamder_events
            ],
            "recent_events": [
                {
                    "id": event.id,
                    "platform": event.platform,
                    "event_type": event.event_type,
                    "actor": event.actor_identity.username,
                    "target": event.target_person.name,
                    "points": event.points,
                    "reason": event.reason,
                    "source": event.source,
                    "created_at": event.created_at.isoformat(),
                }
                for event in events
            ],
        }
        cache.set(key, payload, _cache_ttl())
    return JsonResponse(payload)
