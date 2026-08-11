from django.contrib import admin
from django.urls import path

from simpwatch.views import (
    bot_health,
    crimeboard_api,
    crimeboard_page,
    deathboard_api,
    deathboard_page,
    healthcheck,
    leaderboard_api,
    leaderboard_page,
    metrics_view,
    twitch_bot_token_callback,
    twitch_bot_token_start,
    twitch_onboard_callback,
    twitch_onboard_revoke,
    twitch_onboard_start,
)


urlpatterns = [
    path("admin/", admin.site.urls),
    path("healthz", healthcheck, name="healthcheck"),
    path("api/bot-health", bot_health, name="bot_health"),
    path("metrics", metrics_view, name="metrics"),
    path("oauth/twitch/start", twitch_onboard_start, name="twitch_onboard_start"),
    path("oauth/twitch/callback", twitch_onboard_callback, name="twitch_onboard_callback"),
    path("oauth/twitch/revoke", twitch_onboard_revoke, name="twitch_onboard_revoke"),
    path("oauth/twitch/bot/start", twitch_bot_token_start, name="twitch_bot_token_start"),
    path("oauth/twitch/bot/callback", twitch_bot_token_callback, name="twitch_bot_token_callback"),
    path("", leaderboard_page, name="leaderboard_page"),
    path("api/leaderboard", leaderboard_api, name="leaderboard_api"),
    path("crimeboard", crimeboard_page, name="crimeboard_page"),
    path("api/crimeboard", crimeboard_api, name="crimeboard_api"),
    path("deathboard", deathboard_page, name="deathboard_page"),
    path("api/deathboard", deathboard_api, name="deathboard_api"),
]
