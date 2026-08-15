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

from django.db.utils import OperationalError
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

from services.twitch_bot.main import TwitchSimpBot, _db_call, _stats
import services.twitch_bot.main as twitch_main
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
    }
    with patch.dict(os.environ, env, clear=False), \
         patch("simpwatch.twitch_channels.get_monitored_channels", return_value=["streamerchan"]), \
         patch("simpwatch.twitch_channels.get_reply_channels", return_value=set()):
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


class BotInitAsyncSafeTests(SimpleTestCase):
    """Ensure TwitchSimpBot.__init__ does not make ORM calls (async-safe)."""

    async def test_init_inside_event_loop_does_not_raise(self):
        """Constructing the bot inside asyncio must not trigger SynchronousOnlyOperation."""
        env = {
            "TWITCH_BOT_USERNAME": "simpbot",
            "TWITCH_CLIENT_ID": "test-client-id",
            "TWITCH_CLIENT_SECRET": "test-client-secret",
            "TWITCH_BOT_ID": "test-bot-id",
            "TWITCH_BOT_ACCESS_TOKEN": "test-access-token",
            "TWITCH_BOT_REFRESH_TOKEN": "test-refresh-token",
        }
        with patch.dict(os.environ, env, clear=False), \
             patch("simpwatch.twitch_channels.get_monitored_channels", return_value=["streamerchan"]), \
             patch("simpwatch.twitch_channels.get_reply_channels", return_value=set()):
            # If __init__ still calls ORM code this will raise SynchronousOnlyOperation
            bot = TwitchSimpBot()
        self.assertEqual(bot._bot_username, "simpbot")
        self.assertFalse(bot._db_grant_loaded)

    async def test_setup_hook_loads_db_grant_via_thread(self):
        """setup_hook fetches DB grant using asyncio.to_thread, not a direct ORM call."""
        env = {
            "TWITCH_BOT_USERNAME": "",
            "TWITCH_CLIENT_ID": "test-client-id",
            "TWITCH_CLIENT_SECRET": "test-client-secret",
            "TWITCH_BOT_ID": "",
            "TWITCH_BOT_ACCESS_TOKEN": "",
            "TWITCH_BOT_REFRESH_TOKEN": "",
        }
        db_grant = ("simpbot", "bot-user-99", "acc-tok", "ref-tok")
        with patch.dict(os.environ, env, clear=False), \
             patch("simpwatch.twitch_channels.get_monitored_channels", return_value=["streamerchan"]), \
             patch("simpwatch.twitch_channels.get_reply_channels", return_value=set()):
            bot = TwitchSimpBot()

        with patch("services.twitch_bot.main._get_bot_grant_from_db", return_value=db_grant), \
             patch.object(bot, "_validate_bot_token_for_eventsub_async", AsyncMock(), create=True), \
             patch("services.twitch_bot.main._validate_bot_token_for_eventsub", return_value=None), \
             patch.object(bot, "add_token", AsyncMock()), \
             patch.object(bot, "_subscribe_to_channels", AsyncMock()):
            await bot.setup_hook()

        self.assertTrue(bot._db_grant_loaded)
        self.assertEqual(bot._bot_username, "simpbot")
        self.assertEqual(bot._bot_id, "bot-user-99")
        self.assertEqual(bot._bot_access_token, "acc-tok")
        self.assertEqual(bot._bot_refresh_token, "ref-tok")


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

    async def test_reply_mention_stripped_before_processing(self):
        """Reply @parentUser prefix is stripped before _process_message receives content."""
        bot = _make_bot()
        reply_user = SimpleNamespace(mention="@targetuser")
        reply = SimpleNamespace(parent_user=reply_user)
        msg = _message(
            "@targetuser Happy Pride", channel="streamerchan"
        )
        msg.text = "@targetuser Happy Pride"
        msg.reply = reply

        with patch.object(
            bot, "_process_message", AsyncMock()
        ) as mock_process:
            await bot.event_message(msg)

        mock_process.assert_awaited_once()
        stripped_content = mock_process.call_args[0][1]
        self.assertEqual(stripped_content, "Happy Pride")

    async def test_happy_pride_easter_egg_fires_on_reply_message(self):
        """Easter egg triggers on reply messages after @parentUser prefix is stripped."""
        bot = _make_bot(reply_channels={"streamerchan"})
        reply_user = SimpleNamespace(mention="@targetuser")
        reply = SimpleNamespace(parent_user=reply_user)
        msg = _message(
            "@targetuser Happy Pride", channel="streamerchan"
        )
        msg.text = "@targetuser Happy Pride"
        msg.reply = reply

        await bot.event_message(msg)

        msg.channel.send.assert_awaited_once()
        sent_text = msg.channel.send.call_args[0][0]
        self.assertIn("HAPPY PRIDE", sent_text)
        self.assertEqual(_stats["commands_seen"], 0)


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

    async def test_simpcount_command_is_ignored(self):
        """!simpcount should not trigger the simp handler (word boundary check)."""
        bot = _make_bot()
        msg = _message("!simpcount")
        with patch("services.twitch_bot.main.register_simp") as mock_register:
            await bot._process_message(msg, msg.content)
        mock_register.assert_not_called()
        self.assertEqual(_stats["commands_seen"], 0)

    async def test_deathcheckcount_command_is_ignored(self):
        """!deathcheckcount should not trigger the deathcheck handler."""
        bot = _make_bot()
        msg = _message("!deathcheckcount")
        with patch(
            "services.twitch_bot.main.get_death_count_for_person_in_game"
        ) as mock_count:
            await bot._process_message(msg, msg.content)
        mock_count.assert_not_called()
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


