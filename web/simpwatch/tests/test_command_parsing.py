from django.test import SimpleTestCase

from simpwatch.command_parsing import (
    parse_bot_mention_command,
    parse_twitch_reason,
    parse_twitch_target,
)

class TwitchCommandParsingTests(SimpleTestCase):
    def test_parse_target_none_for_plain_simp(self):
        self.assertIsNone(parse_twitch_target("!simp"))

    def test_parse_target_exact_username(self):
        self.assertEqual(parse_twitch_target("!simp @SomeUser"), "someuser")

    def test_parse_target_ignores_non_mention_second_token(self):
        self.assertIsNone(parse_twitch_target("!simp reason this is why"))

    def test_parse_target_empty_mention_returns_none(self):
        self.assertIsNone(parse_twitch_target("!simp @"))

    def test_parse_reason_none_for_plain_simp(self):
        self.assertEqual(parse_twitch_reason("!simp"), "")

    def test_parse_reason_with_target_keyword_reason(self):
        self.assertEqual(
            parse_twitch_reason("!simp @riikarii reason gifted 10 subs"),
            "gifted 10 subs",
        )

    def test_parse_reason_without_target_keyword_reason(self):
        self.assertEqual(
            parse_twitch_reason("!simp reason very down bad"),
            "very down bad",
        )

    def test_parse_reason_with_target_keyword_because(self):
        self.assertEqual(
            parse_twitch_reason("!simp @riikarii because sent 20 hearts"),
            "sent 20 hearts",
        )

    def test_parse_reason_without_target_keyword_because(self):
        self.assertEqual(
            parse_twitch_reason("!simp because donated another 50"),
            "donated another 50",
        )

    def test_parse_reason_keyword_without_text_returns_empty(self):
        self.assertEqual(parse_twitch_reason("!simp @riikarii reason"), "")
        self.assertEqual(parse_twitch_reason("!simp because"), "")

    def test_parse_reason_non_keyword_phrase_returns_empty(self):
        self.assertEqual(
            parse_twitch_reason("!simp @riikarii absolutely no chill"),
            "",
        )


class BotMentionCommandParsingTests(SimpleTestCase):
    def test_simpcheck_no_target(self):
        result = parse_bot_mention_command("@mybot simpcheck", "mybot")
        self.assertEqual(result, ("simpcheck", []))

    def test_simpcheck_with_at_target(self):
        result = parse_bot_mention_command("@mybot simpcheck @riikarii", "mybot")
        self.assertEqual(result, ("simpcheck", ["@riikarii"]))

    def test_standings_no_args(self):
        result = parse_bot_mention_command("@mybot standings", "mybot")
        self.assertEqual(result, ("standings", []))

    def test_standings_with_limit(self):
        result = parse_bot_mention_command("@mybot standings 5", "mybot")
        self.assertEqual(result, ("standings", ["5"]))

    def test_non_mention_returns_none(self):
        self.assertIsNone(parse_bot_mention_command("!simp @riikarii", "mybot"))

    def test_different_bot_name_returns_none(self):
        self.assertIsNone(parse_bot_mention_command("@otherbot simpcheck", "mybot"))

    def test_case_insensitive_bot_name(self):
        result = parse_bot_mention_command("@MyBot simpcheck", "mybot")
        self.assertEqual(result, ("simpcheck", []))

    def test_command_lowercased(self):
        result = parse_bot_mention_command("@mybot STANDINGS", "mybot")
        self.assertEqual(result, ("standings", []))

    def test_mention_only_no_command_returns_none(self):
        self.assertIsNone(parse_bot_mention_command("@mybot", "mybot"))

    def test_empty_content_returns_none(self):
        self.assertIsNone(parse_bot_mention_command("", "mybot"))
