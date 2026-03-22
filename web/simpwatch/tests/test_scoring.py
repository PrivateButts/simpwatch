from django.contrib.auth import get_user_model
from django.test import TestCase
from typing import cast

from simpwatch.models import Identity, Person, ScoreAdjustment, SimpEvent
from simpwatch.scoring import (
    IdentityInput,
    get_bamder_counts,
    get_leaderboard_entries,
    get_person_score_and_rank,
    merge_people,
    register_simp,
)


class ScoringTests(TestCase):
    def test_register_simp_persists_reason(self):
        target = Person.objects.create(name="target")

        event = register_simp(
            actor=IdentityInput(
                platform=str(Identity.Platform.TWITCH),
                platform_user_id="actor-1",
                username="caller",
                display_name="Caller",
            ),
            target=target,
            platform=str(Identity.Platform.TWITCH),
            source="streamer_channel",
            reason="gifted 10 subs",
            raw_content="!simp @target reason gifted 10 subs",
            message_id="m1",
            dedupe_key="twitch:m1",
        )

        self.assertIsNotNone(event)
        event = cast(SimpEvent, event)
        self.assertEqual(event.reason, "gifted 10 subs")

    def test_merge_people_reassigns_related_records(self):
        target = Person.objects.create(name="Canonical")
        source = Person.objects.create(name="Duplicate")
        actor_person = Person.objects.create(name="Actor")

        actor_identity = Identity.objects.create(
            person=actor_person,
            platform=Identity.Platform.TWITCH,
            platform_user_id="actor-1",
            username="actor",
            display_name="actor",
        )
        source_identity = Identity.objects.create(
            person=source,
            platform=Identity.Platform.DISCORD,
            platform_user_id="source-1",
            username="duplicate",
            display_name="duplicate",
        )

        SimpEvent.objects.create(
            actor_identity=actor_identity,
            target_person=source,
            platform=Identity.Platform.TWITCH,
            source="chan",
            points=1,
            reason="reason",
        )

        user_model = get_user_model()
        admin_user = user_model.objects.create_user(
            username="admin",
            password="password123",
        )
        ScoreAdjustment.objects.create(
            target_person=source,
            points_delta=3,
            reason="manual correction",
            created_by=admin_user,
        )

        deleted_count = merge_people(target=target, sources=[source])

        self.assertGreaterEqual(deleted_count, 1)
        self.assertFalse(Person.objects.filter(id=source.id).exists())
        source_identity.refresh_from_db()
        self.assertEqual(source_identity.person_id, target.id)
        self.assertEqual(SimpEvent.objects.get().target_person_id, target.id)
        self.assertEqual(ScoreAdjustment.objects.get().target_person_id, target.id)

    def test_register_bamder_event_type(self):
        target = Person.objects.create(name="pamder")
        event = register_simp(
            actor=IdentityInput(
                platform=str(Identity.Platform.TWITCH),
                platform_user_id="actor-2",
                username="caller2",
                display_name="Caller2",
            ),
            target=target,
            platform=str(Identity.Platform.TWITCH),
            event_type=str(SimpEvent.EventType.BAMDER),
            source="streamer_channel",
            reason="was chaotic",
            raw_content="!bamder reason was chaotic",
            message_id="m2",
            dedupe_key="twitch:m2",
        )

        self.assertIsNotNone(event)
        event = cast(SimpEvent, event)
        self.assertEqual(event.event_type, SimpEvent.EventType.BAMDER)
        self.assertEqual(event.reason, "was chaotic")


