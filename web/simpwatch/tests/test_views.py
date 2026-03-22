from datetime import timedelta

from django.test import TestCase
from django.utils import timezone

from simpwatch.models import Identity, Person, SimpEvent


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
