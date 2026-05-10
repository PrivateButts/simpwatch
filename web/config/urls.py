from django.contrib import admin
from django.urls import path

from simpwatch.views import (
    healthcheck,
    leaderboard_api,
    leaderboard_page,
    metrics_view,
    twitch_onboard_callback,
    twitch_onboard_revoke,
    twitch_onboard_start,
)


urlpatterns = [
    path("admin/", admin.site.urls),
    path("healthz", healthcheck, name="healthcheck"),
    path("metrics", metrics_view, name="metrics"),
    path("oauth/twitch/start", twitch_onboard_start, name="twitch_onboard_start"),
    path("oauth/twitch/callback", twitch_onboard_callback, name="twitch_onboard_callback"),
    path("oauth/twitch/revoke", twitch_onboard_revoke, name="twitch_onboard_revoke"),
    path("", leaderboard_page, name="leaderboard_page"),
    path("api/leaderboard", leaderboard_api, name="leaderboard_api"),
]
