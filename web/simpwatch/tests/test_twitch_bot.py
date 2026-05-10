from __future__ import annotations

import asyncio
import os
from pathlib import Path
import re
import sys
import time
from types import SimpleNamespace
from unittest.mock import ANY, AsyncMock, MagicMock, patch
import unittest

from django.test import SimpleTestCase

_HAS_SERVICES_PACKAGE = False
for _parent in Path(__file__).resolve().parents:
    if (_parent / "services").is_dir():
        if str(_parent) not in sys.path:
            sys.path.insert(0, str(_parent))
        _HAS_SERVICES_PACKAGE = True
        break

if not _HAS_SERVICES_PACKAGE:
    raise unittest.SkipTest("services package unavailable in this test runtime")

from services.twitch_bot.main import TwitchSimpBot, _stats
from simpwatch.metrics import prometheus_payload


def _counter_value(name: str, labels: dict[str, str] | None = None) -> float:
    payload, _ = prometheus_payload()
    text = payload.decode()
    label_text = ""
    if labels:
        label_text = "{" + ",".join(
            f'{key}="{value}"' for key, value in sorted(labels.items())
        ) + "}"
    pattern = rf"^{re.escape(name)}{re.escape(label_text)}\s+([0-9eE+\-.]+)$"
    match = re.search(pattern, text, flags=re.MULTILINE)
    if not match:
        return 0.0
    return float(match.group(1))


def _make_bot(
    nick: str = "simpbot", reply_channels: set[str] | None = None
) -> TwitchSimpBot:
    env = {
        "TWITCH_BOT_USERNAME": nick,
        "TWITCH_CLIENT_ID": "test-client-id",
        "TWITCH_CLIENT_SECRET": "test-client-secret",
        "TWITCH_BOT_ID": "test-bot-id",
        "TWITCH_BOT_ACCESS_TOKEN": "test-access-token",
        "TWITCH_BOT_REFRESH_TOKEN": "test-refresh-token",
        "TWITCH_CHANNELS": "streamerchan",
    }
    with patch.dict(os.environ, env, clear=False):
        bot = TwitchSimpBot()
    bot.nick = nick
    if reply_channels is not None:
        bot._reply_channels = reply_channels
    return bot


def _message(
    content: str = "!simp",
    channel: str = "streamerchan",
    author_id: str = "author-1",
    author_name: str = "caller",
    display_name: str = "Caller",
    echo: bool = False,
    msg_id: str = "msg-1",
) -> SimpleNamespace:
    return SimpleNamespace(
        echo=echo,
        content=content,
        id=msg_id,
        channel=SimpleNamespace(name=channel, send=AsyncMock()),
        author=SimpleNamespace(
            id=author_id,
            name=author_name,
            display_name=display_name,
        ),
    )


class EventMessageRoutingTests(SimpleTestCase):
    """Tests for event_message — gating, counter updates, error trapping."""

    def setUp(self) -> None:
        for key in _stats:
            _stats[key] = 0

    async def test_echo_message_is_ignored(self):
        bot = _make_bot()
        msg = _message(echo=True)
        before = time.monotonic()
        with patch.object(bot, "_process_message", AsyncMock()) as mock_process:
            await bot.event_message(msg)
        mock_process.assert_not_awaited()
        self.assertEqual(_stats["messages_seen"], 0)
        # _last_message_at must not have advanced past `before`
        self.assertLessEqual(bot._last_message_at, before)

    async def test_non_echo_message_increments_messages_seen(self):
        bot = _make_bot()
        msg = _message()
        with patch.object(bot, "_process_message", AsyncMock()):
            await bot.event_message(msg)
        self.assertEqual(_stats["messages_seen"], 1)

    async def test_non_echo_message_updates_last_message_at(self):
        bot = _make_bot()
        bot._last_message_at = 0.0
        msg = _message()
        with patch.object(bot, "_process_message", AsyncMock()):
            await bot.event_message(msg)
        self.assertGreater(bot._last_message_at, 0.0)

    async def test_processing_exception_increments_errors_and_does_not_propagate(self):
        bot = _make_bot()
        msg = _message()
        with patch.object(
            bot, "_process_message", AsyncMock(side_effect=RuntimeError("boom"))
        ):
            await bot.event_message(msg)
        self.assertEqual(_stats["messages_seen"], 1)
        self.assertEqual(_stats["errors"], 1)


