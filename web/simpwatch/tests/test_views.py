from datetime import timedelta

from django.core.cache import cache
from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.utils import timezone
from unittest.mock import patch

from simpwatch.models import (
    Identity,
    Person,
    ScoreAdjustment,
    SimpEvent,
    TwitchBotGrant,
    TwitchBroadcasterGrant,
)


class LeaderboardViewTests(TestCase):
    def setUp(self):
        self.target_one = Person.objects.create(name="Target One")
        self.target_two = Person.objects.create(name="Target Two")
        self.actor_person = Person.objects.create(name="Caller")

        self.actor_identity = Identity.objects.create(
            person=self.actor_person,
            platform=Identity.Platform.TWITCH,
            platform_user_id="actor-1",
            username="caller",
            display_name="caller",
        )

    def test_leaderboard_api_returns_narc_leaderboard_and_reason(self):
        SimpEvent.objects.create(
            actor_identity=self.actor_identity,
            target_person=self.target_one,
            platform=Identity.Platform.TWITCH,
            source="streamer",
            points=1,
            reason="for science",
            message_id="1",
        )
        SimpEvent.objects.create(
            actor_identity=self.actor_identity,
            target_person=self.target_two,
            platform=Identity.Platform.TWITCH,
            source="streamer",
            points=1,
            reason="",
            message_id="2",
        )

        response = self.client.get("/api/leaderboard", {"window": "all"})
        self.assertEqual(response.status_code, 200)
        payload = response.json()

        self.assertIn("narc_leaderboard", payload)
        self.assertGreaterEqual(payload["narc_leaderboard"][0]["callout_count"], 2)

        recent = payload["recent_events"]
        self.assertTrue(any(event["reason"] == "for science" for event in recent))

    def test_window_filters_out_old_events(self):
        old_event = SimpEvent.objects.create(
            actor_identity=self.actor_identity,
            target_person=self.target_one,
            platform=Identity.Platform.TWITCH,
            source="streamer",
            points=1,
            reason="old",
            message_id="old",
        )
        SimpEvent.objects.filter(id=old_event.id).update(
            created_at=timezone.now() - timedelta(days=10)
        )

        response = self.client.get("/api/leaderboard", {"window": "24h"})
        self.assertEqual(response.status_code, 200)
        payload = response.json()

        self.assertEqual(payload["leaderboard"], [])
        self.assertEqual(payload["narc_leaderboard"], [])