class ProcessMessageBanTests(SimpleTestCase):
    """Tests for the !ban path inside _process_message."""

    def setUp(self) -> None:
        for key in _stats:
            _stats[key] = 0

    async def test_ban_registers_event_against_named_target(self):
        bot = _make_bot()
        msg = _message("!ban @prvbutts", channel="streamerchan")
        fake_target = SimpleNamespace(name="prvbutts")
        fake_event = SimpleNamespace(id=8, points=1)

        with (
            patch(
                "services.twitch_bot.main.get_or_create_twitch_target",
                return_value=fake_target,
            ) as mock_target,
            patch("services.twitch_bot.main.register_simp", return_value=fake_event),
        ):
            await bot._process_message(msg, msg.content)

        mock_target.assert_called_once_with("prvbutts")
        self.assertEqual(_stats["commands_seen"], 1)
        self.assertEqual(_stats["events_registered"], 1)

    async def test_ban_in_reply_channel_sends_count_message(self):
        bot = _make_bot(reply_channels={"streamerchan"})
        msg = _message("!ban @prvbutts", channel="streamerchan")
        fake_target = SimpleNamespace(name="prvbutts")
        fake_event = SimpleNamespace(id=8, points=1)

        with (
            patch(
                "services.twitch_bot.main.get_or_create_twitch_target",
                return_value=fake_target,
            ),
            patch("services.twitch_bot.main.register_simp", return_value=fake_event),
            patch(
                "services.twitch_bot.main.get_banthem_counts", return_value=(2, 4, 7)
            ),
        ):
            await bot._process_message(msg, msg.content)

        msg.channel.send.assert_awaited_once()
        sent: str = msg.channel.send.call_args[0][0]
        self.assertIn("prvbutts", sent)
        self.assertNotIn("@prvbutts", sent)
        self.assertIn("7 times", sent)
        self.assertNotIn("today", sent)
        self.assertNotIn("this week", sent)

    async def test_ban_free_text_reason_is_passed_to_register(self):
        bot = _make_bot()
        msg = _message("!ban @prvbutts acted up again", channel="streamerchan")
        fake_target = SimpleNamespace(name="prvbutts")
        fake_event = SimpleNamespace(id=8, points=1)

        with (
            patch(
                "services.twitch_bot.main.get_or_create_twitch_target",
                return_value=fake_target,
            ),
            patch("services.twitch_bot.main.register_simp", return_value=fake_event) as mock_register,
        ):
            await bot._process_message(msg, msg.content)

        self.assertEqual(mock_register.call_args.kwargs["reason"], "acted up again")

    async def test_ban_missing_target_sends_usage(self):
        bot = _make_bot()
        msg = _message("!ban", channel="streamerchan")

        with patch("services.twitch_bot.main.register_simp") as mock_register:
            await bot._process_message(msg, msg.content)

        mock_register.assert_not_called()
        msg.channel.send.assert_awaited_once()
        sent: str = msg.channel.send.call_args[0][0]
        self.assertIn("Usage: !ban @username", sent)