class LeaderboardQueryTests(TestCase):
    """Tests for get_leaderboard_entries and get_person_score_and_rank."""

    def _actor(self, uid: str = "actor-1", username: str = "caller") -> IdentityInput:
        return IdentityInput(
            platform=str(Identity.Platform.TWITCH),
            platform_user_id=uid,
            username=username,
            display_name=username,
        )

    def _simp(self, actor: IdentityInput, target: Person, uid: str) -> None:
        register_simp(
            actor=actor,
            target=target,
            platform=str(Identity.Platform.TWITCH),
            source="chan",
            message_id=uid,
            dedupe_key=f"twitch:{uid}",
        )

    def test_get_leaderboard_entries_sorted_descending(self):
        p1 = Person.objects.create(name="p1")
        p2 = Person.objects.create(name="p2")
        self._simp(self._actor("a1", "c1"), p1, "m1")
        self._simp(self._actor("a2", "c2"), p2, "m2")
        self._simp(self._actor("a3", "c3"), p2, "m3")

        entries = get_leaderboard_entries()
        self.assertEqual(len(entries), 2)
        self.assertEqual(entries[0]["person"].id, p2.id)
        self.assertEqual(entries[0]["points"], 2)
        self.assertEqual(entries[1]["person"].id, p1.id)
        self.assertEqual(entries[1]["points"], 1)

    def test_get_leaderboard_entries_empty(self):
        self.assertEqual(get_leaderboard_entries(), [])

    def test_get_person_score_and_rank_found(self):
        p1 = Person.objects.create(name="top")
        Identity.objects.create(
            person=p1,
            platform=Identity.Platform.TWITCH,
            platform_user_id="top-1",
            username="top",
            display_name="top",
        )
        p2 = Person.objects.create(name="second")
        Identity.objects.create(
            person=p2,
            platform=Identity.Platform.TWITCH,
            platform_user_id="second-1",
            username="second",
            display_name="second",
        )
        self._simp(self._actor("a1", "c1"), p1, "m1")
        self._simp(self._actor("a2", "c2"), p1, "m2")
        self._simp(self._actor("a3", "c3"), p2, "m3")

        score, rank = get_person_score_and_rank("top")
        self.assertEqual(score, 2)
        self.assertEqual(rank, 1)

        score, rank = get_person_score_and_rank("second")
        self.assertEqual(score, 1)
        self.assertEqual(rank, 2)

    def test_get_person_score_and_rank_not_found(self):
        score, rank = get_person_score_and_rank("nobody")
        self.assertEqual(score, 0)
        self.assertIsNone(rank)

    def test_get_person_score_and_rank_case_insensitive(self):
        p = Person.objects.create(name="TopUser")
        Identity.objects.create(
            person=p,
            platform=Identity.Platform.TWITCH,
            platform_user_id="top-1",
            username="topuser",
            display_name="TopUser",
        )
        self._simp(self._actor(), p, "m1")

        score, rank = get_person_score_and_rank("TopUser")
        self.assertEqual(score, 1)
        self.assertEqual(rank, 1)


class BamderCountsTests(TestCase):
    """Tests for get_bamder_counts."""

    def _actor(self, uid: str, username: str) -> IdentityInput:
        return IdentityInput(
            platform=str(Identity.Platform.TWITCH),
            platform_user_id=uid,
            username=username,
            display_name=username,
        )

    def _bamder(self, actor: IdentityInput, target: Person, uid: str) -> None:
        register_simp(
            actor=actor,
            target=target,
            platform=str(Identity.Platform.TWITCH),
            event_type=str(SimpEvent.EventType.BAMDER),
            source="chan",
            message_id=uid,
            dedupe_key=f"twitch:{uid}",
        )

    def test_counts_start_at_zero(self):
        pamder = Person.objects.create(name="pamder")
        today, this_week, total = get_bamder_counts(pamder)
        self.assertEqual(today, 0)
        self.assertEqual(this_week, 0)
        self.assertEqual(total, 0)

    def test_counts_after_single_bamder(self):
        pamder = Person.objects.create(name="pamder")
        self._bamder(self._actor("a1", "user1"), pamder, "m1")
        today, this_week, total = get_bamder_counts(pamder)
        self.assertEqual(today, 1)
        self.assertEqual(this_week, 1)
        self.assertEqual(total, 1)

    def test_counts_multiple_bamders(self):
        pamder = Person.objects.create(name="pamder")
        self._bamder(self._actor("a1", "user1"), pamder, "m1")
        self._bamder(self._actor("a2", "user2"), pamder, "m2")
        self._bamder(self._actor("a3", "user3"), pamder, "m3")
        today, this_week, total = get_bamder_counts(pamder)
        self.assertEqual(today, 3)
        self.assertEqual(this_week, 3)
        self.assertEqual(total, 3)

    def test_simp_events_not_counted(self):
        pamder = Person.objects.create(name="pamder")
        # Register a simp event (not bamder) — should not affect counts
        register_simp(
            actor=self._actor("a1", "user1"),
            target=pamder,
            platform=str(Identity.Platform.TWITCH),
            event_type=str(SimpEvent.EventType.SIMP),
            source="chan",
            message_id="ms1",
            dedupe_key="twitch:ms1",
        )
        today, this_week, total = get_bamder_counts(pamder)
        self.assertEqual(today, 0)
        self.assertEqual(this_week, 0)
        self.assertEqual(total, 0)

    def test_counts_isolated_per_person(self):
        pamder = Person.objects.create(name="pamder")
        other = Person.objects.create(name="other")
        self._bamder(self._actor("a1", "user1"), pamder, "m1")
        self._bamder(self._actor("a2", "user2"), other, "m2")
        today, this_week, total = get_bamder_counts(pamder)
        self.assertEqual(total, 1)
