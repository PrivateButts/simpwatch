import json
import logging
import urllib.error
import urllib.request
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

logger = logging.getLogger(__name__)


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


def _watched_channels() -> list[str]:
    return list(getattr(settings, "TWITCH_CHANNELS", []))


_TWITCH_CHANNEL_CACHE_KEY = "twitch_channel_data"
_TWITCH_CHANNEL_CACHE_TTL = 60  # seconds


def _fetch_twitch_channel_data(channels: list[str]) -> dict[str, dict]:
    """Fetch channel profile and live-stream data from the Twitch Helix API.

    Returns a mapping of login -> enriched dict, or empty dict on failure.
    """
    client_id: str = getattr(settings, "TWITCH_CLIENT_ID", "")
    token: str = getattr(settings, "TWITCH_OAUTH_TOKEN", "")
    # TwitchIO stores the token without the oauth: prefix, but strip it just in case.
    if token.lower().startswith("oauth:"):
        token = token[6:]
    if not client_id or not token:
        logger.debug(
            "Twitch channel enrichment skipped: TWITCH_CLIENT_ID=%s TWITCH_OAUTH_TOKEN=%s",
            "set" if client_id else "unset",
            "set" if token else "unset",
        )
        return {}

    headers = {
        "Client-Id": client_id,
        "Authorization": f"Bearer {token}",
    }

    user_params = "&".join(f"login={ch}" for ch in channels)
    stream_params = "&".join(f"user_login={ch}" for ch in channels)
    user_url = f"https://api.twitch.tv/helix/users?{user_params}"
    stream_url = f"https://api.twitch.tv/helix/streams?{stream_params}"

    try:
        req = urllib.request.Request(user_url, headers=headers)
        with urllib.request.urlopen(req, timeout=5) as resp:
            user_data = json.loads(resp.read())

        req = urllib.request.Request(stream_url, headers=headers)
        with urllib.request.urlopen(req, timeout=5) as resp:
            stream_data = json.loads(resp.read())
    except Exception:
        logger.warning("Failed to fetch Twitch channel data", exc_info=True)
        return {}

    users = {u["login"].lower(): u for u in user_data.get("data", [])}
    live = {s["user_login"].lower(): s for s in stream_data.get("data", [])}

    result: dict[str, dict] = {}
    for channel in channels:
        login = channel.lower()
        user = users.get(login, {})
        stream = live.get(login)
        result[login] = {
            "login": login,
            "display_name": user.get("display_name", channel),
            "profile_image_url": user.get("profile_image_url", ""),
            "is_live": stream is not None,
            "viewer_count": stream["viewer_count"] if stream else 0,
            "stream_title": stream["title"] if stream else "",
            "game_name": stream["game_name"] if stream else "",
            "has_data": bool(user),
        }
    return result


def _watched_channels_enriched() -> list[dict]:
    """Return channel list, enriched with Twitch API data when credentials are set."""
    channels = list(getattr(settings, "TWITCH_CHANNELS", []))
    if not channels:
        return []

    cached = cache.get(_TWITCH_CHANNEL_CACHE_KEY)
    if cached is not None:
        return cached

    api_data = _fetch_twitch_channel_data(channels)

    result = []
    for channel in channels:
        login = channel.lower()
        if login in api_data:
            result.append(api_data[login])
        else:
            result.append(
                {
                    "login": login,
                    "display_name": channel,
                    "profile_image_url": "",
                    "is_live": False,
                    "viewer_count": 0,
                    "stream_title": "",
                    "game_name": "",
                    "has_data": False,
                }
            )

    cache.set(_TWITCH_CHANNEL_CACHE_KEY, result, _TWITCH_CHANNEL_CACHE_TTL)
    return result


def healthcheck(request):
    return JsonResponse({"status": "ok"})


def leaderboard_page(request):
    window = request.GET.get("window", "all")
    if window not in WINDOWS:
        window = "all"
    key = _cache_key("page", window)
    context = cache.get(key)
    if context is None:
        channels = _watched_channels_enriched()
        context = {
            "window": window,
            "windows": list(WINDOWS.keys()),
            "rows": _leaderboard_rows(window),
            "narc_rows": _narc_rows(window),
            "bamder_total": _bamder_total(window),
            "bamder_recent_events": _bamder_recent_events(window),
            "recent_events": _recent_events(window),
            "watched_channels": channels,
            "twitch_configured": bool(
                getattr(settings, "TWITCH_BOT_USERNAME", "") or channels
            ),
            "twitch_bot_username": getattr(settings, "TWITCH_BOT_USERNAME", ""),
            "discord_configured": bool(getattr(settings, "DISCORD_BOT_TOKEN", "")),
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
