import json
import logging
import hashlib
import secrets
import time
from itertools import chain
import urllib.error
import urllib.parse
import urllib.request
import base64
from datetime import timedelta
from datetime import datetime
from datetime import timezone as dt_timezone

from django.conf import settings
from django.core.cache import cache
from django.db.models import Count, Max
from django.db.models import Sum
from django.http import JsonResponse
from django.http import HttpResponse
from django.views.decorators.http import require_POST
from django.shortcuts import redirect
from django.shortcuts import render
from django.urls import reverse
from django.utils import timezone
from cryptography.fernet import Fernet

from .metrics import (
    http_request_duration_seconds,
    http_requests_total,
    leaderboard_cache_total,
    prometheus_payload,
)
from .models import Person, ScoreAdjustment, SimpEvent, TwitchBroadcasterGrant
from .scoring import (
    current_leaderboard_cache_version,
    score_adjustment_has_columns,
    score_adjustments_for_game_type,
    score_adjustments_for_type,
)

logger = logging.getLogger(__name__)

TWITCH_AUTHORIZE_URL = "https://id.twitch.tv/oauth2/authorize"
TWITCH_TOKEN_URL = "https://id.twitch.tv/oauth2/token"
TWITCH_VALIDATE_URL = "https://id.twitch.tv/oauth2/validate"
TWITCH_ONBOARD_STATE_CACHE_PREFIX = "twitch:onboard:state:"
TWITCH_BOT_REQUIRED_SCOPES = ["user:bot", "user:read:chat", "user:write:chat"]


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
    adjustment_qs = score_adjustments_for_type(ScoreAdjustment.AdjustmentType.SIMP)
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
    adjustment_qs = score_adjustments_for_type(ScoreAdjustment.AdjustmentType.BAMDER)
    if since is not None:
        qs = qs.filter(created_at__gte=since)
        adjustment_qs = adjustment_qs.filter(created_at__gte=since)
    return (qs.aggregate(total=Sum("points"))["total"] or 0) + (
        adjustment_qs.aggregate(total=Sum("points_delta"))["total"] or 0
    )


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


def _banthem_rows(window: str):
    since = _get_since(window)
    event_qs = SimpEvent.objects.filter(event_type=SimpEvent.EventType.BANTHEM)
    adjustment_qs = score_adjustments_for_type(
        ScoreAdjustment.AdjustmentType.BANTHEM
    )
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
    rows.sort(key=lambda row: (-row["points"], row["person"].name.lower()))
    return rows


def _banthem_recent_events(window: str):
    since = _get_since(window)
    qs = (
        SimpEvent.objects.filter(event_type=SimpEvent.EventType.BANTHEM)
        .select_related("actor_identity", "target_person")
        .order_by("-created_at")
    )
    if since is not None:
        qs = qs.filter(created_at__gte=since)
    return qs[:25]


def _death_game_options() -> list[dict]:
    event_rows = (
        SimpEvent.objects.filter(event_type=SimpEvent.EventType.DEATH)
        .values("game_id", "game_name")
        .annotate(last_seen=Max("created_at"))
        .order_by("-last_seen")
    )
    if score_adjustment_has_columns("adjustment_type", "game_id", "game_name"):
        adjustment_rows = (
            score_adjustments_for_game_type(ScoreAdjustment.AdjustmentType.DEATH)
            .values("game_id", "game_name")
            .annotate(last_seen=Max("created_at"))
            .order_by("-last_seen")
        )
    else:
        adjustment_rows = []
    seen_game_ids: set[str] = set()
    has_unknown = False
    options: list[dict] = []

    for row in chain(event_rows, adjustment_rows):
        game_id = (row.get("game_id") or "").strip()
        game_name = (row.get("game_name") or "").strip()
        if game_id:
            if game_id in seen_game_ids:
                continue
            seen_game_ids.add(game_id)
            options.append(
                {
                    "game_id": game_id,
                    "game_name": game_name or f"Game {game_id}",
                }
            )
        else:
            has_unknown = True

    options.sort(key=lambda row: row["game_name"].lower())
    if has_unknown:
        options.append({"game_id": "unknown", "game_name": "Unknown"})
    return options


def _deathboard_rows_alltime(selected_game_id: str = "") -> list[dict]:
    event_qs = SimpEvent.objects.filter(event_type=SimpEvent.EventType.DEATH)
    adjustment_qs = score_adjustments_for_game_type(
        ScoreAdjustment.AdjustmentType.DEATH
    )
    if selected_game_id == "unknown":
        event_qs = event_qs.filter(game_id="")
        adjustment_qs = adjustment_qs.filter(game_id="")
    elif selected_game_id:
        event_qs = event_qs.filter(game_id=selected_game_id)
        adjustment_qs = adjustment_qs.filter(game_id=selected_game_id)

    event_totals = {
        row["target_person"]: row["death_count"]
        for row in event_qs.values("target_person").annotate(death_count=Sum("points"))
    }
    adjustment_totals = {
        row["target_person"]: row["adjustment_total"]
        for row in adjustment_qs.values("target_person").annotate(
            adjustment_total=Sum("points_delta")
        )
    }
    person_ids = sorted(set(event_totals.keys()) | set(adjustment_totals.keys()))
    people = {person.id: person for person in Person.objects.filter(id__in=person_ids)}

    rows = []
    for person_id in person_ids:
        person = people.get(person_id)
        if not person:
            continue
        death_count = event_totals.get(person_id, 0) + adjustment_totals.get(
            person_id, 0
        )
        if death_count == 0:
            continue
        rows.append(
            {
                "person": person,
                "death_count": death_count,
            }
        )
    rows.sort(key=lambda row: (-row["death_count"], row["person"].id))
    return rows