class ProcessMessageSimpTests(SimpleTestCase):
    """Tests for the !simp path inside _process_message."""

    def setUp(self) -> None:
        for key in _stats:
            _stats[key] = 0

    async def test_plain_simp_registers_event_against_broadcaster(self):
        bot = _make_bot()
        msg = _message("!simp", channel="streamerchan")
        fake_target = SimpleNamespace(name="streamerchan")
        fake_event = SimpleNamespace(id=42, points=1)

        with (
            patch(
                "services.twitch_bot.main.get_or_create_twitch_target",
                return_value=fake_target,
            ) as mock_target,
            patch("services.twitch_bot.main.register_simp", return_value=fake_event),
        ):
            await bot._process_message(msg, msg.content)

        mock_target.assert_called_once_with("streamerchan")
        self.assertEqual(_stats["commands_seen"], 1)
        self.assertEqual(_stats["events_registered"], 1)
        self.assertEqual(_stats["cooldowns"], 0)

    async def test_simp_at_mention_sends_redirect_hint(self):
        """!simp @username (not a bot-mention) should redirect the user, not register an event."""
        bot = _make_bot(nick="simpbot")
        msg = _message("!simp @riikarii", channel="streamerchan")

        with patch("services.twitch_bot.main.register_simp") as mock_register:
            await bot._process_message(msg, msg.content)

        mock_register.assert_not_called()
        msg.channel.send.assert_awaited_once()
        sent_text: str = msg.channel.send.call_args[0][0]
        self.assertIn("simpbot", sent_text)

    async def test_simp_on_cooldown_increments_cooldown_stat(self):
        bot = _make_bot()
        msg = _message("!simp")
        fake_target = SimpleNamespace(name="streamerchan")

        with (
            patch(
                "services.twitch_bot.main.get_or_create_twitch_target",
                return_value=fake_target,
            ),
            patch("services.twitch_bot.main.register_simp", return_value=None),
        ):
            await bot._process_message(msg, msg.content)

        self.assertEqual(_stats["cooldowns"], 1)
        self.assertEqual(_stats["events_registered"], 0)

    async def test_simp_in_reply_channel_sends_rank_response(self):
        bot = _make_bot(reply_channels={"streamerchan"})
        msg = _message("!simp", channel="streamerchan")
        fake_target = SimpleNamespace(name="streamerchan")
        fake_event = SimpleNamespace(id=1, points=1)

        with (
            patch(
                "services.twitch_bot.main.get_or_create_twitch_target",
                return_value=fake_target,
            ),
            patch("services.twitch_bot.main.register_simp", return_value=fake_event),
            patch(
                "services.twitch_bot.main.get_score_and_rank_for_person",
                return_value=(5, 2),
            ),
        ):
            await bot._process_message(msg, msg.content)

        msg.channel.send.assert_awaited_once()
        sent: str = msg.channel.send.call_args[0][0]
        self.assertIn("ranked #2", sent)
        self.assertIn("5 point", sent)

    async def test_simp_in_reply_channel_sends_registered_when_unranked(self):
        bot = _make_bot(reply_channels={"streamerchan"})
        msg = _message("!simp", channel="streamerchan")
        fake_target = SimpleNamespace(name="streamerchan")
        fake_event = SimpleNamespace(id=1, points=1)

        with (
            patch(
                "services.twitch_bot.main.get_or_create_twitch_target",
                return_value=fake_target,
            ),
            patch("services.twitch_bot.main.register_simp", return_value=fake_event),
            patch(
                "services.twitch_bot.main.get_score_and_rank_for_person",
                return_value=(0, None),
            ),
        ):
            await bot._process_message(msg, msg.content)

        msg.channel.send.assert_awaited_once()
        sent: str = msg.channel.send.call_args[0][0]
        self.assertIn("registered", sent)

    async def test_simp_not_in_reply_channels_sends_no_chat(self):
        bot = _make_bot(reply_channels=set())
        msg = _message("!simp")
        fake_target = SimpleNamespace(name="streamerchan")
        fake_event = SimpleNamespace(id=1, points=1)

        with (
            patch(
                "services.twitch_bot.main.get_or_create_twitch_target",
                return_value=fake_target,
            ),
            patch("services.twitch_bot.main.register_simp", return_value=fake_event),
        ):
            await bot._process_message(msg, msg.content)

        msg.channel.send.assert_not_awaited()

    async def test_non_command_message_is_ignored(self):
        bot = _make_bot()
        msg = _message("hello world")
        with patch("services.twitch_bot.main.register_simp") as mock_register:
            await bot._process_message(msg, msg.content)
        mock_register.assert_not_called()
        self.assertEqual(_stats["commands_seen"], 0)