class BotMentionBanCommandTests(SimpleTestCase):
    def setUp(self) -> None:
        for key in _stats:
            _stats[key] = 0

    async def test_bot_ban_command_registers_banthem(self):
        bot = _make_bot(nick="simpbot")
        msg = _message("@simpbot ban @prvbutts", channel="streamerchan")
        fake_target = SimpleNamespace(name="prvbutts")
        fake_event = SimpleNamespace(id=18, points=1)

        with (
            patch(
                "services.twitch_bot.main.get_or_create_twitch_target",
                return_value=fake_target,
            ) as mock_target,
            patch("services.twitch_bot.main.register_simp", return_value=fake_event),
            patch(
                "services.twitch_bot.main.get_banthem_counts", return_value=(1, 1, 1)
            ),
        ):
            await bot._process_message(msg, msg.content)

        mock_target.assert_called_once_with("prvbutts")
        msg.channel.send.assert_awaited_once()

    async def test_bot_ban_command_with_free_text_reason_is_passed_to_register(self):
        bot = _make_bot(nick="simpbot")
        msg = _message("@simpbot ban @prvbutts acted up again", channel="streamerchan")
        fake_target = SimpleNamespace(name="prvbutts")
        fake_event = SimpleNamespace(id=18, points=1)

        with (
            patch(
                "services.twitch_bot.main.get_or_create_twitch_target",
                return_value=fake_target,
            ),
            patch("services.twitch_bot.main.register_simp", return_value=fake_event) as mock_register,
            patch(
                "services.twitch_bot.main.get_banthem_counts", return_value=(1, 1, 1)
            ),
        ):
            await bot._process_message(msg, msg.content)

        self.assertEqual(mock_register.call_args.kwargs["reason"], "acted up again")

    async def test_bot_ban_command_without_target_shows_usage(self):
        bot = _make_bot(nick="simpbot")
        msg = _message("@simpbot ban", channel="streamerchan")

        with patch("services.twitch_bot.main.register_simp") as mock_register:
            await bot._process_message(msg, msg.content)

        mock_register.assert_not_called()
        msg.channel.send.assert_awaited_once()
        sent: str = msg.channel.send.call_args[0][0]
        self.assertIn("@simpbot ban @username", sent)