def _recent_death_events_alltime(selected_game_id: str = ""):
    qs = SimpEvent.objects.filter(event_type=SimpEvent.EventType.DEATH)
    if selected_game_id == "unknown":
        qs = qs.filter(game_id="")
    elif selected_game_id:
        qs = qs.filter(game_id=selected_game_id)
    return qs.select_related("actor_identity", "target_person").order_by("-created_at")[:25]


def _games_by_death_count() -> list[dict]:
    """Get games ranked by total death count."""
    event_counts = (
        SimpEvent.objects.filter(event_type=SimpEvent.EventType.DEATH)
        .values("game_id", "game_name")
        .annotate(total_deaths=Sum("points"))
    )
    if score_adjustment_has_columns("adjustment_type", "game_id", "game_name"):
        adjustment_counts = (
            score_adjustments_for_game_type(ScoreAdjustment.AdjustmentType.DEATH)
            .values("game_id", "game_name")
            .annotate(total_deaths=Sum("points_delta"))
        )
    else:
        adjustment_counts = []

    totals_by_game_id: dict[str, int] = {}
    names_by_game_id: dict[str, str] = {}
    for row in chain(event_counts, adjustment_counts):
        game_id = (row.get("game_id") or "").strip()
        game_name = (row.get("game_name") or "").strip()
        total_deaths = row.get("total_deaths") or 0
        key = game_id or "unknown"
        totals_by_game_id[key] = totals_by_game_id.get(key, 0) + total_deaths
        if key not in names_by_game_id and game_name:
            names_by_game_id[key] = game_name

    games = []
    for game_id, total_deaths in totals_by_game_id.items():
        if total_deaths == 0:
            continue
        if game_id == "unknown":
            game_name = "Unknown"
        else:
            game_name = names_by_game_id.get(game_id) or f"Game {game_id}"
        games.append(
            {
                "game_id": game_id,
                "game_name": game_name,
                "total_deaths": total_deaths,
            }
        )
    games.sort(key=lambda row: (-row["total_deaths"], row["game_name"].lower()))

    return games


def _criminal_game_options() -> list[dict]:
    event_rows = (
        SimpEvent.objects.filter(event_type=SimpEvent.EventType.CRIMINAL)
        .values("game_id", "game_name")
        .annotate(last_seen=Max("created_at"))
        .order_by("-last_seen")
    )
    if score_adjustment_has_columns("adjustment_type", "game_id", "game_name"):
        adjustment_rows = (
            score_adjustments_for_game_type(ScoreAdjustment.AdjustmentType.CRIMINAL)
            .values("game_id", "game_name")
            .annotate(last_seen=Max("created_at"))
            .order_by("-last_seen")
        )
    else:
        adjustment_rows = []
    seen_game_ids: set[str] = set()
    has_unknown = False
    options: list[dict] = []

    for row in chain(event_rows, adjustment_rows):
        game_id = (row.get("game_id") or "").strip()
        game_name = (row.get("game_name") or "").strip()
        if game_id:
            if game_id in seen_game_ids:
                continue
            seen_game_ids.add(game_id)
            options.append(
                {
                    "game_id": game_id,
                    "game_name": game_name or f"Game {game_id}",
                }
            )
        else:
            has_unknown = True

    options.sort(key=lambda row: row["game_name"].lower())
    if has_unknown:
        options.append({"game_id": "unknown", "game_name": "Unknown"})
    return options


def _crimeboard_rows_alltime(selected_game_id: str = "") -> list[dict]:
    event_qs = SimpEvent.objects.filter(event_type=SimpEvent.EventType.CRIMINAL)
    adjustment_qs = score_adjustments_for_game_type(
        ScoreAdjustment.AdjustmentType.CRIMINAL
    )
    if selected_game_id == "unknown":
        event_qs = event_qs.filter(game_id="")
        adjustment_qs = adjustment_qs.filter(game_id="")
    elif selected_game_id:
        event_qs = event_qs.filter(game_id=selected_game_id)
        adjustment_qs = adjustment_qs.filter(game_id=selected_game_id)

    event_totals = {
        row["target_person"]: row["crime_count"]
        for row in event_qs.values("target_person").annotate(crime_count=Sum("points"))
    }
    adjustment_totals = {
        row["target_person"]: row["adjustment_total"]
        for row in adjustment_qs.values("target_person").annotate(
            adjustment_total=Sum("points_delta")
        )
    }
    person_ids = sorted(set(event_totals.keys()) | set(adjustment_totals.keys()))
    people = {person.id: person for person in Person.objects.filter(id__in=person_ids)}

    rows = []
    for person_id in person_ids:
        person = people.get(person_id)
        if not person:
            continue
        crime_count = event_totals.get(person_id, 0) + adjustment_totals.get(
            person_id, 0
        )
        if crime_count == 0:
            continue
        rows.append(
            {
                "person": person,
                "crime_count": crime_count,
            }
        )
    rows.sort(key=lambda row: (-row["crime_count"], row["person"].id))
    return rows