class DeathboardViewTests(TestCase):
    def setUp(self):
        self.target_one = Person.objects.create(name="StreamerOne")
        self.target_two = Person.objects.create(name="StreamerTwo")
        self.actor_person = Person.objects.create(name="Caller")
        self.actor_identity = Identity.objects.create(
            person=self.actor_person,
            platform=Identity.Platform.TWITCH,
            platform_user_id="actor-1",
            username="caller",
            display_name="caller",
        )

    def test_deathboard_api_groups_by_streamer(self):
        SimpEvent.objects.create(
            actor_identity=self.actor_identity,
            target_person=self.target_one,
            platform=Identity.Platform.TWITCH,
            event_type=SimpEvent.EventType.DEATH,
            game_id="100",
            game_name="Elden Ring",
            source="streamer",
            points=1,
        )
        SimpEvent.objects.create(
            actor_identity=self.actor_identity,
            target_person=self.target_one,
            platform=Identity.Platform.TWITCH,
            event_type=SimpEvent.EventType.DEATH,
            game_id="100",
            game_name="Elden Ring",
            source="streamer",
            points=1,
        )
        SimpEvent.objects.create(
            actor_identity=self.actor_identity,
            target_person=self.target_two,
            platform=Identity.Platform.TWITCH,
            event_type=SimpEvent.EventType.DEATH,
            game_id="200",
            game_name="Hades",
            source="streamer2",
            points=1,
        )

        response = self.client.get("/api/deathboard")
        self.assertEqual(response.status_code, 200)
        payload = response.json()

        self.assertEqual(payload["window"], "all")
        self.assertEqual(payload["deathboard"][0]["name"], "StreamerOne")
        self.assertEqual(payload["deathboard"][0]["death_count"], 2)
        self.assertEqual(payload["deathboard"][1]["name"], "StreamerTwo")
        self.assertEqual(payload["deathboard"][1]["death_count"], 1)
        self.assertEqual(payload["games"][0]["game_id"], "100")

    def test_deathboard_api_filters_by_game_id(self):
        SimpEvent.objects.create(
            actor_identity=self.actor_identity,
            target_person=self.target_one,
            platform=Identity.Platform.TWITCH,
            event_type=SimpEvent.EventType.DEATH,
            game_id="100",
            game_name="Elden Ring",
            source="streamer",
            points=1,
        )
        SimpEvent.objects.create(
            actor_identity=self.actor_identity,
            target_person=self.target_two,
            platform=Identity.Platform.TWITCH,
            event_type=SimpEvent.EventType.DEATH,
            game_id="200",
            game_name="Hades",
            source="streamer2",
            points=1,
        )

        response = self.client.get("/api/deathboard", {"game": "100"})
        self.assertEqual(response.status_code, 200)
        payload = response.json()

        self.assertEqual(payload["selected_game_id"], "100")
        self.assertEqual(len(payload["deathboard"]), 1)
        self.assertEqual(payload["deathboard"][0]["name"], "StreamerOne")

    def test_deathboard_api_excludes_non_death_events(self):
        SimpEvent.objects.create(
            actor_identity=self.actor_identity,
            target_person=self.target_one,
            platform=Identity.Platform.TWITCH,
            event_type=SimpEvent.EventType.SIMP,
            source="streamer",
            points=1,
        )

        response = self.client.get("/api/deathboard")
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["deathboard"], [])

    def test_deathboard_api_includes_death_adjustments(self):
        ScoreAdjustment.objects.create(
            target_person=self.target_one,
            adjustment_type=ScoreAdjustment.AdjustmentType.DEATH,
            points_delta=2,
            game_id="100",
            game_name="Elden Ring",
            reason="manual correction",
        )
        response = self.client.get("/api/deathboard")
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["deathboard"][0]["name"], "StreamerOne")
        self.assertEqual(payload["deathboard"][0]["death_count"], 2)
        self.assertEqual(payload["games"][0]["game_id"], "100")

    def test_deathboard_api_sums_multi_point_death_events(self):
        SimpEvent.objects.create(
            actor_identity=self.actor_identity,
            target_person=self.target_one,
            platform=Identity.Platform.TWITCH,
            event_type=SimpEvent.EventType.DEATH,
            game_id="100",
            game_name="Elden Ring",
            source="streamer",
            points=10,
        )
        response = self.client.get("/api/deathboard")
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["deathboard"][0]["name"], "StreamerOne")
        self.assertEqual(payload["deathboard"][0]["death_count"], 10)

    @override_settings(TWITCH_BOT_USERNAME="testbot")
    def test_deathboard_page_renders(self):
        cache.clear()
        SimpEvent.objects.create(
            actor_identity=self.actor_identity,
            target_person=self.target_one,
            platform=Identity.Platform.TWITCH,
            event_type=SimpEvent.EventType.DEATH,
            game_id="300",
            game_name="Balatro",
            source="streamer",
            points=1,
        )

        response = self.client.get("/deathboard")
        self.assertEqual(response.status_code, 200)
        content = response.content.decode()
        self.assertIn("Death Board", content)
        self.assertIn("Balatro", content)
        self.assertIn("Game Filter", content)
        self.assertIn("!death", content)
        self.assertIn("!died", content)
        self.assertIn("!deathcheck", content)
        self.assertNotIn("!simp", content)
        self.assertNotIn("!bamder", content)

    @override_settings(TWITCH_CHANNELS=["streamer1", "streamer2"])
    def test_deathboard_shows_watched_channels(self):
        cache.clear()
        response = self.client.get("/deathboard")
        self.assertEqual(response.status_code, 200)
        content = response.content.decode()
        self.assertIn("Watched Channels", content)
        self.assertIn("https://twitch.tv/streamer1", content)
        self.assertIn("https://twitch.tv/streamer2", content)

    def test_bamder_events_are_reported_separately(self):
        SimpEvent.objects.create(
            actor_identity=self.actor_identity,
            target_person=Person.objects.create(name="pamder"),
            platform=Identity.Platform.TWITCH,
            event_type=SimpEvent.EventType.BAMDER,
            source="streamer",
            points=1,
            reason="misbehaving",
            message_id="b1",
        )

        response = self.client.get("/api/leaderboard", {"window": "all"})
        self.assertEqual(response.status_code, 200)
        payload = response.json()

        self.assertEqual(payload["bamder_total"], 1)
        self.assertEqual(len(payload["bamder_recent_events"]), 1)
        self.assertEqual(payload["bamder_recent_events"][0]["reason"], "misbehaving")

    def test_bamder_adjustments_are_counted(self):
        ScoreAdjustment.objects.create(
            target_person=Person.objects.create(name="pamder"),
            adjustment_type=ScoreAdjustment.AdjustmentType.BAMDER,
            points_delta=2,
            reason="manual correction",
        )
        response = self.client.get("/api/leaderboard", {"window": "all"})
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["bamder_total"], 2)

    def test_banthem_events_are_reported_separately(self):
        SimpEvent.objects.create(
            actor_identity=self.actor_identity,
            target_person=Person.objects.create(name="prvbutts"),
            platform=Identity.Platform.TWITCH,
            event_type=SimpEvent.EventType.BANTHEM,
            source="streamer",
            points=1,
            reason="acted up",
            message_id="bt1",
        )

        response = self.client.get("/api/leaderboard", {"window": "all"})
        self.assertEqual(response.status_code, 200)
        payload = response.json()

        self.assertEqual(len(payload["banthem_leaderboard"]), 1)
        self.assertEqual(payload["banthem_leaderboard"][0]["name"], "prvbutts")
        self.assertEqual(payload["banthem_leaderboard"][0]["points"], 1)
        self.assertEqual(len(payload["banthem_recent_events"]), 1)
        self.assertEqual(payload["banthem_recent_events"][0]["reason"], "acted up")

    def test_banthem_adjustments_are_counted(self):
        ScoreAdjustment.objects.create(
            target_person=Person.objects.create(name="prvbutts"),
            adjustment_type=ScoreAdjustment.AdjustmentType.BANTHEM,
            points_delta=2,
            reason="manual correction",
        )
        response = self.client.get("/api/leaderboard", {"window": "all"})
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["banthem_leaderboard"][0]["points"], 2)

    def test_banthem_events_do_not_affect_simp_or_narc_leaderboards(self):
        SimpEvent.objects.create(
            actor_identity=self.actor_identity,
            target_person=Person.objects.create(name="prvbutts"),
            platform=Identity.Platform.TWITCH,
            event_type=SimpEvent.EventType.BANTHEM,
            source="streamer",
            points=1,
            reason="bad behavior",
            message_id="bt2",
        )

        response = self.client.get("/api/leaderboard", {"window": "all"})
        self.assertEqual(response.status_code, 200)
        payload = response.json()

        self.assertEqual(payload["leaderboard"], [])
        self.assertEqual(payload["narc_leaderboard"], [])

    def test_banthem_api_still_works_when_adjustment_type_column_is_missing(self):
        SimpEvent.objects.create(
            actor_identity=self.actor_identity,
            target_person=Person.objects.create(name="prvbutts"),
            platform=Identity.Platform.TWITCH,
            event_type=SimpEvent.EventType.BANTHEM,
            source="streamer",
            points=1,
            reason="acted up",
            message_id="bt3",
        )

        with patch("simpwatch.scoring.score_adjustment_has_columns", return_value=False):
            response = self.client.get("/api/leaderboard", {"window": "all"})

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["banthem_leaderboard"][0]["name"], "prvbutts")
        self.assertEqual(payload["banthem_leaderboard"][0]["points"], 1)

    def test_bamder_events_do_not_affect_simp_or_narc_leaderboards(self):
        SimpEvent.objects.create(
            actor_identity=self.actor_identity,
            target_person=Person.objects.create(name="pamder"),
            platform=Identity.Platform.TWITCH,
            event_type=SimpEvent.EventType.BAMDER,
            source="streamer",
            points=1,
            reason="bad behavior",
            message_id="b2",
        )

        response = self.client.get("/api/leaderboard", {"window": "all"})
        self.assertEqual(response.status_code, 200)
        payload = response.json()

        self.assertEqual(payload["leaderboard"], [])
        self.assertEqual(payload["narc_leaderboard"], [])

    def test_non_simp_adjustments_do_not_affect_simp_or_narc_leaderboards(self):
        ScoreAdjustment.objects.create(
            target_person=Person.objects.create(name="pamder"),
            adjustment_type=ScoreAdjustment.AdjustmentType.BAMDER,
            points_delta=3,
            reason="bamder fix",
        )
        response = self.client.get("/api/leaderboard", {"window": "all"})
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["leaderboard"], [])
        self.assertEqual(payload["narc_leaderboard"], [])


