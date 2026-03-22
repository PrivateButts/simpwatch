from django.contrib.auth import get_user_model
from django.test import TestCase
from typing import cast

from simpwatch.models import Identity, Person, ScoreAdjustment, SimpEvent
from simpwatch.scoring import IdentityInput, merge_people, register_simp


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