class ProcessMessageDeathTests(SimpleTestCase):
    """Tests for !death and !died handling inside _process_message."""

    def setUp(self) -> None:
        for key in _stats:
            _stats[key] = 0

    async def test_death_registers_event_against_broadcaster(self):
        bot = _make_bot()
        msg = _message("!death", channel="streamerchan")
        fake_target = SimpleNamespace(name="streamerchan")
        fake_event = SimpleNamespace(id=10, points=1, game_id="123", game_name="Hades")

        with (
            patch(
                "services.twitch_bot.main.get_or_create_twitch_target",
                return_value=fake_target,
            ) as mock_target,
            patch(
                "services.twitch_bot.main._fetch_twitch_channel_game",
                return_value=("123", "Hades"),
            ),
            patch("services.twitch_bot.main.register_simp", return_value=fake_event) as mock_register,
        ):
            await bot._process_message(msg, msg.content)

        mock_target.assert_called_once_with("streamerchan")
        self.assertEqual(_stats["commands_seen"], 1)
        self.assertEqual(_stats["events_registered"], 1)
        self.assertEqual(_stats["cooldowns"], 0)
        self.assertEqual(
            mock_register.call_args.kwargs["event_type"],
            "death",
        )
        self.assertEqual(mock_register.call_args.kwargs["game_id"], "123")
        self.assertEqual(mock_register.call_args.kwargs["game_name"], "Hades")

    async def test_died_alias_registers_death(self):
        bot = _make_bot()
        msg = _message("!died", channel="streamerchan")
        fake_target = SimpleNamespace(name="streamerchan")
        fake_event = SimpleNamespace(id=11, points=1, game_id="456", game_name="Celeste")

        with (
            patch(
                "services.twitch_bot.main.get_or_create_twitch_target",
                return_value=fake_target,
            ),
            patch(
                "services.twitch_bot.main._fetch_twitch_channel_game",
                return_value=("456", "Celeste"),
            ),
            patch("services.twitch_bot.main.register_simp", return_value=fake_event),
        ):
            await bot._process_message(msg, msg.content)

        self.assertEqual(_stats["commands_seen"], 1)
        self.assertEqual(_stats["events_registered"], 1)

    async def test_death_game_lookup_failure_uses_empty_game_name(self):
        bot = _make_bot()
        msg = _message("!death", channel="streamerchan")
        fake_target = SimpleNamespace(name="streamerchan")
        fake_event = SimpleNamespace(id=12, points=1, game_id="", game_name="")

        with (
            patch(
                "services.twitch_bot.main.get_or_create_twitch_target",
                return_value=fake_target,
            ),
            patch(
                "services.twitch_bot.main._fetch_twitch_channel_game",
                return_value=("", ""),
            ),
            patch("services.twitch_bot.main.register_simp", return_value=fake_event) as mock_register,
        ):
            await bot._process_message(msg, msg.content)

        self.assertEqual(mock_register.call_args.kwargs["game_id"], "")
        self.assertEqual(mock_register.call_args.kwargs["game_name"], "")

    async def test_death_in_reply_channel_sends_confirmation(self):
        bot = _make_bot(reply_channels={"streamerchan"})
        msg = _message("!death", channel="streamerchan")
        fake_target = SimpleNamespace(name="streamerchan")
        fake_event = SimpleNamespace(id=13, points=1, game_id="789", game_name="Balatro")

        with (
            patch(
                "services.twitch_bot.main.get_or_create_twitch_target",
                return_value=fake_target,
            ),
            patch(
                "services.twitch_bot.main._fetch_twitch_channel_game",
                return_value=("789", "Balatro"),
            ),
            patch(
                "services.twitch_bot.main.get_death_count_for_person_in_game",
                return_value=1,
            ),
            patch("services.twitch_bot.main.register_simp", return_value=fake_event),
        ):
            await bot._process_message(msg, msg.content)

        msg.channel.send.assert_awaited_once()
        sent: str = msg.channel.send.call_args[0][0]
        self.assertIn("Death logged", sent)
        self.assertIn("Balatro", sent)
        self.assertIn("1 death in Balatro", sent)