def _recent_crime_events_alltime(selected_game_id: str = ""):
    qs = SimpEvent.objects.filter(event_type=SimpEvent.EventType.CRIMINAL)
    if selected_game_id == "unknown":
        qs = qs.filter(game_id="")
    elif selected_game_id:
        qs = qs.filter(game_id=selected_game_id)
    return qs.select_related("actor_identity", "target_person").order_by("-created_at")[:25]


def _games_by_crime_count() -> list[dict]:
    """Get games ranked by total crime count."""
    event_counts = (
        SimpEvent.objects.filter(event_type=SimpEvent.EventType.CRIMINAL)
        .values("game_id", "game_name")
        .annotate(total_crimes=Sum("points"))
    )
    if score_adjustment_has_columns("adjustment_type", "game_id", "game_name"):
        adjustment_counts = (
            score_adjustments_for_game_type(ScoreAdjustment.AdjustmentType.CRIMINAL)
            .values("game_id", "game_name")
            .annotate(total_crimes=Sum("points_delta"))
        )
    else:
        adjustment_counts = []

    totals_by_game_id: dict[str, int] = {}
    names_by_game_id: dict[str, str] = {}
    for row in chain(event_counts, adjustment_counts):
        game_id = (row.get("game_id") or "").strip()
        game_name = (row.get("game_name") or "").strip()
        total_crimes = row.get("total_crimes") or 0
        key = game_id or "unknown"
        totals_by_game_id[key] = totals_by_game_id.get(key, 0) + total_crimes
        if key not in names_by_game_id and game_name:
            names_by_game_id[key] = game_name

    games = []
    for game_id, total_crimes in totals_by_game_id.items():
        if total_crimes == 0:
            continue
        if game_id == "unknown":
            game_name = "Unknown"
        else:
            game_name = names_by_game_id.get(game_id) or f"Game {game_id}"
        games.append(
            {
                "game_id": game_id,
                "game_name": game_name,
                "total_crimes": total_crimes,
            }
        )
    games.sort(key=lambda row: (-row["total_crimes"], row["game_name"].lower()))

    return games


def _watched_channels() -> list[str]:
    from .twitch_channels import get_monitored_channels

    return get_monitored_channels()


def _normalized_watched_channels() -> set[str]:
    return {channel.strip().lower() for channel in _watched_channels() if channel.strip()}


