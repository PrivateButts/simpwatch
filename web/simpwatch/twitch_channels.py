from __future__ import annotations

import logging

logger = logging.getLogger("twitch_channels")


def get_monitored_channels() -> list[str]:
    try:
        from .models import TwitchChannel

        rows = TwitchChannel.objects.filter(is_monitored=True).values_list(
            "login", flat=True
        )
        channels = list(rows)
        if not channels:
            logger.warning(
                "No monitored Twitch channels found in database. "
                "The bot will not be able to join any channels."
            )
        return channels
    except Exception:
        logger.exception("Failed to query TwitchChannel from database")
        return []


def get_reply_channels() -> set[str]:
    try:
        from .models import TwitchChannel

        rows = TwitchChannel.objects.filter(
            is_monitored=True, send_replies=True
        ).values_list("login", flat=True)
        return set(rows)
    except Exception:
        logger.exception("Failed to query reply channels from database")
        return set()


def is_monitored(login: str) -> bool:
    normalized = login.strip().lower()
    return normalized in set(get_monitored_channels())