class ProcessMessageDeathCheckTests(SimpleTestCase):
    """Tests for the !deathcheck command inside _process_message."""

    def setUp(self) -> None:
        for key in _stats:
            _stats[key] = 0

    async def test_deathcheck_reports_count_for_current_game(self):
        bot = _make_bot()
        msg = _message("!deathcheck", channel="streamerchan")

        with (
            patch(
                "services.twitch_bot.main.get_or_create_twitch_target",
                return_value=SimpleNamespace(name="streamerchan"),
            ),
            patch(
                "services.twitch_bot.main._fetch_twitch_channel_game",
                return_value=("123", "Hades"),
            ),
            patch(
                "services.twitch_bot.main.get_death_count_for_person_in_game",
                return_value=3,
            ) as mock_count,
        ):
            await bot._process_message(msg, msg.content)

        mock_count.assert_called_once()
        msg.channel.send.assert_awaited_once()
        sent: str = msg.channel.send.call_args[0][0]
        self.assertIn("has died 3 times while playing Hades", sent)
        self.assertEqual(_stats["commands_seen"], 1)

    async def test_deathcheck_handles_unknown_current_game(self):
        bot = _make_bot()
        msg = _message("!deathcheck", channel="streamerchan")

        with (
            patch(
                "services.twitch_bot.main.get_or_create_twitch_target",
                return_value=SimpleNamespace(name="streamerchan"),
            ),
            patch(
                "services.twitch_bot.main._fetch_twitch_channel_game",
                return_value=("", ""),
            ),
            patch(
                "services.twitch_bot.main.get_death_count_for_person_in_game"
            ) as mock_count,
        ):
            await bot._process_message(msg, msg.content)

        mock_count.assert_not_called()
        msg.channel.send.assert_awaited_once()
        sent: str = msg.channel.send.call_args[0][0]
        self.assertIn("can't tell what game", sent)


class ProcessMessageCriminalTests(SimpleTestCase):
    """Tests for the !criminal command inside _process_message."""

    def setUp(self) -> None:
        for key in _stats:
            _stats[key] = 0

    async def test_criminal_requires_reason(self):
        bot = _make_bot()
        msg = _message("!criminal", channel="streamerchan")

        with patch("services.twitch_bot.main.register_simp") as mock_register:
            await bot._process_message(msg, msg.content)

        mock_register.assert_not_called()
        msg.channel.send.assert_awaited_once()
        sent: str = msg.channel.send.call_args[0][0]
        self.assertIn("Usage", sent)
        self.assertIn("reason is required", sent)

    async def test_criminal_registers_event_against_broadcaster(self):
        bot = _make_bot()
        msg = _message("!criminal stole a cookie", channel="streamerchan")
        fake_target = SimpleNamespace(name="streamerchan")
        fake_event = SimpleNamespace(
            id=10, points=1, game_id="123", game_name="GTA V",
        )

        with (
            patch(
                "services.twitch_bot.main.get_or_create_twitch_target",
                return_value=fake_target,
            ),
            patch(
                "services.twitch_bot.main._fetch_twitch_channel_game",
                return_value=("123", "GTA V"),
            ),
            patch("services.twitch_bot.main.register_simp", return_value=fake_event),
        ):
            await bot._process_message(msg, msg.content)

        self.assertEqual(_stats["events_registered"], 1)

    async def test_criminal_fetches_game_info(self):
        bot = _make_bot()
        msg = _message("!criminal speeding", channel="streamerchan")
        fake_target = SimpleNamespace(name="streamerchan")
        fake_event = SimpleNamespace(
            id=11, points=1, game_id="999", game_name="Cyberpunk",
        )

        with (
            patch(
                "services.twitch_bot.main.get_or_create_twitch_target",
                return_value=fake_target,
            ),
            patch(
                "services.twitch_bot.main._fetch_twitch_channel_game",
                return_value=("999", "Cyberpunk"),
            ),
            patch("services.twitch_bot.main.register_simp", return_value=fake_event) as mock_register,
        ):
            await bot._process_message(msg, msg.content)

        mock_register.assert_called_once()
        _, kwargs = mock_register.call_args
        self.assertEqual(kwargs["game_id"], "999")
        self.assertEqual(kwargs["game_name"], "Cyberpunk")

    async def test_criminal_registers_with_event_type_criminal(self):
        bot = _make_bot()
        msg = _message("!criminal reason hacking", channel="streamerchan")
        fake_target = SimpleNamespace(name="streamerchan")
        fake_event = SimpleNamespace(
            id=12, points=1, game_id="", game_name="",
        )

        with (
            patch(
                "services.twitch_bot.main.get_or_create_twitch_target",
                return_value=fake_target,
            ),
            patch(
                "services.twitch_bot.main._fetch_twitch_channel_game",
                return_value=("", ""),
            ),
            patch("services.twitch_bot.main.register_simp", return_value=fake_event) as mock_register,
        ):
            await bot._process_message(msg, msg.content)

        mock_register.assert_called_once()
        _, kwargs = mock_register.call_args
        self.assertEqual(kwargs["event_type"], "criminal")
        self.assertEqual(kwargs["reason"], "hacking")

    async def test_criminal_in_reply_channel_sends_count(self):
        bot = _make_bot(reply_channels={"streamerchan"})
        msg = _message("!criminal tax evasion", channel="streamerchan")
        fake_target = SimpleNamespace(name="streamerchan")
        fake_event = SimpleNamespace(
            id=13, points=1, game_id="123", game_name="GTA V", reason="tax evasion",
        )

        with (
            patch(
                "services.twitch_bot.main.get_or_create_twitch_target",
                return_value=fake_target,
            ),
            patch(
                "services.twitch_bot.main._fetch_twitch_channel_game",
                return_value=("123", "GTA V"),
            ),
            patch("services.twitch_bot.main.get_crime_count_for_person_in_game", return_value=1),
            patch("services.twitch_bot.main.register_simp", return_value=fake_event),
        ):
            await bot._process_message(msg, msg.content)

        msg.channel.send.assert_awaited_once()
        sent: str = msg.channel.send.call_args[0][0]
        self.assertIn("Call the Sherriff!", sent)
        self.assertIn("has commited a crime", sent)
        self.assertIn("locked up 1 times", sent)
        self.assertIn("WANTED for tax evasion during GTA V", sent)