class HelpSectionViewTests(TestCase):
    @override_settings(
        TWITCH_CHANNELS=["streamer1"],
        TWITCH_BOT_USERNAME="simpbot",
        DISCORD_BOT_TOKEN="discord-token",
    )
    def test_help_section_rendered_on_page(self):
        cache.clear()
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        content = response.content.decode()
        self.assertIn("Help", content)
        self.assertIn("Twitch Commands", content)
        self.assertIn("Discord Commands", content)
        self.assertIn("Watched Channels", content)

    @override_settings(
        TWITCH_CHANNELS=["streamer1"],
        TWITCH_BOT_USERNAME="simpbot",
        DISCORD_BOT_TOKEN="discord-token",
    )
    def test_commands_listed_on_page(self):
        cache.clear()
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        content = response.content.decode()
        self.assertIn("!simp", content)
        self.assertIn("!bamder", content)
        self.assertIn("!ban @username", content)
        self.assertIn("ban @username", content)
        self.assertIn("ban @username reason", content)
        self.assertIn("/simp", content)
        self.assertIn("simp @username", content)
        self.assertIn("simpcheck", content)
        self.assertIn("standings", content)
        self.assertNotIn("!deathcheck", content)
        self.assertNotIn("<td class=\"cmd\">!death</td>", content)
        self.assertNotIn("<td class=\"cmd\">!died</td>", content)

    @override_settings(
        TWITCH_CHANNELS=["streamer1"],
        TWITCH_BOT_USERNAME="simpbot",
    )
    def test_banthem_panels_rendered_on_page(self):
        cache.clear()
        SimpEvent.objects.create(
            actor_identity=Identity.objects.create(
                person=Person.objects.create(name="panel-caller"),
                platform=Identity.Platform.TWITCH,
                platform_user_id="panel-actor-1",
                username="panelcaller",
                display_name="PanelCaller",
            ),
            target_person=Person.objects.create(name="prvbutts"),
            platform=Identity.Platform.TWITCH,
            event_type=SimpEvent.EventType.BANTHEM,
            source="streamer1",
            points=1,
            reason="acted up",
            message_id="page-bt1",
        )

        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        content = response.content.decode()
        self.assertIn("Most likely to be Banned", content)
        self.assertIn("Recent Ban Requests", content)
        self.assertIn("prvbutts", content)
        self.assertIn("wants <strong>prvbutts</strong> to be banned for acted up.", content)

    @override_settings(TWITCH_CHANNELS=["streamer1", "streamer2"])
    def test_watched_channels_shown_when_configured(self):
        cache.clear()
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        content = response.content.decode()
        self.assertIn("https://twitch.tv/streamer1", content)
        self.assertIn("https://twitch.tv/streamer2", content)

    @override_settings(TWITCH_CHANNELS=["streamer1", "streamer2"])
    def test_no_grant_badge_shown_when_channel_lacks_grant(self):
        cache.clear()
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        content = response.content.decode()
        self.assertIn("Lurk Only", content)

    @override_settings(TWITCH_CHANNELS=["streamer1", "streamer2"])
    def test_no_grant_badge_hidden_when_channel_has_active_grant(self):
        cache.clear()
        TwitchBroadcasterGrant.objects.create(
            username="streamer1",
            broadcaster_user_id="111",
            access_token="a",
            refresh_token="r",
            is_active=True,
        )
        TwitchBroadcasterGrant.objects.create(
            username="streamer2",
            broadcaster_user_id="222",
            access_token="a",
            refresh_token="r",
            is_active=True,
        )
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        content = response.content.decode()
        self.assertNotIn("Lurk Only", content)

    @override_settings(TWITCH_CHANNELS=["streamer1", "streamer2"])
    def test_no_grant_badge_shown_when_grant_is_inactive(self):
        cache.clear()
        TwitchBroadcasterGrant.objects.create(
            username="streamer1",
            broadcaster_user_id="111",
            access_token="a",
            refresh_token="r",
            is_active=False,
        )
        TwitchBroadcasterGrant.objects.create(
            username="streamer2",
            broadcaster_user_id="222",
            access_token="a",
            refresh_token="r",
            is_active=True,
        )
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        content = response.content.decode()
        # streamer1 has an inactive grant so badge should still appear
        self.assertIn("Lurk Only", content)

    @override_settings(TWITCH_CHANNELS=[], TWITCH_BOT_USERNAME="", DISCORD_BOT_TOKEN="")
    def test_no_channels_shows_fallback_message(self):
        cache.clear()
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        content = response.content.decode()
        self.assertIn("No integrations configured.", content)


