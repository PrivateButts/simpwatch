from datetime import timedelta

from django.db.models import Count
from django.db.models import Sum
from django.http import JsonResponse
from django.shortcuts import render
from django.utils import timezone

from .models import Person, ScoreAdjustment, SimpEvent


WINDOWS = {
    "24h": timedelta(hours=24),
    "7d": timedelta(days=7),
    "30d": timedelta(days=30),
    "all": None,
}


def _get_since(window: str):
    delta = WINDOWS.get(window, WINDOWS["all"])
    if delta is None:
        return None
    return timezone.now() - delta


def _leaderboard_rows(window: str):
    since = _get_since(window)
    event_qs = SimpEvent.objects.all()
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
    qs = SimpEvent.objects.select_related("actor_identity", "target_person").order_by(
        "-created_at"
    )
    if since is not None:
        qs = qs.filter(created_at__gte=since)
    return qs[:50]


def _narc_rows(window: str):
    since = _get_since(window)
    event_qs = SimpEvent.objects.select_related("actor_identity__person")
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


def leaderboard_page(request):
    window = request.GET.get("window", "all")
    if window not in WINDOWS:
        window = "all"
    context = {
        "window": window,
        "windows": WINDOWS.keys(),
        "rows": _leaderboard_rows(window),
        "narc_rows": _narc_rows(window),
        "recent_events": _recent_events(window),
    }
    return render(request, "simpwatch/leaderboard.html", context)


def leaderboard_api(request):
    window = request.GET.get("window", "all")
    if window not in WINDOWS:
        window = "all"
    rows = _leaderboard_rows(window)
    narc_rows = _narc_rows(window)
    events = _recent_events(window)
    return JsonResponse(
        {
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
            "recent_events": [
                {
                    "id": event.id,
                    "platform": event.platform,
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
    )
