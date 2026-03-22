from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from typing import Iterable

from django.conf import settings
from django.db import transaction
from django.db.models import Sum
from django.utils import timezone

from .models import Identity, Person, ScoreAdjustment, ScoringConfig, SimpEvent


@dataclass
class IdentityInput:
    platform: str
    platform_user_id: str
    username: str
    display_name: str = ""


def normalize_username(username: str) -> str:
    return username.strip().lstrip("@").lower()


def get_or_create_person_for_identity(identity: Identity) -> Person:
    return identity.person


def get_or_create_identity(identity_input: IdentityInput) -> Identity:
    username = normalize_username(identity_input.username)
    identity = Identity.objects.filter(
        platform=identity_input.platform,
        platform_user_id=identity_input.platform_user_id,
    ).first()
    created = False
    if not identity:
        existing_username_identity = Identity.objects.filter(
            platform=identity_input.platform,
            username=username,
        ).first()
        person = (
            existing_username_identity.person
            if existing_username_identity
            else Person.objects.create(name=identity_input.display_name or username)
        )
        identity = Identity.objects.create(
            platform=identity_input.platform,
            platform_user_id=identity_input.platform_user_id,
            username=username,
            display_name=identity_input.display_name or username,
            person=person,
        )
        created = True
    if not created:
        changed = False
        if identity.username != username:
            identity.username = username
            changed = True
        if (
            identity_input.display_name
            and identity.display_name != identity_input.display_name
        ):
            identity.display_name = identity_input.display_name
            changed = True
        if changed:
            identity.save(update_fields=["username", "display_name"])
    return identity


def get_or_create_twitch_target(username: str) -> Person:
    normalized = normalize_username(username)
    identity = Identity.objects.filter(
        platform=Identity.Platform.TWITCH, username=normalized
    ).first()
    if identity:
        return identity.person
    person = Person.objects.create(name=normalized)
    Identity.objects.create(
        person=person,
        platform=Identity.Platform.TWITCH,
        platform_user_id=f"pending:{normalized}",
        username=normalized,
        display_name=normalized,
    )
    return person


def get_scoring_config() -> ScoringConfig:
    config = ScoringConfig.objects.first()
    if config:
        return config
    return ScoringConfig.objects.create(
        cooldown_seconds=getattr(settings, "SIMP_DEFAULT_COOLDOWN_SECONDS", 0),
        default_points=getattr(settings, "SIMP_DEFAULT_POINTS", 1),
    )


def _cooldown_active(
    actor: Identity, target: Person, platform: str, cooldown_seconds: int
) -> bool:
    if cooldown_seconds <= 0:
        return False
    threshold = timezone.now() - timedelta(seconds=cooldown_seconds)
    return SimpEvent.objects.filter(
        actor_identity=actor,
        target_person=target,
        platform=platform,
        created_at__gte=threshold,
    ).exists()


@transaction.atomic
def register_simp(
    actor: IdentityInput,
    target: Person,
    platform: str,
    source: str,
    reason: str = "",
    raw_content: str = "",
    message_id: str = "",
    dedupe_key: str = "",
) -> SimpEvent | None:
    actor_identity = get_or_create_identity(actor)
    config = get_scoring_config()
    if _cooldown_active(actor_identity, target, platform, config.cooldown_seconds):
        return None
    event = SimpEvent.objects.create(
        actor_identity=actor_identity,
        target_person=target,
        platform=platform,
        source=source,
        points=config.default_points,
        reason=reason,
        raw_content=raw_content,
        message_id=message_id,
        dedupe_key=dedupe_key,
    )
    return event


_WINDOW_DELTAS = {
    "24h": timedelta(hours=24),
    "7d": timedelta(days=7),
    "30d": timedelta(days=30),
}


def get_leaderboard_entries(window: str = "all") -> list[dict]:
    """Return leaderboard rows sorted by total score descending.

    Each row is a dict with keys ``person`` (Person) and ``points`` (int).
    Supports the same window values as the web leaderboard: ``"24h"``,
    ``"7d"``, ``"30d"``, and ``"all"`` (default).
    """
    delta = _WINDOW_DELTAS.get(window)
    since = timezone.now() - delta if delta is not None else None

    event_qs = SimpEvent.objects.all()
    adjustment_qs = ScoreAdjustment.objects.all()
    if since is not None:
        event_qs = event_qs.filter(created_at__gte=since)
        adjustment_qs = adjustment_qs.filter(created_at__gte=since)

    event_totals = {
        row["target_person"]: row["total"]
        for row in event_qs.values("target_person").annotate(total=Sum("points"))
    }
    adjustment_totals = {
        row["target_person"]: row["total"]
        for row in adjustment_qs.values("target_person").annotate(
            total=Sum("points_delta")
        )
    }
    person_ids = sorted(set(event_totals.keys()) | set(adjustment_totals.keys()))
    people = {p.id: p for p in Person.objects.filter(id__in=person_ids)}

    rows = []
    for person_id in person_ids:
        total = (event_totals.get(person_id) or 0) + (
            adjustment_totals.get(person_id) or 0
        )
        if total == 0:
            continue
        person = people.get(person_id)
        if not person:
            continue
        rows.append({"person": person, "points": total})
    rows.sort(key=lambda r: r["points"], reverse=True)
    return rows


def get_person_score_and_rank(
    username: str, window: str = "all"
) -> tuple[int, int | None]:
    """Return ``(score, rank)`` for a Twitch user by username.

    Rank is 1-based and derived from the sorted leaderboard.  Returns
    ``(0, None)`` when the person has no recorded score for the given window.
    Only searches Twitch platform identities.
    """
    normalized = normalize_username(username)
    identity = Identity.objects.filter(
        platform=Identity.Platform.TWITCH, username=normalized
    ).first()
    if not identity:
        return 0, None

    target_person = identity.person
    entries = get_leaderboard_entries(window)
    for rank, row in enumerate(entries, start=1):
        if row["person"].id == target_person.id:
            return row["points"], rank
    return 0, None


def person_total_score(person: Person, since=None) -> int:
    events = SimpEvent.objects.filter(target_person=person)
    adjustments = ScoreAdjustment.objects.filter(target_person=person)
    if since is not None:
        events = events.filter(created_at__gte=since)
        adjustments = adjustments.filter(created_at__gte=since)
    event_points = events.aggregate(total=Sum("points"))["total"] or 0
    adjustment_points = adjustments.aggregate(total=Sum("points_delta"))["total"] or 0
    return event_points + adjustment_points


@transaction.atomic
def merge_people(target: Person, sources: Iterable[Person]) -> int:
    source_ids = [
        person.id for person in sources if person.id and person.id != target.id
    ]
    if not source_ids:
        return 0

    Identity.objects.filter(person_id__in=source_ids).update(person=target)
    SimpEvent.objects.filter(target_person_id__in=source_ids).update(
        target_person=target
    )
    ScoreAdjustment.objects.filter(target_person_id__in=source_ids).update(
        target_person=target
    )

    deleted_count, _ = Person.objects.filter(id__in=source_ids).delete()
    return deleted_count