class ProcessMessageCriminalCheckTests(SimpleTestCase):
    """Tests for the !backgroundcheck command inside _process_message."""

    def setUp(self) -> None:
        for key in _stats:
            _stats[key] = 0

    async def test_backgroundcheck_reports_count_for_current_game(self):
        bot = _make_bot()
        msg = _message("!backgroundcheck", channel="streamerchan")

        with (
            patch(
                "services.twitch_bot.main.get_or_create_twitch_target",
                return_value=SimpleNamespace(name="streamerchan"),
            ),
            patch(
                "services.twitch_bot.main._fetch_twitch_channel_game",
                return_value=("123", "GTA V"),
            ),
            patch(
                "services.twitch_bot.main.get_crime_count_for_person_in_game",
                return_value=3,
            ) as mock_count,
        ):
            await bot._process_message(msg, msg.content)

        mock_count.assert_called_once()
        msg.channel.send.assert_awaited_once()
        sent: str = msg.channel.send.call_args[0][0]
        self.assertIn("STREAMERCHAN has committed 3 crimes while playing GTA V", sent)
        self.assertEqual(_stats["commands_seen"], 1)

    async def test_backgroundcheck_with_user_arg_reports_for_that_user(self):
        bot = _make_bot()
        msg = _message("!backgroundcheck @someuser", channel="streamerchan")

        with (
            patch(
                "services.twitch_bot.main.get_or_create_twitch_target",
                return_value=SimpleNamespace(name="someuser"),
            ) as mock_target,
            patch(
                "services.twitch_bot.main._fetch_twitch_channel_game",
                return_value=("123", "GTA V"),
            ),
            patch(
                "services.twitch_bot.main.get_crime_count_for_person_in_game",
                return_value=5,
            ) as mock_count,
        ):
            await bot._process_message(msg, msg.content)

        mock_target.assert_called_once_with("someuser")
        mock_count.assert_called_once()
        msg.channel.send.assert_awaited_once()
        sent: str = msg.channel.send.call_args[0][0]
        self.assertIn("SOMEUSER has committed 5 crimes while playing GTA V", sent)

    async def test_backgroundcheck_with_user_arg_handles_unknown_game(self):
        bot = _make_bot()
        msg = _message("!backgroundcheck @someuser", channel="streamerchan")

        with (
            patch(
                "services.twitch_bot.main.get_or_create_twitch_target",
                return_value=SimpleNamespace(name="someuser"),
            ),
            patch(
                "services.twitch_bot.main._fetch_twitch_channel_game",
                return_value=("", ""),
            ),
            patch(
                "services.twitch_bot.main.get_crime_count_for_person_in_game"
            ) as mock_count,
        ):
            await bot._process_message(msg, msg.content)

        mock_count.assert_not_called()
        msg.channel.send.assert_awaited_once()
        sent: str = msg.channel.send.call_args[0][0]
        self.assertIn("can't tell what game", sent)

    async def test_backgroundcheck_handles_unknown_current_game(self):
        bot = _make_bot()
        msg = _message("!backgroundcheck", channel="streamerchan")

        with (
            patch(
                "services.twitch_bot.main.get_or_create_twitch_target",
                return_value=SimpleNamespace(name="streamerchan"),
            ),
            patch(
                "services.twitch_bot.main._fetch_twitch_channel_game",
                return_value=("", ""),
            ),
            patch(
                "services.twitch_bot.main.get_crime_count_for_person_in_game"
            ) as mock_count,
        ):
            await bot._process_message(msg, msg.content)

        mock_count.assert_not_called()
        msg.channel.send.assert_awaited_once()
        sent: str = msg.channel.send.call_args[0][0]
        self.assertIn("can't tell what game", sent)


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
    async def test_eventsub_send_falls_back_to_channel_send_when_grant_missing(self):
        bot = _make_bot(reply_channels={"streamerchan"})
        msg = _message("!simp", channel="streamerchan")
        msg.respond = AsyncMock()

        with patch.object(bot, "_is_reply_authorized_for_channel", AsyncMock(return_value=False)):
            await bot._send_message(msg, "hello")

        msg.respond.assert_not_awaited()
        msg.channel.send.assert_awaited_once_with("hello")

    async def test_eventsub_send_uses_respond_when_grant_present(self):
        bot = _make_bot(reply_channels={"streamerchan"})
        msg = _message("!simp", channel="streamerchan")
        msg.respond = AsyncMock()

        with patch.object(bot, "_is_reply_authorized_for_channel", AsyncMock(return_value=True)):
            await bot._send_message(msg, "hello")

        msg.respond.assert_awaited_once()

    async def test_grant_cache_negative_result_expires_quickly(self):
        bot = _make_bot()
        db_mock = AsyncMock(return_value=False)

        with patch.object(twitch_main.time, "monotonic", return_value=100.0), \
             patch.object(twitch_main, "_db_call", new=db_mock):
            self.assertFalse(await bot._is_reply_authorized_for_channel("streamerchan"))
            self.assertEqual(db_mock.call_count, 1)

        with patch.object(twitch_main.time, "monotonic", return_value=103.0), \
             patch.object(twitch_main, "_db_call", new=db_mock):
            self.assertFalse(await bot._is_reply_authorized_for_channel("streamerchan"))
            self.assertEqual(db_mock.call_count, 1)

        with patch.object(twitch_main.time, "monotonic", return_value=110.0), \
             patch.object(twitch_main, "_db_call", new=db_mock):
            self.assertFalse(await bot._is_reply_authorized_for_channel("streamerchan"))
            self.assertEqual(db_mock.call_count, 2)

    async def test_grant_cache_positive_result_cached_longer(self):
        bot = _make_bot()
        db_mock = AsyncMock(return_value=True)

        with patch.object(twitch_main.time, "monotonic", return_value=100.0), \
             patch.object(twitch_main, "_db_call", new=db_mock):
            self.assertTrue(await bot._is_reply_authorized_for_channel("streamerchan"))
            self.assertEqual(db_mock.call_count, 1)

        with patch.object(twitch_main.time, "monotonic", return_value=120.0), \
             patch.object(twitch_main, "_db_call", new=db_mock):
            self.assertTrue(await bot._is_reply_authorized_for_channel("streamerchan"))
            self.assertEqual(db_mock.call_count, 1)

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

    async def test_death_command_increments_command_metric(self):
        bot = _make_bot()
        msg = _message("!death", channel="streamerchan")
        fake_target = SimpleNamespace(name="streamerchan")
        fake_event = SimpleNamespace(id=2, points=1, game_id="123", game_name="Hades")
        before = _counter_value(
            "simpwatch_twitch_commands_total",
            labels={"command": "death"},
        )

        with (
            patch(
                "services.twitch_bot.main.get_or_create_twitch_target",
                return_value=fake_target,
            ),
            patch(
                "services.twitch_bot.main._fetch_twitch_channel_game",
                return_value=("123", "Hades"),
            ),
            patch("services.twitch_bot.main.register_simp", return_value=fake_event),
        ):
            await bot._process_message(msg, msg.content)

        after = _counter_value(
            "simpwatch_twitch_commands_total",
            labels={"command": "death"},
        )
        self.assertEqual(after - before, 1.0)

    async def test_criminal_command_increments_command_metric(self):
        bot = _make_bot()
        msg = _message("!criminal stole a cookie", channel="streamerchan")
        fake_target = SimpleNamespace(name="streamerchan")
        fake_event = SimpleNamespace(
            id=3, points=1, game_id="123", game_name="GTA V",
        )
        before = _counter_value(
            "simpwatch_twitch_commands_total",
            labels={"command": "criminal"},
        )

        with (
            patch(
                "services.twitch_bot.main.get_or_create_twitch_target",
                return_value=fake_target,
            ),
            patch(
                "services.twitch_bot.main._fetch_twitch_channel_game",
                return_value=("123", "GTA V"),
            ),
            patch("services.twitch_bot.main.register_simp", return_value=fake_event),
        ):
            await bot._process_message(msg, msg.content)

        after = _counter_value(
            "simpwatch_twitch_commands_total",
            labels={"command": "criminal"},
        )
        self.assertEqual(after - before, 1.0)


