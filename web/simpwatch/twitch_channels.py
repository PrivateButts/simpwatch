from __future__ import annotations


def get_monitored_channels() -> list[str]:
    try:
        from .models import TwitchChannel

        rows = TwitchChannel.objects.filter(is_monitored=True).values_list(
            "login", flat=True
        )
        return list(rows)
    except Exception:
        return []


def get_reply_channels() -> set[str]:
    try:
        from .models import TwitchChannel

        rows = TwitchChannel.objects.filter(
            is_monitored=True, send_replies=True
        ).values_list("login", flat=True)
        return set(rows)
    except Exception:
        return set()


def is_monitored(login: str) -> bool:
    normalized = login.strip().lower()
    return normalized in set(get_monitored_channels())