class ProcessMessageBamderTests(SimpleTestCase):
    """Tests for the !bamder path inside _process_message."""

    def setUp(self) -> None:
        for key in _stats:
            _stats[key] = 0

    async def test_bamder_registers_event_against_pamder(self):
        bot = _make_bot()
        msg = _message("!bamder", channel="streamerchan")
        fake_target = SimpleNamespace(name="pamder")
        fake_event = SimpleNamespace(id=7, points=1)

        with (
            patch(
                "services.twitch_bot.main.get_or_create_named_person",
                return_value=fake_target,
            ) as mock_person,
            patch("services.twitch_bot.main.register_simp", return_value=fake_event),
        ):
            await bot._process_message(msg, msg.content)

        mock_person.assert_called_once_with("pamder")
        self.assertEqual(_stats["commands_seen"], 1)
        self.assertEqual(_stats["events_registered"], 1)

    async def test_bamder_in_reply_channel_sends_count_message(self):
        bot = _make_bot(reply_channels={"streamerchan"})
        msg = _message("!bamder", channel="streamerchan")
        fake_target = SimpleNamespace(name="pamder")
        fake_event = SimpleNamespace(id=7, points=1)

        with (
            patch(
                "services.twitch_bot.main.get_or_create_named_person",
                return_value=fake_target,
            ),
            patch("services.twitch_bot.main.register_simp", return_value=fake_event),
            patch(
                "services.twitch_bot.main.get_bamder_counts", return_value=(3, 5, 12)
            ),
        ):
            await bot._process_message(msg, msg.content)

        msg.channel.send.assert_awaited_once()
        sent: str = msg.channel.send.call_args[0][0]
        self.assertIn("3rd", sent)  # today
        self.assertIn("5th", sent)  # this week
        self.assertIn("12th", sent)  # total

    async def test_bamder_not_in_reply_channels_sends_no_chat(self):
        bot = _make_bot(reply_channels=set())
        msg = _message("!bamder")
        fake_target = SimpleNamespace(name="pamder")
        fake_event = SimpleNamespace(id=7, points=1)

        with (
            patch(
                "services.twitch_bot.main.get_or_create_named_person",
                return_value=fake_target,
            ),
            patch("services.twitch_bot.main.register_simp", return_value=fake_event),
        ):
            await bot._process_message(msg, msg.content)

        msg.channel.send.assert_not_awaited()


class BotCommandTests(SimpleTestCase):
    """Tests for @bot mention commands routed through _handle_bot_command."""

    def setUp(self) -> None:
        for key in _stats:
            _stats[key] = 0

    async def test_simpcheck_with_no_score_sends_no_score_message(self):
        bot = _make_bot(nick="simpbot")
        msg = _message("@simpbot simpcheck @riikarii")

        with patch(
            "services.twitch_bot.main.get_person_score_and_rank", return_value=(0, None)
        ):
            await bot._process_message(msg, msg.content)

        msg.channel.send.assert_awaited_once()
        self.assertIn("no score", msg.channel.send.call_args[0][0])

    async def test_simpcheck_with_score_sends_rank(self):
        bot = _make_bot(nick="simpbot")
        msg = _message("@simpbot simpcheck @riikarii")

        with patch(
            "services.twitch_bot.main.get_person_score_and_rank", return_value=(10, 1)
        ):
            await bot._process_message(msg, msg.content)

        sent: str = msg.channel.send.call_args[0][0]
        self.assertIn("ranked #1", sent)
        self.assertIn("10 point", sent)

    async def test_standings_empty_sends_no_standings_message(self):
        bot = _make_bot(nick="simpbot")
        msg = _message("@simpbot standings")

        with patch("services.twitch_bot.main.get_leaderboard_entries", return_value=[]):
            await bot._process_message(msg, msg.content)

        self.assertIn("No standings", msg.channel.send.call_args[0][0])

    async def test_standings_returns_top_entries(self):
        bot = _make_bot(nick="simpbot")
        msg = _message("@simpbot standings 2")
        p1 = SimpleNamespace(name="alice")
        p2 = SimpleNamespace(name="bob")
        entries = [{"person": p1, "points": 5}, {"person": p2, "points": 3}]

        with patch(
            "services.twitch_bot.main.get_leaderboard_entries", return_value=entries
        ):
            await bot._process_message(msg, msg.content)

        sent: str = msg.channel.send.call_args[0][0]
        self.assertIn("alice", sent)
        self.assertIn("bob", sent)

    async def test_bot_mention_simp_registers_event(self):
        bot = _make_bot(nick="simpbot")
        msg = _message("@simpbot simp @riikarii")
        fake_target = SimpleNamespace(name="riikarii")
        fake_event = SimpleNamespace(id=99, points=1)

        with (
            patch(
                "services.twitch_bot.main.get_or_create_twitch_target",
                return_value=fake_target,
            ),
            patch("services.twitch_bot.main.register_simp", return_value=fake_event),
            patch(
                "services.twitch_bot.main.get_score_and_rank_for_person",
                return_value=(1, 3),
            ),
        ):
            await bot._process_message(msg, msg.content)

        self.assertEqual(_stats["events_registered"], 1)
        sent: str = msg.channel.send.call_args[0][0]
        self.assertIn("ranked #3", sent)

    async def test_bot_mention_simp_invalid_args_sends_usage(self):
        bot = _make_bot(nick="simpbot")
        msg = _message("@simpbot simp no-at-sign")

        with patch("services.twitch_bot.main.register_simp") as mock_register:
            await bot._process_message(msg, msg.content)

        mock_register.assert_not_called()
        sent: str = msg.channel.send.call_args[0][0]
        self.assertIn("Usage", sent)


