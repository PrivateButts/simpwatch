import asyncio
import logging
import os
import sys
import time

from twitchio.ext import commands
from twitchio import errors as twitch_errors
from asgiref.sync import sync_to_async

from services.common_setup import setup_django
from services.twitch_bot.healthcheck import clear_heartbeat, mark_healthy


setup_django()

logger = logging.getLogger("twitch_bot")

# --- In-process telemetry counters (reset every stats interval) ---
_stats: dict[str, int] = {
    "messages_seen": 0,
    "commands_seen": 0,
    "events_registered": 0,
    "cooldowns": 0,
    "errors": 0,
}

# How long (seconds) with no incoming messages before the watchdog forces a reconnect.
_WATCHDOG_TIMEOUT_SECONDS = int(os.getenv("TWITCH_WATCHDOG_TIMEOUT", "300"))
# How often (seconds) to emit the periodic stats log line.
_STATS_INTERVAL_SECONDS = int(os.getenv("TWITCH_STATS_INTERVAL", "300"))

from simpwatch.models import Identity, SimpEvent  # noqa: E402

from simpwatch.command_parsing import (  # noqa: E402
    parse_bot_simp_args,
    parse_bot_mention_command,
    parse_twitch_bamder_reason,
    parse_twitch_reason,
)
from simpwatch.scoring import (  # noqa: E402
    IdentityInput,
    get_bamder_counts,
    get_leaderboard_entries,
    get_or_create_named_person,
    get_or_create_twitch_target,
    get_person_score_and_rank,
    get_score_and_rank_for_person,
    normalize_username,
    register_simp,
)


def _ordinal(n: int) -> str:
    """Return the ordinal string for a positive integer (e.g. 1 -> '1st')."""
    if 11 <= (n % 100) <= 13:
        suffix = "th"
    else:
        suffix = {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suffix}"