def _exchange_twitch_code_for_tokens(code: str, *, redirect_uri: str | None = None) -> dict:
    if not redirect_uri:
        redirect_uri = getattr(settings, "TWITCH_TOKEN_REDIRECT_URI", "")
    redirect_uri = str(redirect_uri).strip()
    body = urllib.parse.urlencode(
        {
            "client_id": getattr(settings, "TWITCH_CLIENT_ID", ""),
            "client_secret": getattr(settings, "TWITCH_CLIENT_SECRET", ""),
            "code": code,
            "grant_type": "authorization_code",
            "redirect_uri": redirect_uri,
        }
    ).encode("utf-8")
    request = urllib.request.Request(TWITCH_TOKEN_URL, data=body, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            return json.loads(response.read())
    except urllib.error.HTTPError as exc:
        raw_body = ""
        try:
            raw_body = exc.read().decode("utf-8", errors="replace")
        except Exception:
            raw_body = ""

        detail = "unknown_error"
        if raw_body:
            try:
                payload = json.loads(raw_body)
                detail = (
                    str(payload.get("message") or payload.get("error") or "").strip()
                    or detail
                )
            except json.JSONDecodeError:
                detail = raw_body.strip() or detail

        raise RuntimeError(
            f"Twitch token exchange failed ({exc.code}): {detail}"
        ) from exc


def _bot_oauth_redirect_uri(request) -> str:
    configured = getattr(settings, "TWITCH_BOT_TOKEN_REDIRECT_URI", "").strip()
    if configured:
        return configured

    # Preserve host/scheme from the existing broadcaster redirect config.
    legacy = getattr(settings, "TWITCH_TOKEN_REDIRECT_URI", "").strip()
    if legacy:
        parsed = urllib.parse.urlparse(legacy)
        if parsed.scheme and parsed.netloc:
            return urllib.parse.urlunparse(
                (
                    parsed.scheme,
                    parsed.netloc,
                    "/oauth/twitch/bot/callback",
                    "",
                    "",
                    "",
                )
            )

    return request.build_absolute_uri(reverse("twitch_bot_token_callback"))


def _validate_twitch_access_token(access_token: str) -> dict:
    request = urllib.request.Request(
        TWITCH_VALIDATE_URL,
        headers={"Authorization": f"OAuth {access_token}"},
        method="GET",
    )
    with urllib.request.urlopen(request, timeout=10) as response:
        return json.loads(response.read())


def _token_cipher() -> Fernet:
    raw_key = (
        getattr(settings, "TWITCH_GRANT_ENCRYPTION_KEY", "").strip()
        or settings.SECRET_KEY
    )
    digest = hashlib.sha256(raw_key.encode("utf-8")).digest()
    fernet_key = base64.urlsafe_b64encode(digest)
    return Fernet(fernet_key)


def _encrypt_token(plain_text: str) -> str:
    return _token_cipher().encrypt(plain_text.encode("utf-8")).decode("utf-8")


def _decrypt_token(encrypted_text: str) -> str:
    """Decrypt a token encrypted with _encrypt_token()."""
    return _token_cipher().decrypt(encrypted_text.encode("utf-8")).decode("utf-8")


_TWITCH_CHANNEL_CACHE_KEY = "twitch_channel_data"
_TWITCH_CHANNEL_CACHE_TTL = 60  # seconds
_TWITCH_APP_TOKEN_CACHE_KEY = "twitch:app_access_token"
# App tokens live up to 60 days; cache for 24h so we refresh well before expiry.
_TWITCH_APP_TOKEN_CACHE_TTL = 86400


def _get_app_access_token(client_id: str, client_secret: str) -> str | None:
    """Return a cached Twitch App Access Token, fetching a new one if needed.

    Uses the OAuth2 client_credentials flow — no user auth required.
    Returns None on failure.
    """
    cached = cache.get(_TWITCH_APP_TOKEN_CACHE_KEY)
    if cached:
        return str(cached)

    try:
        body = urllib.parse.urlencode(
            {
                "client_id": client_id,
                "client_secret": client_secret,
                "grant_type": "client_credentials",
            }
        ).encode("utf-8")
        req = urllib.request.Request(TWITCH_TOKEN_URL, data=body, method="POST")
        with urllib.request.urlopen(req, timeout=10) as resp:
            payload = json.loads(resp.read())
        token = str(payload.get("access_token", "")).strip()
        if not token:
            logger.warning("Twitch app token response missing access_token")
            return None
        # Honour the expires_in from the response when available.
        expires_in = int(payload.get("expires_in") or _TWITCH_APP_TOKEN_CACHE_TTL)
        ttl = min(expires_in - 300, _TWITCH_APP_TOKEN_CACHE_TTL)  # 5-min safety margin
        cache.set(_TWITCH_APP_TOKEN_CACHE_KEY, token, max(ttl, 60))
        logger.debug("Fetched new Twitch app access token, expires_in=%d", expires_in)
        return token
    except Exception:
        logger.warning("Failed to fetch Twitch app access token", exc_info=True)
        return None


def _fetch_twitch_channel_data(channels: list[str]) -> dict[str, dict]:
    """Fetch channel profile and live-stream data from the Twitch Helix API.

    Uses the App Access Token (client credentials) so no bot user token is required.
    Returns a mapping of login -> enriched dict, or empty dict on failure.
    """
    client_id: str = getattr(settings, "TWITCH_CLIENT_ID", "").strip()
    client_secret: str = getattr(settings, "TWITCH_CLIENT_SECRET", "").strip()
    if not client_id or not client_secret:
        logger.debug(
            "Twitch channel enrichment skipped: TWITCH_CLIENT_ID=%s TWITCH_CLIENT_SECRET=%s",
            "set" if client_id else "unset",
            "set" if client_secret else "unset",
        )
        return {}

    token = _get_app_access_token(client_id, client_secret)
    if not token:
        return {}

    user_params = "&".join(f"login={ch}" for ch in channels)
    stream_params = "&".join(f"user_login={ch}" for ch in channels)
    user_url = f"https://api.twitch.tv/helix/users?{user_params}"
    stream_url = f"https://api.twitch.tv/helix/streams?{stream_params}"

    def _load_helix_payloads(current_token: str) -> tuple[dict, dict]:
        headers = {
            "Client-Id": client_id,
            "Authorization": f"Bearer {current_token}",
        }
        req = urllib.request.Request(user_url, headers=headers)
        with urllib.request.urlopen(req, timeout=5) as resp:
            user_data = json.loads(resp.read())

        req = urllib.request.Request(stream_url, headers=headers)
        with urllib.request.urlopen(req, timeout=5) as resp:
            stream_data = json.loads(resp.read())

        return user_data, stream_data

    try:
        user_data, stream_data = _load_helix_payloads(token)
    except urllib.error.HTTPError as exc:
        # Cached app token can become invalid before TTL (revocation/rotation).
        # Purge and fetch a fresh token once before giving up.
        if exc.code in (401, 403):
            logger.warning(
                "Twitch Helix auth failed with cached app token (status=%s); refreshing app token cache",
                exc.code,
            )
            cache.delete(_TWITCH_APP_TOKEN_CACHE_KEY)
            fresh_token = _get_app_access_token(client_id, client_secret)
            if fresh_token:
                try:
                    user_data, stream_data = _load_helix_payloads(fresh_token)
                except Exception:
                    logger.warning(
                        "Failed to fetch Twitch channel data after refreshing app token",
                        exc_info=True,
                    )
                    return {}
            else:
                return {}
        else:
            logger.warning("Failed to fetch Twitch channel data", exc_info=True)
            return {}
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
    from .twitch_channels import get_monitored_channels

    channels = get_monitored_channels()
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

    active_grant_logins = set(
        TwitchBroadcasterGrant.objects.filter(
            is_active=True,
            username__in=[ch["login"] for ch in result],
        ).values_list("username", flat=True)
    )
    for ch in result:
        ch["has_grant"] = ch["login"] in active_grant_logins

    cache.set(_TWITCH_CHANNEL_CACHE_KEY, result, _TWITCH_CHANNEL_CACHE_TTL)
    return result


def healthcheck(request):
    started = time.monotonic()
    response = JsonResponse({"status": "ok"})
    duration = max(time.monotonic() - started, 0.0)
    endpoint = "healthcheck"
    http_request_duration_seconds.labels(request.method, endpoint).observe(duration)
    http_requests_total.labels(request.method, endpoint, "2xx").inc()
    return response


_BOT_HEARTBEAT_TTL_SECONDS = 120
_BOT_HEARTBEAT_MAX_AGE_SECONDS = 90


def bot_health(request):
    started = time.monotonic()
    raw = cache.get("twitch:bot:heartbeat")
    if raw is not None:
        try:
            last_seen = int(str(raw))
            age = int(time.time()) - last_seen
            if 0 <= age <= _BOT_HEARTBEAT_MAX_AGE_SECONDS:
                status, http_code = "ok", 200
            else:
                status, http_code = "degraded", 200
        except (ValueError, TypeError):
            status, http_code = "degraded", 200
    else:
        status, http_code = "degraded", 503

    response = JsonResponse(
        {
            "status": status,
            "bot_alive": status == "ok",
        }
    )
    duration = max(time.monotonic() - started, 0.0)
    endpoint = "bot_health"
    http_request_duration_seconds.labels(request.method, endpoint).observe(duration)
    http_requests_total.labels(
        request.method, endpoint, "2xx" if http_code < 400 else f"{http_code // 100}xx"
    ).inc()
    return response


def metrics_view(request):
    payload, content_type = prometheus_payload()
    return HttpResponse(payload, content_type=content_type)


def twitch_onboard_start(request):
    client_id = getattr(settings, "TWITCH_CLIENT_ID", "").strip()
    redirect_uri = getattr(settings, "TWITCH_TOKEN_REDIRECT_URI", "").strip()
    scopes = getattr(settings, "TWITCH_BROADCASTER_TOKEN_SCOPES", "channel:bot")
    if not client_id or not redirect_uri:
        return JsonResponse(
            {
                "ok": False,
                "error": "missing_config",
                "detail": "TWITCH_CLIENT_ID and TWITCH_TOKEN_REDIRECT_URI are required.",
            },
            status=500,
        )

    state = secrets.token_urlsafe(24)
    ttl = max(int(getattr(settings, "TWITCH_ONBOARD_STATE_TTL_SECONDS", 600)), 60)
    cache.set(f"{TWITCH_ONBOARD_STATE_CACHE_PREFIX}{state}", "1", ttl)

    params = urllib.parse.urlencode(
        {
            "response_type": "code",
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "scope": scopes,
            "state": state,
            "force_verify": "true",
        }
    )
    return redirect(f"{TWITCH_AUTHORIZE_URL}?{params}")


def twitch_onboard_callback(request):
    error = request.GET.get("error", "").strip()
    if error:
        return JsonResponse(
            {
                "ok": False,
                "error": "oauth_denied",
                "detail": request.GET.get("error_description", "authorization denied"),
            },
            status=400,
        )

    state = request.GET.get("state", "").strip()
    code = request.GET.get("code", "").strip()
    if not state or not code:
        return JsonResponse(
            {"ok": False, "error": "invalid_callback", "detail": "Missing state or code."},
            status=400,
        )

    state_cache_key = f"{TWITCH_ONBOARD_STATE_CACHE_PREFIX}{state}"
    if not cache.get(state_cache_key):
        return JsonResponse(
            {"ok": False, "error": "invalid_state", "detail": "OAuth state is invalid or expired."},
            status=400,
        )
    cache.delete(state_cache_key)

    try:
        token_data = _exchange_twitch_code_for_tokens(code)
    except Exception:
        logger.exception("Failed exchanging Twitch OAuth code for tokens")
        return JsonResponse(
            {"ok": False, "error": "token_exchange_failed", "detail": "Unable to exchange OAuth code."},
            status=502,
        )

    access_token = str(token_data.get("access_token", "")).strip()
    refresh_token = str(token_data.get("refresh_token", "")).strip()
    if not access_token or not refresh_token:
        return JsonResponse(
            {"ok": False, "error": "token_exchange_failed", "detail": "Missing access or refresh token."},
            status=502,
        )

    try:
        validation = _validate_twitch_access_token(access_token)
    except Exception:
        logger.exception("Failed validating Twitch OAuth token")
        return JsonResponse(
            {"ok": False, "error": "token_validation_failed", "detail": "Unable to validate access token."},
            status=502,
        )

    login = str(validation.get("login", "")).strip().lower()
    user_id = str(validation.get("user_id", "")).strip()
    scopes = validation.get("scopes", []) or []
    if not login or not user_id:
        return JsonResponse(
            {"ok": False, "error": "token_validation_failed", "detail": "Validation response missing user identity."},
            status=502,
        )

    if login not in _normalized_watched_channels():
        logger.info(
            "Rejected onboarding token for unconfigured channel login=%s",
            login,
        )
        return JsonResponse(
            {
                "ok": False,
                "error": "channel_not_configured",
                "detail": "Broadcaster login is not configured as a monitored Twitch channel.",
                "login": login,
            },
            status=403,
        )

    expires_in = int(token_data.get("expires_in") or 0)
    expires_at = None
    if expires_in > 0:
        expires_at = datetime.now(dt_timezone.utc) + timedelta(seconds=expires_in)

    TwitchBroadcasterGrant.objects.update_or_create(
        username=login,
        defaults={
            "broadcaster_user_id": user_id,
            "access_token": _encrypt_token(access_token),
            "refresh_token": _encrypt_token(refresh_token),
            "scopes": " ".join(scopes),
            "expires_at": expires_at,
            "is_active": True,
        },
    )

    return JsonResponse({"ok": True, "login": login, "stored": True})


@require_POST
def twitch_onboard_revoke(request):
    user = getattr(request, "user", None)
    if user is None or not user.is_authenticated or not user.is_staff:
        return JsonResponse(
            {"ok": False, "error": "forbidden", "detail": "Staff authentication required."},
            status=403,
        )

    username = (request.POST.get("username") or "").strip().lower()
    if not username:
        return JsonResponse(
            {"ok": False, "error": "invalid_request", "detail": "username is required."},
            status=400,
        )

    updated = TwitchBroadcasterGrant.objects.filter(username=username).update(
        is_active=False,
    )
    if not updated:
        return JsonResponse(
            {"ok": False, "error": "not_found", "detail": "No broadcaster grant found."},
            status=404,
        )

    return JsonResponse({"ok": True, "revoked": True, "username": username})


def twitch_bot_token_start(request):
    """Initiate Twitch OAuth flow to set up bot account tokens.

    Only staff users can start the bot token setup flow.
    Returns JSON with oauth_url to redirect to.
    """
    user = getattr(request, "user", None)
    if user is None or not user.is_authenticated or not user.is_staff:
        return JsonResponse(
            {"ok": False, "error": "forbidden", "detail": "Staff authentication required."},
            status=403,
        )

    client_id = getattr(settings, "TWITCH_CLIENT_ID", "").strip()
    client_secret = getattr(settings, "TWITCH_CLIENT_SECRET", "").strip()
    redirect_uri = _bot_oauth_redirect_uri(request)
    if not client_id or not client_secret or not redirect_uri:
        logger.error(
            "Bot token setup not available: TWITCH_CLIENT_ID=%s TWITCH_CLIENT_SECRET=%s TWITCH_BOT_TOKEN_REDIRECT_URI=%s",
            "set" if client_id else "unset",
            "set" if client_secret else "unset",
            "set" if redirect_uri else "unset",
        )
        return JsonResponse(
            {
                "ok": False,
                "error": "misconfigured",
                "detail": "Bot token setup is not configured. Set TWITCH_CLIENT_ID, TWITCH_CLIENT_SECRET, and TWITCH_BOT_TOKEN_REDIRECT_URI.",
            },
            status=500,
        )

    state = secrets.token_urlsafe(32)
    state_cache_key = f"{TWITCH_ONBOARD_STATE_CACHE_PREFIX}bot:{state}"
    ttl = getattr(settings, "TWITCH_ONBOARD_STATE_TTL_SECONDS", 600)
    cache.set(state_cache_key, {"redirect_uri": redirect_uri}, ttl)

    scopes = getattr(
        settings,
        "TWITCH_BOT_TOKEN_SCOPES",
        " ".join(TWITCH_BOT_REQUIRED_SCOPES),
    ).split()
    if not scopes:
        scopes = list(TWITCH_BOT_REQUIRED_SCOPES)
    oauth_url = (
        f"https://id.twitch.tv/oauth2/authorize"
        f"?client_id={urllib.parse.quote(client_id)}"
        f"&redirect_uri={urllib.parse.quote(redirect_uri)}"
        f"&response_type=code"
        f"&scope={urllib.parse.quote(' '.join(scopes))}"
        f"&state={urllib.parse.quote(state)}"
    )

    return JsonResponse({"ok": True, "oauth_url": oauth_url})


def twitch_bot_token_callback(request):
    """Handle Twitch OAuth callback for bot account tokens.

    Exchanges code for tokens, validates token identity, and stores in DB with encryption.
    """
    error = request.GET.get("error", "").strip()
    if error:
        return JsonResponse(
            {
                "ok": False,
                "error": "oauth_denied",
                "detail": request.GET.get("error_description", "authorization denied"),
            },
            status=400,
        )

    state = request.GET.get("state", "").strip()
    code = request.GET.get("code", "").strip()
    if not state or not code:
        return JsonResponse(
            {"ok": False, "error": "invalid_callback", "detail": "Missing state or code."},
            status=400,
        )

    state_cache_key = f"{TWITCH_ONBOARD_STATE_CACHE_PREFIX}bot:{state}"
    state_data = cache.get(state_cache_key)
    if not state_data:
        return JsonResponse(
            {"ok": False, "error": "invalid_state", "detail": "OAuth state is invalid or expired."},
            status=400,
        )
    cache.delete(state_cache_key)

    redirect_uri = _bot_oauth_redirect_uri(request)
    if isinstance(state_data, dict):
        redirect_uri = str(state_data.get("redirect_uri") or "").strip() or redirect_uri

    try:
        token_data = _exchange_twitch_code_for_tokens(code, redirect_uri=redirect_uri)
    except Exception as exc:
        logger.exception("Failed exchanging Twitch OAuth code for bot tokens")
        return JsonResponse(
            {"ok": False, "error": "token_exchange_failed", "detail": str(exc)},
            status=502,
        )

    access_token = str(token_data.get("access_token", "")).strip()
    refresh_token = str(token_data.get("refresh_token", "")).strip()
    if not access_token or not refresh_token:
        return JsonResponse(
            {"ok": False, "error": "token_exchange_failed", "detail": "Missing access or refresh token."},
            status=502,
        )

    try:
        validation = _validate_twitch_access_token(access_token)
    except Exception:
        logger.exception("Failed validating Twitch OAuth token for bot")
        return JsonResponse(
            {"ok": False, "error": "token_validation_failed", "detail": "Unable to validate access token."},
            status=502,
        )

    bot_username = str(validation.get("login", "")).strip().lower()
    bot_user_id = str(validation.get("user_id", "")).strip()
    scopes = validation.get("scopes", []) or []
    missing_scopes = sorted(set(TWITCH_BOT_REQUIRED_SCOPES) - set(scopes))
    if not bot_username or not bot_user_id:
        return JsonResponse(
            {"ok": False, "error": "token_validation_failed", "detail": "Validation response missing user identity."},
            status=502,
        )
    if missing_scopes:
        return JsonResponse(
            {
                "ok": False,
                "error": "insufficient_scopes",
                "detail": (
                    "Bot token is missing required EventSub scopes: "
                    + ", ".join(missing_scopes)
                ),
                "missing_scopes": missing_scopes,
            },
            status=400,
        )

    expires_in = int(token_data.get("expires_in") or 0)
    expires_at = None
    if expires_in > 0:
        expires_at = datetime.now(dt_timezone.utc) + timedelta(seconds=expires_in)

    from .models import TwitchBotGrant  # noqa: E402

    TwitchBotGrant.objects.update_or_create(
        bot_username=bot_username,
        defaults={
            "bot_user_id": bot_user_id,
            "access_token": _encrypt_token(access_token),
            "refresh_token": _encrypt_token(refresh_token),
            "scopes": " ".join(scopes),
            "expires_at": expires_at,
            "is_active": True,
        },
    )

    logger.info("Successfully stored bot OAuth grant for bot_username=%s", bot_username)

    return JsonResponse({"ok": True, "bot_username": bot_username, "stored": True})


def leaderboard_page(request):
    started = time.monotonic()
    window = request.GET.get("window", "all")
    if window not in WINDOWS:
        window = "all"
    key = _cache_key("page", window)
    context = cache.get(key)
    if context is None:
        leaderboard_cache_total.labels("page", "miss").inc()
        channels = _watched_channels_enriched()
        context = {
            "window": window,
            "windows": list(WINDOWS.keys()),
            "rows": _leaderboard_rows(window),
            "narc_rows": _narc_rows(window),
            "bamder_total": _bamder_total(window),
            "bamder_recent_events": _bamder_recent_events(window),
            "banthem_rows": _banthem_rows(window),
            "banthem_recent_events": _banthem_recent_events(window),
            "recent_events": _recent_events(window),
            "watched_channels": channels,
            "twitch_configured": bool(
                getattr(settings, "TWITCH_BOT_USERNAME", "") or channels
            ),
            "twitch_bot_username": getattr(settings, "TWITCH_BOT_USERNAME", ""),
            "discord_configured": bool(getattr(settings, "DISCORD_BOT_TOKEN", "")),
        }
        cache.set(key, context, _cache_ttl())
    else:
        leaderboard_cache_total.labels("page", "hit").inc()
    response = render(request, "simpwatch/leaderboard.html", context)
    duration = max(time.monotonic() - started, 0.0)
    endpoint = "leaderboard_page"
    http_request_duration_seconds.labels(request.method, endpoint).observe(duration)
    http_requests_total.labels(request.method, endpoint, "2xx").inc()
    return response


def leaderboard_api(request):
    started = time.monotonic()
    window = request.GET.get("window", "all")
    if window not in WINDOWS:
        window = "all"
    key = _cache_key("api", window)
    payload = cache.get(key)
    if payload is None:
        leaderboard_cache_total.labels("api", "miss").inc()
        rows = _leaderboard_rows(window)
        narc_rows = _narc_rows(window)
        bamder_total = _bamder_total(window)
        bamder_events = _bamder_recent_events(window)
        banthem_rows = _banthem_rows(window)
        banthem_events = _banthem_recent_events(window)
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
                    "target": event.target_person.name,
                    "reason": event.reason,
                    "source": event.source,
                    "created_at": event.created_at.isoformat(),
                }
                for event in bamder_events
            ],
            "banthem_leaderboard": [
                {
                    "person_id": row["person"].id,
                    "name": row["person"].name,
                    "points": row["points"],
                }
                for row in banthem_rows
            ],
            "banthem_recent_events": [
                {
                    "id": event.id,
                    "actor": event.actor_identity.username,
                    "target": event.target_person.name,
                    "reason": event.reason,
                    "source": event.source,
                    "created_at": event.created_at.isoformat(),
                }
                for event in banthem_events
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
    else:
        leaderboard_cache_total.labels("api", "hit").inc()
    response = JsonResponse(payload)
    duration = max(time.monotonic() - started, 0.0)
    endpoint = "leaderboard_api"
    http_request_duration_seconds.labels(request.method, endpoint).observe(duration)
    http_requests_total.labels(request.method, endpoint, "2xx").inc()
    return response


def deathboard_page(request):
    started = time.monotonic()
    selected_game_id = request.GET.get("game", "").strip()
    key = _cache_key("deathboard_page", f"all:{selected_game_id or 'all'}")
    context = cache.get(key)
    if context is None:
        leaderboard_cache_total.labels("deathboard_page", "miss").inc()
        channels = _watched_channels_enriched()
        games = _death_game_options()
        selected_game_name = ""
        if selected_game_id:
            for game in games:
                if game.get("game_id") == selected_game_id:
                    selected_game_name = game.get("game_name", "")
                    break
        context = {
            "rows": _deathboard_rows_alltime(selected_game_id),
            "recent_events": _recent_death_events_alltime(selected_game_id),
            "games": games,
            "selected_game_id": selected_game_id,
            "selected_game_name": selected_game_name,
            "most_lethal_games": _games_by_death_count(),
            "watched_channels": channels,
            "twitch_configured": bool(
                getattr(settings, "TWITCH_BOT_USERNAME", "") or channels
            ),
            "twitch_bot_username": getattr(settings, "TWITCH_BOT_USERNAME", ""),
            "discord_configured": bool(getattr(settings, "DISCORD_BOT_TOKEN", "")),
        }
        cache.set(key, context, _cache_ttl())
    else:
        leaderboard_cache_total.labels("deathboard_page", "hit").inc()

    response = render(request, "simpwatch/deathboard.html", context)
    duration = max(time.monotonic() - started, 0.0)
    endpoint = "deathboard_page"
    http_request_duration_seconds.labels(request.method, endpoint).observe(duration)
    http_requests_total.labels(request.method, endpoint, "2xx").inc()
    return response


def deathboard_api(request):
    started = time.monotonic()
    selected_game_id = request.GET.get("game", "").strip()
    key = _cache_key("deathboard_api", f"all:{selected_game_id or 'all'}")
    payload = cache.get(key)
    if payload is None:
        leaderboard_cache_total.labels("deathboard_api", "miss").inc()
        rows = _deathboard_rows_alltime(selected_game_id)
        recent_events = _recent_death_events_alltime(selected_game_id)
        payload = {
            "window": "all",
            "selected_game_id": selected_game_id,
            "games": _death_game_options(),
            "deathboard": [
                {
                    "person_id": row["person"].id,
                    "name": row["person"].name,
                    "death_count": row["death_count"],
                }
                for row in rows
            ],
            "recent_deaths": [
                {
                    "id": event.id,
                    "actor": event.actor_identity.username,
                    "target": event.target_person.name,
                    "game_id": event.game_id,
                    "game_name": event.game_name,
                    "reason": event.reason,
                    "source": event.source,
                    "created_at": event.created_at.isoformat(),
                }
                for event in recent_events
            ],
        }
        cache.set(key, payload, _cache_ttl())
    else:
        leaderboard_cache_total.labels("deathboard_api", "hit").inc()

    response = JsonResponse(payload)
    duration = max(time.monotonic() - started, 0.0)
    endpoint = "deathboard_api"
    http_request_duration_seconds.labels(request.method, endpoint).observe(duration)
    http_requests_total.labels(request.method, endpoint, "2xx").inc()
    return response


def crimeboard_page(request):
    started = time.monotonic()
    selected_game_id = request.GET.get("game", "").strip()
    key = _cache_key("crimeboard_page", f"all:{selected_game_id or 'all'}")
    context = cache.get(key)
    if context is None:
        leaderboard_cache_total.labels("crimeboard_page", "miss").inc()
        channels = _watched_channels_enriched()
        games = _criminal_game_options()
        selected_game_name = ""
        if selected_game_id:
            for game in games:
                if game.get("game_id") == selected_game_id:
                    selected_game_name = game.get("game_name", "")
                    break
        context = {
            "rows": _crimeboard_rows_alltime(selected_game_id),
            "recent_events": _recent_crime_events_alltime(selected_game_id),
            "games": games,
            "selected_game_id": selected_game_id,
            "selected_game_name": selected_game_name,
            "most_criminal_games": _games_by_crime_count(),
            "watched_channels": channels,
            "twitch_configured": bool(
                getattr(settings, "TWITCH_BOT_USERNAME", "") or channels
            ),
            "twitch_bot_username": getattr(settings, "TWITCH_BOT_USERNAME", ""),
            "discord_configured": bool(getattr(settings, "DISCORD_BOT_TOKEN", "")),
        }
        cache.set(key, context, _cache_ttl())
    else:
        leaderboard_cache_total.labels("crimeboard_page", "hit").inc()

    response = render(request, "simpwatch/crimeboard.html", context)
    duration = max(time.monotonic() - started, 0.0)
    endpoint = "crimeboard_page"
    http_request_duration_seconds.labels(request.method, endpoint).observe(duration)
    http_requests_total.labels(request.method, endpoint, "2xx").inc()
    return response


def crimeboard_api(request):
    started = time.monotonic()
    selected_game_id = request.GET.get("game", "").strip()
    key = _cache_key("crimeboard_api", f"all:{selected_game_id or 'all'}")
    payload = cache.get(key)
    if payload is None:
        leaderboard_cache_total.labels("crimeboard_api", "miss").inc()
        rows = _crimeboard_rows_alltime(selected_game_id)
        recent_events = _recent_crime_events_alltime(selected_game_id)
        payload = {
            "window": "all",
            "selected_game_id": selected_game_id,
            "games": _criminal_game_options(),
            "crimeboard": [
                {
                    "person_id": row["person"].id,
                    "name": row["person"].name,
                    "crime_count": row["crime_count"],
                }
                for row in rows
            ],
            "recent_crimes": [
                {
                    "id": event.id,
                    "actor": event.actor_identity.username,
                    "target": event.target_person.name,
                    "game_id": event.game_id,
                    "game_name": event.game_name,
                    "reason": event.reason,
                    "source": event.source,
                    "created_at": event.created_at.isoformat(),
                }
                for event in recent_events
            ],
        }
        cache.set(key, payload, _cache_ttl())
    else:
        leaderboard_cache_total.labels("crimeboard_api", "hit").inc()

    response = JsonResponse(payload)
    duration = max(time.monotonic() - started, 0.0)
    endpoint = "crimeboard_api"
    http_request_duration_seconds.labels(request.method, endpoint).observe(duration)
    http_requests_total.labels(request.method, endpoint, "2xx").inc()
    return response
