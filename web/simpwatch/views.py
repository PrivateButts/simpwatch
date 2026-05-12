import json
import logging
import hashlib
import secrets
import time
import urllib.error
import urllib.parse
import urllib.request
import base64
from datetime import timedelta
from datetime import datetime
from datetime import timezone as dt_timezone

from django.conf import settings
from django.core.cache import cache
from django.db.models import Count
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
from .scoring import current_leaderboard_cache_version

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
    started = time.monotonic()
    response = JsonResponse({"status": "ok"})
    duration = max(time.monotonic() - started, 0.0)
    endpoint = "healthcheck"
    http_request_duration_seconds.labels(request.method, endpoint).observe(duration)
    http_requests_total.labels(request.method, endpoint, "2xx").inc()
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
                "detail": "Broadcaster login is not listed in TWITCH_CHANNELS.",
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
    else:
        leaderboard_cache_total.labels("api", "hit").inc()
    response = JsonResponse(payload)
    duration = max(time.monotonic() - started, 0.0)
    endpoint = "leaderboard_api"
    http_request_duration_seconds.labels(request.method, endpoint).observe(duration)
    http_requests_total.labels(request.method, endpoint, "2xx").inc()
    return response
