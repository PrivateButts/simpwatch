import asyncio
import json
import logging
import os
import sys
import time
import urllib.request
from twitchio.exceptions import HTTPException

from twitchio import eventsub
from twitchio.ext import commands
from asgiref.sync import sync_to_async
from prometheus_client import start_http_server

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
# How long (seconds) to wait for database operations before timing out.
_DB_OPERATION_TIMEOUT_SECONDS = int(os.getenv("TWITCH_DB_TIMEOUT", "10"))
# How many reconnect attempts the watchdog should try before forcing process restart.
_WATCHDOG_MAX_RECONNECT_ATTEMPTS = int(
    os.getenv("TWITCH_WATCHDOG_MAX_RECONNECT_ATTEMPTS", "3")
)
_METRICS_ENABLED = os.getenv("TWITCH_METRICS_ENABLED", "true").lower() == "true"
_METRICS_PORT = int(os.getenv("TWITCH_METRICS_PORT", "9090"))
_TWITCH_CHANNEL_LOGINS = [
    c.strip().lower()
    for c in os.getenv("TWITCH_CHANNELS", "").split(",")
    if c.strip()
]
_TWITCH_REPLY_CHANNELS = {
    c.strip().lower()
    for c in os.getenv("TWITCH_REPLY_CHANNELS", "").split(",")
    if c.strip()
}
_REPLY_GRANT_CACHE_SECONDS = int(os.getenv("TWITCH_REPLY_GRANT_CACHE_SECONDS", "30"))

from simpwatch.models import Identity, SimpEvent, TwitchBroadcasterGrant  # noqa: E402