class EventErrorTests(SimpleTestCase):
    """Tests for the event_error handler."""

    def setUp(self) -> None:
        for key in _stats:
            _stats[key] = 0

    async def test_event_error_increments_error_counter(self):
        bot = _make_bot()
        await bot.event_error(RuntimeError("network glitch"), data="raw irc line")
        self.assertEqual(_stats["errors"], 1)

    async def test_event_error_handles_none_data(self):
        bot = _make_bot()
        # Should not raise even when data=None
        await bot.event_error(ValueError("oops"), data=None)
        self.assertEqual(_stats["errors"], 1)


class WatchdogTests(SimpleTestCase):
    """Tests for the idle-disconnect watchdog."""

    async def test_watchdog_forces_reconnect_after_idle_threshold(self):
        bot = _make_bot()
        bot.close = AsyncMock()
        bot._last_irc_at = time.monotonic() - 1000

        sleep_calls = 0

        async def limited_sleep(_delay):
            nonlocal sleep_calls
            sleep_calls += 1
            if sleep_calls >= 2:
                raise asyncio.CancelledError

        with patch("services.twitch_bot.main.asyncio.sleep", limited_sleep):
            with self.assertRaises(asyncio.CancelledError):
                await bot._watchdog_loop()

        bot.close.assert_awaited_once()

    async def test_watchdog_does_not_reconnect_when_recently_active(self):
        bot = _make_bot()
        bot.close = AsyncMock()
        bot._last_irc_at = time.monotonic()

        sleep_calls = 0

        async def limited_sleep(_delay):
            nonlocal sleep_calls
            sleep_calls += 1
            # Update IRC timestamp to simulate ongoing keepalive activity,
            # then stop after one cycle.
            bot._last_irc_at = time.monotonic()
            if sleep_calls >= 2:
                raise asyncio.CancelledError

        with patch("services.twitch_bot.main.asyncio.sleep", limited_sleep):
            try:
                await bot._watchdog_loop()
            except asyncio.CancelledError:
                pass

        bot.close.assert_not_awaited()


class PrometheusMetricsTests(SimpleTestCase):
    def setUp(self) -> None:
        for key in _stats:
            _stats[key] = 0

    async def test_event_message_increments_twitch_messages_metric(self):
        bot = _make_bot()
        msg = _message("hello world")
        before = _counter_value("simpwatch_twitch_messages_total")

        await bot.event_message(msg)

        after = _counter_value("simpwatch_twitch_messages_total")
        self.assertEqual(after - before, 1.0)


class ReplyAuthorizationTests(SimpleTestCase):
    async def test_eventsub_send_skips_when_grant_missing(self):
        bot = _make_bot(reply_channels={"streamerchan"})
        msg = _message("!simp", channel="streamerchan")
        msg.respond = AsyncMock()

        with patch.object(bot, "_is_reply_authorized_for_channel", AsyncMock(return_value=False)):
            await bot._send_message(msg, "hello")

        msg.respond.assert_not_awaited()

    async def test_eventsub_send_uses_respond_when_grant_present(self):
        bot = _make_bot(reply_channels={"streamerchan"})
        msg = _message("!simp", channel="streamerchan")
        msg.respond = AsyncMock()

        with patch.object(bot, "_is_reply_authorized_for_channel", AsyncMock(return_value=True)):
            await bot._send_message(msg, "hello")

        msg.respond.assert_awaited_once()

    async def test_simp_command_increments_command_metric(self):
        bot = _make_bot()
        msg = _message("!simp", channel="streamerchan")
        fake_target = SimpleNamespace(name="streamerchan")
        fake_event = SimpleNamespace(id=1, points=1)
        before = _counter_value(
            "simpwatch_twitch_commands_total",
            labels={"command": "simp"},
        )

        with (
            patch(
                "services.twitch_bot.main.get_or_create_twitch_target",
                return_value=fake_target,
            ),
            patch("services.twitch_bot.main.register_simp", return_value=fake_event),
        ):
            await bot._process_message(msg, msg.content)

        after = _counter_value(
            "simpwatch_twitch_commands_total",
            labels={"command": "simp"},
        )
        self.assertEqual(after - before, 1.0)