class TwitchSimpBot(commands.Bot):
    def __init__(self) -> None:
        channels = [
            c.strip().lower()
            for c in os.getenv("TWITCH_CHANNELS", "").split(",")
            if c.strip()
        ]
        self._heartbeat_task: asyncio.Task[None] | None = None
        self._watchdog_task: asyncio.Task[None] | None = None
        self._stats_task: asyncio.Task[None] | None = None
        self._last_message_at: float = time.monotonic()
        self._reply_channels: set[str] = {
            c.strip().lower()
            for c in os.getenv("TWITCH_REPLY_CHANNELS", "").split(",")
            if c.strip()
        }
        super().__init__(
            token=os.getenv("TWITCH_OAUTH_TOKEN", ""),
            prefix="!",
            initial_channels=channels,
            nick=os.getenv("TWITCH_BOT_USERNAME", ""),
        )

    async def event_ready(self):
        channels = [c.name for c in self.connected_channels]
        logger.info("Twitch bot ready nick=%s channels=%s", self.nick, channels)
        self._last_message_at = time.monotonic()
        self._start_background_tasks()

    async def event_disconnect(self):
        logger.warning("Twitch bot disconnected")
        self._stop_background_tasks()

    async def event_error(self, error: Exception, data: str | None = None) -> None:
        _stats["errors"] += 1
        logger.exception(
            "TwitchIO event error data=%r",
            data[:200] if data else None,
            exc_info=error,
        )

    # ------------------------------------------------------------------
    # Background task management
    # ------------------------------------------------------------------

    def _start_background_tasks(self) -> None:
        self._stop_background_tasks()
        mark_healthy()
        self._heartbeat_task = asyncio.create_task(self._heartbeat_loop())
        self._watchdog_task = asyncio.create_task(self._watchdog_loop())
        self._stats_task = asyncio.create_task(self._stats_loop())

    def _stop_background_tasks(self) -> None:
        for attr in ("_heartbeat_task", "_watchdog_task", "_stats_task"):
            task: asyncio.Task | None = getattr(self, attr, None)
            if task is not None:
                task.cancel()
            setattr(self, attr, None)
        clear_heartbeat()

    async def _heartbeat_loop(self) -> None:
        try:
            while True:
                mark_healthy()
                await asyncio.sleep(30)
        except asyncio.CancelledError:
            raise

    async def _watchdog_loop(self) -> None:
        try:
            while True:
                await asyncio.sleep(60)
                idle_seconds = time.monotonic() - self._last_message_at
                if idle_seconds > _WATCHDOG_TIMEOUT_SECONDS:
                    logger.warning(
                        "Watchdog: no messages for %.0fs (threshold %ds), "
                        "forcing reconnect",
                        idle_seconds,
                        _WATCHDOG_TIMEOUT_SECONDS,
                    )
                    await self.close()
                    return
        except asyncio.CancelledError:
            raise

    async def _stats_loop(self) -> None:
        try:
            while True:
                await asyncio.sleep(_STATS_INTERVAL_SECONDS)
                logger.info(
                    "stats messages_seen=%d commands_seen=%d "
                    "events_registered=%d cooldowns=%d errors=%d",
                    _stats["messages_seen"],
                    _stats["commands_seen"],
                    _stats["events_registered"],
                    _stats["cooldowns"],
                    _stats["errors"],
                )
                for key in _stats:
                    _stats[key] = 0
        except asyncio.CancelledError:
            raise

    async def event_message(self, message):
        if message.echo:
            return

        self._last_message_at = time.monotonic()
        _stats["messages_seen"] += 1
        content = (message.content or "").strip()

        logger.debug(
            "message channel=%s author=%s content=%r",
            getattr(message.channel, "name", "?"),
            getattr(message.author, "name", "?"),
            content[:80],
        )

        try:
            await self._process_message(message, content)
        except Exception:
            _stats["errors"] += 1
            logger.exception(
                "Unhandled error processing message channel=%s author=%s content=%r",
                getattr(message.channel, "name", "?"),
                getattr(message.author, "name", "?"),
                content[:80],
            )

    async def _process_message(self, message, content: str) -> None:
        bot_cmd = parse_bot_mention_command(content, self.nick or "")
        if bot_cmd is not None:
            _stats["commands_seen"] += 1
            command, args = bot_cmd
            await self._handle_bot_command(message, command, args)
            return

        lowered = content.lower()
        if not lowered.startswith("!simp") and not lowered.startswith("!bamder"):
            return

        _stats["commands_seen"] += 1

        actor_input = IdentityInput(
            platform=Identity.Platform.TWITCH,
            platform_user_id=str(message.author.id),
            username=message.author.name,
            display_name=message.author.display_name or message.author.name,
        )

        if lowered.startswith("!bamder"):
            target_person = await sync_to_async(get_or_create_named_person)("pamder")
            reason = parse_twitch_bamder_reason(content)
            event_type = str(SimpEvent.EventType.BAMDER)
        else:
            parts = content.split()
            if len(parts) > 1 and parts[1].startswith("@"):
                bot_name = (self.nick or "bot").lstrip("@")
                await message.channel.send(
                    f"Use @{bot_name} simp @username for targeted simp callouts."
                )
                return

            broadcaster = message.channel.name
            target_person = await sync_to_async(get_or_create_twitch_target)(
                broadcaster
            )
            reason = parse_twitch_reason(content)
            event_type = str(SimpEvent.EventType.SIMP)

        event = await sync_to_async(register_simp)(
            actor=actor_input,
            target=target_person,
            platform=Identity.Platform.TWITCH,
            event_type=event_type,
            source=message.channel.name,
            reason=reason,
            raw_content=content,
            message_id=str(message.id),
            dedupe_key=f"twitch:{message.id}",
        )
        if event:
            _stats["events_registered"] += 1
            logger.info(
                "event registered platform=twitch type=%s actor=%s target=%s "
                "channel=%s event_id=%d points=%d",
                event_type,
                actor_input.username,
                target_person.name,
                message.channel.name,
                event.id,
                event.points,
            )
            if message.channel.name in self._reply_channels:
                if event_type == str(SimpEvent.EventType.BAMDER):
                    today, this_week, total = await sync_to_async(get_bamder_counts)(
                        target_person
                    )
                    await message.channel.send(
                        f"Pamder has acted out AGAIN! "
                        f"This is the {_ordinal(today)} time today, "
                        f"{_ordinal(this_week)} time this week, "
                        f"{_ordinal(total)} time total. "
                        f"Someone oughta do something about that..."
                    )
                else:
                    score, rank = await sync_to_async(get_score_and_rank_for_person)(
                        target_person
                    )
                    if rank is not None:
                        await message.channel.send(
                            f"{target_person.name} is ranked #{rank} with {score} point(s)."
                        )
                    else:
                        await message.channel.send(
                            f"{target_person.name} has been registered!"
                        )
        else:
            _stats["cooldowns"] += 1
            logger.debug(
                "cooldown active type=%s actor=%s target=%s channel=%s",
                event_type,
                actor_input.username,
                target_person.name,
                message.channel.name,
            )

    async def _handle_bot_command(self, message, command: str, args: list[str]) -> None:
        channel = message.channel

        if command == "simpcheck":
            target_username = (
                normalize_username(args[0]) if args else message.channel.name
            )
            score, rank = await sync_to_async(get_person_score_and_rank)(
                target_username
            )
            if rank is None:
                await channel.send(f"{target_username} has no score yet.")
            else:
                await channel.send(
                    f"{target_username} is ranked #{rank} with {score} point(s)."
                )

        elif command == "standings":
            limit = 3
            if args:
                try:
                    limit = max(1, min(int(args[0]), 10))
                except ValueError:
                    pass
            entries = await sync_to_async(get_leaderboard_entries)()
            top = entries[:limit]
            if not top:
                await channel.send("No standings yet!")
            else:
                parts = [
                    f"#{i + 1} {row['person'].name} ({row['points']} pts)"
                    for i, row in enumerate(top)
                ]
                await channel.send(f"Top {len(top)} simps: " + ", ".join(parts))

        elif command == "simp":
            parsed = parse_bot_simp_args(args)
            if parsed is None:
                bot_name = (self.nick or "bot").lstrip("@")
                await channel.send(
                    f"Usage: @{bot_name} simp @username [reason <text>|because <text>]"
                )
                return

            target_username, reason = parsed
            actor_input = IdentityInput(
                platform=Identity.Platform.TWITCH,
                platform_user_id=str(message.author.id),
                username=message.author.name,
                display_name=message.author.display_name or message.author.name,
            )
            target_person = await sync_to_async(get_or_create_twitch_target)(
                target_username
            )
            event = await sync_to_async(register_simp)(
                actor=actor_input,
                target=target_person,
                platform=Identity.Platform.TWITCH,
                event_type=str(SimpEvent.EventType.SIMP),
                source=message.channel.name,
                reason=reason,
                raw_content=message.content or "",
                message_id=str(message.id),
                dedupe_key=f"twitch:mention:{message.id}",
            )
            if event:
                _stats["events_registered"] += 1
                logger.info(
                    "event registered platform=twitch type=simp actor=%s target=%s "
                    "channel=%s event_id=%d points=%d",
                    message.author.name,
                    target_person.name,
                    message.channel.name,
                    event.id,
                    event.points,
                )
                score, rank = await sync_to_async(get_score_and_rank_for_person)(
                    target_person
                )
                if rank is not None:
                    await channel.send(
                        f"{target_person.name} is ranked #{rank} with {score} point(s)."
                    )
            else:
                _stats["cooldowns"] += 1
                logger.debug(
                    "cooldown active type=simp actor=%s target=%s channel=%s",
                    message.author.name,
                    target_person.name,
                    message.channel.name,
                )


if __name__ == "__main__":
    logging.basicConfig(
        level=os.getenv("LOG_LEVEL", "INFO").upper(),
        stream=sys.stdout,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    clear_heartbeat()
    token = os.getenv("TWITCH_OAUTH_TOKEN", "").strip()
    channels = os.getenv("TWITCH_CHANNELS", "").strip()
    if not token or not channels:
        logger.warning(
            "Twitch bot disabled: set TWITCH_OAUTH_TOKEN and TWITCH_CHANNELS"
        )
        while True:
            time.sleep(300)

    while True:
        try:
            bot = TwitchSimpBot()
            bot.run()
        except twitch_errors.AuthenticationError:
            clear_heartbeat()
            logger.error("Twitch bot auth failed: verify TWITCH_OAUTH_TOKEN")
            time.sleep(60)
        except Exception as exc:
            clear_heartbeat()
            logger.exception("Twitch bot crashed: %s", exc)
            time.sleep(5)
