from __future__ import annotations

import os

from django.conf import settings


def get_monitored_channels() -> list[str]:
    try:
        from .models import TwitchChannel

        rows = TwitchChannel.objects.filter(is_monitored=True).values_list(
            "login", flat=True
        )
        channels = list(rows)
        if channels:
            return channels
    except Exception:
        pass

    raw = getattr(settings, "TWITCH_CHANNELS", "")
    if not raw:
        raw = os.getenv("TWITCH_CHANNELS", "")
    if isinstance(raw, str):
        raw = [c.strip().lower() for c in raw.split(",") if c.strip()]
    return raw


def get_reply_channels() -> set[str]:
    try:
        from .models import TwitchChannel

        rows = TwitchChannel.objects.filter(
            is_monitored=True, send_replies=True
        ).values_list("login", flat=True)
        channels = set(rows)
        if channels:
            return channels
    except Exception:
        pass

    raw = os.getenv("TWITCH_REPLY_CHANNELS", "")
    if raw:
        return {c.strip().lower() for c in raw.split(",") if c.strip()}
    return set()


def is_monitored(login: str) -> bool:
    normalized = login.strip().lower()
    return normalized in set(get_monitored_channels())