from simpwatch.command_parsing import (  # noqa: E402
    parse_bot_simp_args,
    parse_bot_mention_command,
    parse_twitch_bamder_reason,
    parse_twitch_reason,
)
from simpwatch.metrics import (  # noqa: E402
    twitch_commands_total,
    twitch_cooldowns_total,
    twitch_errors_total,
    twitch_events_registered_total,
    twitch_irc_idle_seconds,
    twitch_messages_total,
    twitch_watchdog_reconnect_attempts,
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


async def _db_call(coro_func, *args, **kwargs):
    """Wrap a sync_to_async call with timeout and error handling."""
    try:
        result = await asyncio.wait_for(
            sync_to_async(coro_func)(*args, **kwargs),
            timeout=_DB_OPERATION_TIMEOUT_SECONDS,
        )
        return result
    except asyncio.TimeoutError:
        _stats["errors"] += 1
        twitch_errors_total.labels("db_timeout").inc()
        logger.error(
            "Database operation timed out after %ds: %s",
            _DB_OPERATION_TIMEOUT_SECONDS,
            coro_func.__name__,
        )
        raise
    except Exception as e:
        _stats["errors"] += 1
        twitch_errors_total.labels("db_exception").inc()
        logger.exception("Database operation failed in %s", coro_func.__name__)
        raise


def _ordinal(n: int) -> str:
    """Return the ordinal string for a positive integer (e.g. 1 -> '1st')."""
    if 11 <= (n % 100) <= 13:
        suffix = "th"
    else:
        suffix = {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suffix}"


def _validate_bot_token_for_eventsub(
    access_token: str,
    expected_bot_id: str,
) -> None:
    """Validate bot token identity/scopes required for EventSub chat subscriptions."""

    required_scopes = {"user:bot", "user:read:chat", "user:write:chat"}
    req = urllib.request.Request(
        "https://id.twitch.tv/oauth2/validate",
        headers={"Authorization": f"OAuth {access_token}"},
        method="GET",
    )
    with urllib.request.urlopen(req, timeout=10) as response:
        payload = json.loads(response.read().decode("utf-8"))

    token_user_id = str(payload.get("user_id", "")).strip()
    scopes = set(payload.get("scopes", []))
    missing_scopes = sorted(required_scopes - scopes)
    if token_user_id != expected_bot_id:
        raise RuntimeError(
            "TWITCH_BOT_ACCESS_TOKEN does not belong to TWITCH_BOT_ID. "
            "Regenerate tokens for the bot account and update .env values."
        )
    if missing_scopes:
        raise RuntimeError(
            "TWITCH_BOT_ACCESS_TOKEN is missing required scopes for EventSub chat: "
            f"{', '.join(missing_scopes)}. Regenerate using `just twitch-token` "
            "with TWITCH_TOKEN_SCOPES including user:bot user:read:chat user:write:chat."
        )


def _has_active_broadcaster_grant(username: str) -> bool:
    return TwitchBroadcasterGrant.objects.filter(
        username=username,
        is_active=True,
    ).exists()


class TwitchSimpBot(commands.Bot):
    def __init__(self) -> None:
        self._bot_username = os.getenv("TWITCH_BOT_USERNAME", "").strip()
        self._bot_access_token = os.getenv("TWITCH_BOT_ACCESS_TOKEN", "").strip()
        self._bot_refresh_token = os.getenv("TWITCH_BOT_REFRESH_TOKEN", "").strip()
        self._client_secret = os.getenv("TWITCH_CLIENT_SECRET", "").strip() or None
        self._bot_id = os.getenv("TWITCH_BOT_ID", "").strip() or None
        self._channel_logins = list(_TWITCH_CHANNEL_LOGINS)
        self._heartbeat_task: asyncio.Task[None] | None = None
        self._watchdog_task: asyncio.Task[None] | None = None
        self._stats_task: asyncio.Task[None] | None = None
        self._last_message_at: float = time.monotonic()
        self._last_irc_at: float = time.monotonic()
        self._watchdog_reconnect_attempts: int = 0
        self._reply_channels: set[str] = set(_TWITCH_REPLY_CHANNELS)
        self._reply_grant_cache: dict[str, tuple[float, bool]] = {}
        super().__init__(
            client_id=os.getenv("TWITCH_CLIENT_ID", "").strip(),
            client_secret=self._client_secret,
            bot_id=self._bot_id,
            owner_id=None,
            prefix="!",
        )
        self.nick = self._bot_username

    async def setup_hook(self) -> None:
        if not self._bot_id:
            raise RuntimeError(
                "TWITCH_BOT_ID is required for EventSub websocket subscriptions."
            )

        if not self._bot_access_token or not self._bot_refresh_token:
            raise RuntimeError(
                "TWITCH_BOT_ACCESS_TOKEN and TWITCH_BOT_REFRESH_TOKEN are required to subscribe to Twitch chat via EventSub."
            )

        await asyncio.to_thread(
            _validate_bot_token_for_eventsub,
            self._bot_access_token,
            self._bot_id,
        )

        await self.add_token(self._bot_access_token, self._bot_refresh_token)
        await self._subscribe_to_channels()

    async def event_ready(self):
        logger.info("Twitch bot ready user=%s channels=%s", self.user, self._channel_logins)
        self._last_message_at = time.monotonic()
        self._last_irc_at = time.monotonic()
        self._watchdog_reconnect_attempts = 0
        twitch_watchdog_reconnect_attempts.set(0)
        self._start_background_tasks()

    async def _subscribe_to_channels(self) -> None:
        if not self._channel_logins:
            raise RuntimeError(
                "TWITCH_CHANNELS must list at least one Twitch channel to monitor."
            )

        users = await self.fetch_users(logins=self._channel_logins)
        users_by_login = {user.name.lower(): user for user in users}

        missing = [login for login in self._channel_logins if login not in users_by_login]
        if missing:
            raise RuntimeError(
                f"Unable to resolve Twitch channel logins: {', '.join(missing)}"
            )

        for user in users:
            if user.id == self.bot_id:
                continue

            subscription = eventsub.ChatMessageSubscription(
                broadcaster_user_id=user.id,
                user_id=self.bot_id,
            )
            await self.subscribe_websocket(subscription, as_bot=True)

    async def event_raw_data(self, _data: str) -> None:
        # Any inbound EventSub websocket traffic indicates the connection is alive.
        self._last_irc_at = time.monotonic()

    async def event_disconnect(self):
        logger.warning("Twitch bot disconnected")
        self._stop_background_tasks()

    async def event_error(self, payload, data: str | None = None, **_kwargs) -> None:
        _stats["errors"] += 1
        twitch_errors_total.labels("event_error").inc()
        error = getattr(payload, "error", payload)
        original = getattr(payload, "original", data)
        logger.error("TwitchIO event error original=%r error=%r", original, error)

    async def _send_message(self, message, content: str) -> None:
        """Send a message to chat using the appropriate method.

        For EventSub chat messages: prefer the event's respond helper.
        For IRC/test messages: use channel.send (for backwards compatibility and tests).
        """
        if hasattr(message, "respond"):
            channel_name = self._message_channel_name(message).strip().lower()
            if not await self._is_reply_authorized_for_channel(channel_name):
                logger.warning(
                    "Skipping Twitch reply for channel=%s: missing active broadcaster grant",
                    channel_name,
                )
                return
            try:
                await message.respond(content)
                return
            except HTTPException as exc:
                status = getattr(exc, "status", None)
                detail = str(exc)
                channel_name = self._message_channel_name(message)
                if status == 401 and "channel:bot" in detail:
                    logger.error(
                        "Twitch reply unauthorized for channel=%s. "
                        "Either grant channel:bot to this app for that broadcaster "
                        "or make the bot a moderator in the channel.",
                        channel_name,
                    )
                else:
                    logger.exception(
                        "Failed to send EventSub response via message.respond"
                    )
            except Exception:
                logger.exception("Failed to send EventSub response via message.respond")

        # Fallback: try channel.send (IRC or test mocks)
        channel = getattr(message, "channel", None)
        if channel is not None and hasattr(channel, "send"):
            await channel.send(content)
            return

        ctx = self.get_context(message)
        await ctx.send(content)

    async def _is_reply_authorized_for_channel(self, channel_name: str) -> bool:
        if not channel_name:
            return False

        now = time.monotonic()
        cached = self._reply_grant_cache.get(channel_name)
        if cached is not None:
            ts, allowed = cached
            if now - ts <= _REPLY_GRANT_CACHE_SECONDS:
                return allowed

        allowed = await _db_call(_has_active_broadcaster_grant, channel_name)
        self._reply_grant_cache[channel_name] = (now, bool(allowed))
        return bool(allowed)

    @staticmethod
    def _message_channel_name(message) -> str:
        channel = getattr(message, "channel", None)
        broadcaster = getattr(message, "broadcaster", None)
        return (
            getattr(channel, "name", None)
            or getattr(broadcaster, "name", None)
            or "?"
        )

    @staticmethod
    def _message_author_name(message) -> str:
        chatter = getattr(message, "chatter", None)
        author = getattr(message, "author", None)
        return (
            getattr(chatter, "name", None)
            or getattr(author, "name", None)
            or "?"
        )

    @staticmethod
    def _message_author_id(message) -> str:
        chatter = getattr(message, "chatter", None)
        author = getattr(message, "author", None)
        return str(getattr(chatter, "id", None) or getattr(author, "id", None) or "")

    @staticmethod
    def _message_display_name(message) -> str:
        chatter = getattr(message, "chatter", None)
        author = getattr(message, "author", None)
        return (
            getattr(chatter, "display_name", None)
            or getattr(author, "display_name", None)
            or TwitchSimpBot._message_author_name(message)
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
                idle_seconds = time.monotonic() - self._last_irc_at
                twitch_irc_idle_seconds.set(idle_seconds)
                if idle_seconds > _WATCHDOG_TIMEOUT_SECONDS:
                    self._watchdog_reconnect_attempts += 1
                    twitch_watchdog_reconnect_attempts.set(
                        self._watchdog_reconnect_attempts
                    )
                    channels = list(self._channel_logins)
                    logger.warning(
                        "Watchdog: no IRC activity for %.0fs (threshold %ds), "
                        "forcing reconnect attempt %d/%d channels=%s",
                        idle_seconds,
                        _WATCHDOG_TIMEOUT_SECONDS,
                        self._watchdog_reconnect_attempts,
                        _WATCHDOG_MAX_RECONNECT_ATTEMPTS,
                        channels,
                    )
                    try:
                        await asyncio.wait_for(self.close(), timeout=5)
                    except asyncio.TimeoutError:
                        twitch_errors_total.labels("watchdog_close_timeout").inc()
                        logger.error("Failed to close bot connection (timeout)")
                    except Exception:
                        twitch_errors_total.labels("watchdog_close_exception").inc()
                        logger.exception("Error closing bot connection")

                    if (
                        self._watchdog_reconnect_attempts
                        >= _WATCHDOG_MAX_RECONNECT_ATTEMPTS
                    ):
                        logger.error(
                            "Watchdog reconnect attempts exhausted (%d), "
                            "forcing process restart",
                            self._watchdog_reconnect_attempts,
                        )
                        clear_heartbeat()
                        os._exit(1)

                    # Don't disable the watchdog after one failed reconnect attempt.
                    self._last_irc_at = time.monotonic()
        except asyncio.CancelledError:
            raise

    async def _stats_loop(self) -> None:
        try:
            while True:
                await asyncio.sleep(_STATS_INTERVAL_SECONDS)
                logger.info(
                    "stats messages_seen=%d commands_seen=%d "
                    "events_registered=%d cooldowns=%d errors=%d "
                    "irc_idle_seconds=%.0f watchdog_reconnect_attempts=%d",
                    _stats["messages_seen"],
                    _stats["commands_seen"],
                    _stats["events_registered"],
                    _stats["cooldowns"],
                    _stats["errors"],
                    time.monotonic() - self._last_irc_at,
                    self._watchdog_reconnect_attempts,
                )
                for key in _stats:
                    _stats[key] = 0
        except asyncio.CancelledError:
            raise

    async def event_message(self, message):
        if getattr(message, "echo", False):
            return

        self._last_message_at = time.monotonic()
        self._last_irc_at = time.monotonic()
        self._watchdog_reconnect_attempts = 0
        twitch_messages_total.inc()
        twitch_watchdog_reconnect_attempts.set(0)
        _stats["messages_seen"] += 1
        content = (getattr(message, "text", None) or getattr(message, "content", "") or "").strip()

        logger.debug(
            "message channel=%s author=%s content=%r",
            self._message_channel_name(message),
            self._message_author_name(message),
            content[:80],
        )

        try:
            await self._process_message(message, content)
        except Exception:
            _stats["errors"] += 1
            twitch_errors_total.labels("event_message_exception").inc()
            logger.exception(
                "Unhandled error processing message channel=%s author=%s content=%r",
                self._message_channel_name(message),
                self._message_author_name(message),
                content[:80],
            )

    async def _process_message(self, message, content: str) -> None:
        bot_cmd = parse_bot_mention_command(content, self.nick or self._bot_username or "")
        if bot_cmd is not None:
            _stats["commands_seen"] += 1
            command, args = bot_cmd
            twitch_commands_total.labels(command).inc()
            try:
                await self._handle_bot_command(message, command, args)
            except asyncio.TimeoutError:
                twitch_errors_total.labels("bot_command_timeout").inc()
                logger.error("Database timeout handling bot command")
            return

        lowered = content.lower()
        if not lowered.startswith("!simp") and not lowered.startswith("!bamder"):
            return

        _stats["commands_seen"] += 1
        if lowered.startswith("!bamder"):
            twitch_commands_total.labels("bamder").inc()
        else:
            twitch_commands_total.labels("simp").inc()

        actor_input = IdentityInput(
            platform=Identity.Platform.TWITCH,
            platform_user_id=self._message_author_id(message),
            username=self._message_author_name(message),
            display_name=self._message_display_name(message),
        )

        try:
            if lowered.startswith("!bamder"):
                target_person = await _db_call(get_or_create_named_person, "pamder")
                reason = parse_twitch_bamder_reason(content)
                event_type = str(SimpEvent.EventType.BAMDER)
            else:
                parts = content.split()
                if len(parts) > 1 and parts[1].startswith("@"):
                    bot_name = (self.nick or self._bot_username or "bot").lstrip("@")
                    await self._send_message(
                        message,
                        f"Use @{bot_name} simp @username for targeted simp callouts."
                    )
                    return

                broadcaster = self._message_channel_name(message)
                target_person = await _db_call(get_or_create_twitch_target, broadcaster)
                reason = parse_twitch_reason(content)
                event_type = str(SimpEvent.EventType.SIMP)

            event = await _db_call(
                register_simp,
                actor=actor_input,
                target=target_person,
                platform=Identity.Platform.TWITCH,
                event_type=event_type,
                source=self._message_channel_name(message),
                reason=reason,
                raw_content=content,
                message_id=str(getattr(message, "id", "")),
                dedupe_key=f"twitch:{getattr(message, 'id', '')}",
            )
            if event:
                _stats["events_registered"] += 1
                twitch_events_registered_total.labels(event_type).inc()
                logger.info(
                    "event registered platform=twitch type=%s actor=%s target=%s "
                    "channel=%s event_id=%d points=%d",
                    event_type,
                    actor_input.username,
                    target_person.name,
                    self._message_channel_name(message),
                    event.id,
                    event.points,
                )
                if self._message_channel_name(message) in self._reply_channels:
                    if event_type == str(SimpEvent.EventType.BAMDER):
                        today, this_week, total = await _db_call(get_bamder_counts, target_person)
                        await self._send_message(
                            message,
                            f"Pamder has acted out AGAIN! "
                            f"This is the {_ordinal(today)} time today, "
                            f"{_ordinal(this_week)} time this week, "
                            f"{_ordinal(total)} time total. "
                            f"Someone oughta do something about that..."
                        )
                    else:
                        score, rank = await _db_call(get_score_and_rank_for_person, target_person)
                        if rank is not None:
                            await self._send_message(
                                message,
                                f"{target_person.name} is ranked #{rank} with {score} point(s)."
                            )
                        else:
                            await self._send_message(
                                message,
                                f"{target_person.name} has been registered!"
                            )
            else:
                _stats["cooldowns"] += 1
                if lowered.startswith("!bamder"):
                    twitch_cooldowns_total.labels("bamder").inc()
                else:
                    twitch_cooldowns_total.labels("simp").inc()
                logger.debug(
                    "cooldown active type=%s actor=%s target=%s channel=%s",
                    event_type,
                    actor_input.username,
                    target_person.name,
                    self._message_channel_name(message),
                )
        except asyncio.TimeoutError:
            twitch_errors_total.labels("process_message_timeout").inc()
            logger.error(
                "Database timeout processing message from %s in channel %s",
                actor_input.username,
                self._message_channel_name(message),
            )
        except Exception:
            # Already logged by _db_call or event_message
            pass

    async def _handle_bot_command(self, message, command: str, args: list[str]) -> None:
        channel = getattr(message, "channel", None)
        if channel is None or not hasattr(channel, "send"):
            channel = None

        try:
            if command == "simpcheck":
                target_username = (
                    normalize_username(args[0]) if args else self._message_channel_name(message)
                )
                score, rank = await _db_call(get_person_score_and_rank, target_username)
                if rank is None:
                    await self._send_message(message, f"{target_username} has no score yet.")
                else:
                    await self._send_message(
                        message,
                        f"{target_username} is ranked #{rank} with {score} point(s)."
                    )

            elif command == "standings":
                limit = 3
                if args:
                    try:
                        limit = max(1, min(int(args[0]), 10))
                    except ValueError:
                        pass
                entries = await _db_call(get_leaderboard_entries)
                top = entries[:limit]
                if not top:
                    await self._send_message(message, "No standings yet!")
                else:
                    parts = [
                        f"#{i + 1} {row['person'].name} ({row['points']} pts)"
                        for i, row in enumerate(top)
                    ]
                    await self._send_message(message, f"Top {len(top)} simps: " + ", ".join(parts))

            elif command == "simp":
                parsed = parse_bot_simp_args(args)
                if parsed is None:
                    bot_name = (self.nick or self._bot_username or "bot").lstrip("@")
                    await self._send_message(
                        message,
                        f"Usage: @{bot_name} simp @username [reason <text>|because <text>]"
                    )
                    return

                target_username, reason = parsed
                actor_input = IdentityInput(
                    platform=Identity.Platform.TWITCH,
                    platform_user_id=self._message_author_id(message),
                    username=self._message_author_name(message),
                    display_name=self._message_display_name(message),
                )
                target_person = await _db_call(get_or_create_twitch_target, target_username)
                event = await _db_call(
                    register_simp,
                    actor=actor_input,
                    target=target_person,
                    platform=Identity.Platform.TWITCH,
                    event_type=str(SimpEvent.EventType.SIMP),
                    source=self._message_channel_name(message),
                    reason=reason,
                    raw_content=(getattr(message, "text", None) or getattr(message, "content", "") or ""),
                    message_id=str(getattr(message, "id", "")),
                    dedupe_key=f"twitch:mention:{getattr(message, 'id', '')}",
                )
                if event:
                    _stats["events_registered"] += 1
                    twitch_events_registered_total.labels("simp").inc()
                    logger.info(
                        "event registered platform=twitch type=simp actor=%s target=%s "
                        "channel=%s event_id=%d points=%d",
                        self._message_author_name(message),
                        target_person.name,
                        self._message_channel_name(message),
                        event.id,
                        event.points,
                    )
                    score, rank = await _db_call(get_score_and_rank_for_person, target_person)
                    if rank is not None:
                        await self._send_message(
                            message,
                            f"{target_person.name} is ranked #{rank} with {score} point(s)."
                        )
                else:
                    _stats["cooldowns"] += 1
                    twitch_cooldowns_total.labels("simp").inc()
                    logger.debug(
                        "cooldown active type=simp actor=%s target=%s channel=%s",
                        self._message_author_name(message),
                        target_person.name,
                        self._message_channel_name(message),
                    )
        except asyncio.TimeoutError:
            twitch_errors_total.labels("bot_command_timeout").inc()
            logger.error("Database timeout in bot command: %s", command)


if __name__ == "__main__":
    logging.basicConfig(
        level=os.getenv("LOG_LEVEL", "INFO").upper(),
        stream=sys.stdout,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    if _METRICS_ENABLED:
        start_http_server(_METRICS_PORT)
        logger.info("Twitch metrics exporter listening on port %d", _METRICS_PORT)

    clear_heartbeat()

    required_env = {
        "TWITCH_CLIENT_ID": os.getenv("TWITCH_CLIENT_ID", "").strip(),
        "TWITCH_CLIENT_SECRET": os.getenv("TWITCH_CLIENT_SECRET", "").strip(),
        "TWITCH_BOT_ID": os.getenv("TWITCH_BOT_ID", "").strip(),
        "TWITCH_BOT_ACCESS_TOKEN": os.getenv("TWITCH_BOT_ACCESS_TOKEN", "").strip(),
        "TWITCH_BOT_REFRESH_TOKEN": os.getenv("TWITCH_BOT_REFRESH_TOKEN", "").strip(),
        "TWITCH_CHANNELS": os.getenv("TWITCH_CHANNELS", "").strip(),
    }
    missing = [name for name, value in required_env.items() if not value]
    if missing:
        logger.error(
            "Twitch bot disabled: missing required EventSub configuration: %s",
            ", ".join(missing),
        )
        raise SystemExit(1)

    while True:
        try:
            async def runner() -> None:
                async with TwitchSimpBot() as bot:
                    await bot.start(load_tokens=False, save_tokens=False)

            asyncio.run(runner())
        except Exception as exc:
            clear_heartbeat()
            logger.exception("Twitch bot crashed: %s", exc)
            time.sleep(5)
