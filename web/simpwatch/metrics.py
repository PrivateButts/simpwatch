from __future__ import annotations

from prometheus_client import CONTENT_TYPE_LATEST, Counter, Gauge, Histogram, generate_latest


# Keep labels low-cardinality so Prometheus stays performant.
http_requests_total = Counter(
    "simpwatch_http_requests_total",
    "Total HTTP requests handled by SimpWatch",
    ["method", "endpoint", "status_class"],
)

http_request_duration_seconds = Histogram(
    "simpwatch_http_request_duration_seconds",
    "HTTP request duration in seconds",
    ["method", "endpoint"],
)

leaderboard_cache_total = Counter(
    "simpwatch_leaderboard_cache_total",
    "Leaderboard cache operations",
    ["surface", "result"],
)


# Twitch worker metrics.
twitch_messages_total = Counter(
    "simpwatch_twitch_messages_total",
    "Total Twitch messages observed",
)

twitch_commands_total = Counter(
    "simpwatch_twitch_commands_total",
    "Total Twitch commands handled",
    ["command"],
)

twitch_events_registered_total = Counter(
    "simpwatch_twitch_events_registered_total",
    "Total scoring events registered by Twitch bot",
    ["event_type"],
)

twitch_cooldowns_total = Counter(
    "simpwatch_twitch_cooldowns_total",
    "Total Twitch command attempts blocked by cooldown",
    ["command"],
)

twitch_errors_total = Counter(
    "simpwatch_twitch_errors_total",
    "Total Twitch bot processing/runtime errors",
    ["kind"],
)

twitch_watchdog_reconnect_attempts = Gauge(
    "simpwatch_twitch_watchdog_reconnect_attempts",
    "Current watchdog reconnect attempts count",
)

twitch_irc_idle_seconds = Gauge(
    "simpwatch_twitch_irc_idle_seconds",
    "Current seconds since last IRC activity",
)


def prometheus_payload() -> tuple[bytes, str]:
    """Return latest Prometheus payload bytes and content type."""
    return generate_latest(), CONTENT_TYPE_LATEST