class DatabaseCallTests(SimpleTestCase):
    def setUp(self) -> None:
        twitch_main._db_consecutive_failures = 0

    async def test_db_call_retries_transient_operational_error_once(self):
        attempts = 0

        def flaky_call() -> str:
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                raise OperationalError("terminating connection due to administrator command")
            return "ok"

        with patch("services.twitch_bot.main._DB_RETRY_BACKOFF_SECONDS", 0):
            result = await _db_call(flaky_call)

        self.assertEqual(result, "ok")
        self.assertEqual(attempts, 2)

    async def test_db_call_does_not_retry_non_database_exceptions(self):
        attempts = 0

        def broken_call() -> None:
            nonlocal attempts
            attempts += 1
            raise RuntimeError("boom")

        with self.assertRaises(RuntimeError):
            await _db_call(broken_call)

        self.assertEqual(attempts, 1)

    async def test_db_call_uses_backoff_before_retry(self):
        attempts = 0

        def flaky_call() -> str:
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                raise OperationalError("terminating connection due to administrator command")
            return "ok"

        with (
            patch("services.twitch_bot.main._DB_RETRY_BACKOFF_SECONDS", 0.25),
            patch("services.twitch_bot.main.asyncio.sleep", AsyncMock()) as mock_sleep,
        ):
            result = await _db_call(flaky_call)

        self.assertEqual(result, "ok")
        mock_sleep.assert_awaited_once_with(0.25)

    async def test_db_call_exits_after_max_consecutive_failures(self):
        def broken_call() -> None:
            raise OperationalError("db down")

        with (
            patch("services.twitch_bot.main._DB_OPERATION_RETRIES", 0),
            patch("services.twitch_bot.main._DB_MAX_CONSECUTIVE_FAILURES", 2),
            patch("services.twitch_bot.main.clear_heartbeat") as mock_clear,
            patch("services.twitch_bot.main.os._exit", side_effect=SystemExit(1)) as mock_exit,
        ):
            with self.assertRaises(OperationalError):
                await _db_call(broken_call)

            with self.assertRaises(SystemExit):
                await _db_call(broken_call)

        mock_clear.assert_called_once()
        mock_exit.assert_called_once_with(1)
