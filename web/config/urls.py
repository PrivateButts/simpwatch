from django.contrib import admin
from django.urls import path

from simpwatch.views import (
    healthcheck,
    leaderboard_api,
    leaderboard_page,
    metrics_view,
)


urlpatterns = [
    path("admin/", admin.site.urls),
    path("healthz", healthcheck, name="healthcheck"),
    path("metrics", metrics_view, name="metrics"),
    path("", leaderboard_page, name="leaderboard_page"),
    path("api/leaderboard", leaderboard_api, name="leaderboard_api"),
]