class HealthcheckViewTests(TestCase):
    def test_healthcheck_returns_ok_payload(self):
        response = self.client.get("/healthz")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"status": "ok"})


class MetricsViewTests(TestCase):
    def test_metrics_endpoint_returns_prometheus_content(self):
        self.client.get("/healthz")
        self.client.get("/api/leaderboard", {"window": "all"})

        response = self.client.get("/metrics")

        self.assertEqual(response.status_code, 200)
        self.assertIn("text/plain", response["Content-Type"])
        body = response.content.decode()
        self.assertIn("simpwatch_http_requests_total", body)
        self.assertIn("simpwatch_http_request_duration_seconds", body)
        self.assertIn("simpwatch_leaderboard_cache_total", body)


class TwitchOnboardingViewTests(TestCase):
    def setUp(self):
        cache.clear()

    @override_settings(
        TWITCH_CHANNELS=["prvbutts"],
        TWITCH_ONBOARD_STATE_TTL_SECONDS=600,
    )
    @patch("simpwatch.views._validate_twitch_access_token")
    @patch("simpwatch.views._exchange_twitch_code_for_tokens")
    def test_callback_stores_tokens_when_login_is_watched_channel(
        self,
        mock_exchange,
        mock_validate,
    ):
        state = "state-ok"
        cache.set("twitch:onboard:state:state-ok", "1", 600)
        mock_exchange.return_value = {
            "access_token": "access-1",
            "refresh_token": "refresh-1",
            "expires_in": 3600,
        }
        mock_validate.return_value = {
            "login": "prvbutts",
            "user_id": "42490016",
            "scopes": ["channel:bot"],
        }

        response = self.client.get(
            "/oauth/twitch/callback",
            {"state": state, "code": "code-1"},
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["ok"], True)
        self.assertEqual(TwitchBroadcasterGrant.objects.count(), 1)
        grant = TwitchBroadcasterGrant.objects.get(username="prvbutts")
        self.assertEqual(grant.broadcaster_user_id, "42490016")

    @override_settings(
        TWITCH_CHANNELS=["prvbutts"],
        TWITCH_ONBOARD_STATE_TTL_SECONDS=600,
    )
    @patch("simpwatch.views._validate_twitch_access_token")
    @patch("simpwatch.views._exchange_twitch_code_for_tokens")
    def test_callback_rejects_tokens_when_login_not_watched_channel(
        self,
        mock_exchange,
        mock_validate,
    ):
        state = "state-reject"
        cache.set("twitch:onboard:state:state-reject", "1", 600)
        mock_exchange.return_value = {
            "access_token": "access-2",
            "refresh_token": "refresh-2",
            "expires_in": 3600,
        }
        mock_validate.return_value = {
            "login": "someoneelse",
            "user_id": "777",
            "scopes": ["channel:bot"],
        }

        response = self.client.get(
            "/oauth/twitch/callback",
            {"state": state, "code": "code-2"},
        )

        self.assertEqual(response.status_code, 403)
        payload = response.json()
        self.assertEqual(payload["error"], "channel_not_configured")
        self.assertEqual(TwitchBroadcasterGrant.objects.count(), 0)

    @override_settings(
        TWITCH_CHANNELS=["prvbutts"],
        TWITCH_ONBOARD_STATE_TTL_SECONDS=600,
    )
    @patch("simpwatch.views._validate_twitch_access_token")
    @patch("simpwatch.views._exchange_twitch_code_for_tokens")
    def test_callback_matches_watched_channel_case_insensitively(
        self,
        mock_exchange,
        mock_validate,
    ):
        state = "state-case"
        cache.set("twitch:onboard:state:state-case", "1", 600)
        mock_exchange.return_value = {
            "access_token": "access-3",
            "refresh_token": "refresh-3",
            "expires_in": 3600,
        }
        mock_validate.return_value = {
            "login": "PrVBuTtS",
            "user_id": "42490016",
            "scopes": ["channel:bot"],
        }

        response = self.client.get(
            "/oauth/twitch/callback",
            {"state": state, "code": "code-3"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(TwitchBroadcasterGrant.objects.count(), 1)
        grant = TwitchBroadcasterGrant.objects.get()
        self.assertEqual(grant.username, "prvbutts")

    def test_revoke_requires_staff_authentication(self):
        grant = TwitchBroadcasterGrant.objects.create(
            username="prvbutts",
            broadcaster_user_id="42490016",
            access_token="a",
            refresh_token="r",
            scopes="channel:bot",
            is_active=True,
        )
        response = self.client.post("/oauth/twitch/revoke", {"username": "prvbutts"})
        self.assertEqual(response.status_code, 403)
        grant.refresh_from_db()
        self.assertTrue(grant.is_active)

    def test_revoke_deactivates_grant_for_staff_user(self):
        grant = TwitchBroadcasterGrant.objects.create(
            username="prvbutts",
            broadcaster_user_id="42490016",
            access_token="a",
            refresh_token="r",
            scopes="channel:bot",
            is_active=True,
        )
        staff = get_user_model().objects.create_user(
            username="admin",
            email="admin@example.com",
            password="pw",
            is_staff=True,
        )
        self.client.force_login(staff)
        response = self.client.post("/oauth/twitch/revoke", {"username": "prvbutts"})
        self.assertEqual(response.status_code, 200)
        grant.refresh_from_db()
        self.assertFalse(grant.is_active)


class BotTokenSetupTests(TestCase):
    """Test bot OAuth token setup flow (/oauth/twitch/bot/*)"""

    def test_bot_token_start_requires_staff(self):
        """Non-staff users cannot start bot token setup"""
        response = self.client.get("/oauth/twitch/bot/start")
        self.assertEqual(response.status_code, 403)
        payload = response.json()
        self.assertFalse(payload.get("ok"))
        self.assertEqual(payload.get("error"), "forbidden")

    def test_bot_token_start_returns_oauth_url_for_staff(self):
        """Staff users get OAuth URL to redirect to"""
        staff = get_user_model().objects.create_user(
            username="admin",
            email="admin@example.com",
            password="pw",
            is_staff=True,
        )
        self.client.force_login(staff)

        with override_settings(
            TWITCH_CLIENT_ID="test-client",
            TWITCH_CLIENT_SECRET="test-secret",
            TWITCH_TOKEN_REDIRECT_URI="http://localhost/oauth/twitch/callback",
        ):
            response = self.client.get("/oauth/twitch/bot/start")
            self.assertEqual(response.status_code, 200)
            payload = response.json()
            self.assertTrue(payload.get("ok"))
            self.assertIn("oauth_url", payload)
            self.assertIn("client_id=", payload["oauth_url"])
            self.assertIn("scope=", payload["oauth_url"])
            self.assertIn("state=", payload["oauth_url"])
            self.assertIn("scope=user%3Abot%20user%3Aread%3Achat%20user%3Awrite%3Achat", payload["oauth_url"])
            self.assertIn(
                "redirect_uri=http%3A//localhost/oauth/twitch/bot/callback",
                payload["oauth_url"],
            )

    @override_settings(
        TWITCH_CLIENT_ID="test-client",
        TWITCH_CLIENT_SECRET="test-secret",
        TWITCH_TOKEN_REDIRECT_URI="http://localhost/oauth/twitch/callback",
        TWITCH_ONBOARD_STATE_TTL_SECONDS=600,
    )
    @patch("simpwatch.views._exchange_twitch_code_for_tokens")
    @patch("simpwatch.views._validate_twitch_access_token")
    def test_bot_token_callback_creates_bot_grant(self, mock_validate, mock_exchange):
        """OAuth callback creates/updates TwitchBotGrant with encrypted tokens"""
        # Mock Twitch API responses
        mock_exchange.return_value = {
            "access_token": "bot-access-token-xyz",
            "refresh_token": "bot-refresh-token-abc",
            "expires_in": 3600,
        }
        mock_validate.return_value = {
            "login": "simpbot",
            "user_id": "9999",
            "scopes": ["user:bot", "user:read:chat", "user:write:chat"],
        }

        # Create a state in cache (normally done by /start endpoint)
        state = "test-state-12345"
        cache.set(f"twitch:onboard:state:bot:{state}", True, 600)

        # Exchange code for tokens
        response = self.client.get(
            "/oauth/twitch/bot/callback",
            {"code": "auth-code-123", "state": state},
        )
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload.get("ok"))
        self.assertEqual(payload.get("bot_username"), "simpbot")
        mock_exchange.assert_called_once_with(
            "auth-code-123",
            redirect_uri="http://localhost/oauth/twitch/bot/callback",
        )

        # Verify TwitchBotGrant was created
        grant = TwitchBotGrant.objects.get(bot_username="simpbot")
        self.assertEqual(grant.bot_user_id, "9999")
        self.assertTrue(grant.is_active)
        # Tokens should be encrypted (not plain text)
        self.assertNotEqual(grant.access_token, "bot-access-token-xyz")
        self.assertNotEqual(grant.refresh_token, "bot-refresh-token-abc")

    def test_bot_token_callback_rejects_invalid_state(self):
        """OAuth callback rejects invalid or expired state"""
        response = self.client.get(
            "/oauth/twitch/bot/callback",
            {"code": "auth-code", "state": "invalid-state"},
        )
        self.assertEqual(response.status_code, 400)
        payload = response.json()
        self.assertFalse(payload.get("ok"))
        self.assertEqual(payload.get("error"), "invalid_state")

    @override_settings(
        TWITCH_CLIENT_ID="test-client",
        TWITCH_CLIENT_SECRET="test-secret",
        TWITCH_TOKEN_REDIRECT_URI="http://localhost/oauth/twitch/callback",
        TWITCH_ONBOARD_STATE_TTL_SECONDS=600,
    )
    @patch("simpwatch.views._exchange_twitch_code_for_tokens")
    @patch("simpwatch.views._validate_twitch_access_token")
    def test_bot_token_callback_rejects_missing_required_scopes(self, mock_validate, mock_exchange):
        mock_exchange.return_value = {
            "access_token": "bot-access-token-xyz",
            "refresh_token": "bot-refresh-token-abc",
            "expires_in": 3600,
        }
        mock_validate.return_value = {
            "login": "simpbot",
            "user_id": "9999",
            "scopes": ["channel:bot"],
        }

        state = "test-state-missing-scopes"
        cache.set(f"twitch:onboard:state:bot:{state}", True, 600)

        response = self.client.get(
            "/oauth/twitch/bot/callback",
            {"code": "auth-code-123", "state": state},
        )
        self.assertEqual(response.status_code, 400)
        payload = response.json()
        self.assertFalse(payload.get("ok"))
        self.assertEqual(payload.get("error"), "insufficient_scopes")
        self.assertIn("user:bot", payload.get("missing_scopes", []))
        self.assertFalse(TwitchBotGrant.objects.filter(bot_username="simpbot").exists())
