from django.test import SimpleTestCase

from simpwatch.command_parsing import (
    parse_bot_ban_args,
    parse_bot_simp_args,
    parse_bot_mention_command,
    parse_twitch_ban_args,
    parse_twitch_bamder_reason,
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

    def test_parse_bamder_reason_without_reason_keyword(self):
        self.assertEqual(parse_twitch_bamder_reason("!bamder"), "")

    def test_parse_bamder_reason_without_keyword_free_text(self):
        self.assertEqual(
            parse_twitch_bamder_reason("!bamder bad bean"),
            "bad bean",
        )

    def test_parse_bamder_reason_with_reason_text(self):
        self.assertEqual(
            parse_twitch_bamder_reason("!bamder reason extra down bad today"),
            "extra down bad today",
        )

    def test_parse_bamder_reason_keyword_without_text(self):
        self.assertEqual(parse_twitch_bamder_reason("!bamder reason"), "")

    def test_parse_ban_args_requires_target(self):
        self.assertIsNone(parse_twitch_ban_args("!ban"))

    def test_parse_ban_args_with_target(self):
        self.assertEqual(parse_twitch_ban_args("!ban @SomeUser"), ("someuser", ""))

    def test_parse_ban_args_with_reason_keyword(self):
        self.assertEqual(
            parse_twitch_ban_args("!ban @SomeUser reason acted up again"),
            ("someuser", "acted up again"),
        )

    def test_parse_ban_args_with_because_keyword(self):
        self.assertEqual(
            parse_twitch_ban_args("!ban @SomeUser because acted up again"),
            ("someuser", "acted up again"),
        )

    def test_parse_ban_args_ignores_non_reason_tail(self):
        self.assertEqual(
            parse_twitch_ban_args("!ban @SomeUser definitely suspicious"),
            ("someuser", "definitely suspicious"),
        )

    def test_parse_ban_args_rejects_missing_mention(self):
        self.assertIsNone(parse_twitch_ban_args("!ban SomeUser"))


class BotMentionCommandParsingTests(SimpleTestCase):
    def test_parse_bot_ban_args_requires_target(self):
        self.assertIsNone(parse_bot_ban_args([]))

    def test_parse_bot_ban_args_with_target(self):
        self.assertEqual(parse_bot_ban_args(["@SomeUser"]), ("someuser", ""))

    def test_parse_bot_ban_args_with_reason(self):
        self.assertEqual(
            parse_bot_ban_args(["@SomeUser", "reason", "acted", "up"]),
            ("someuser", "acted up"),
        )

    def test_parse_bot_ban_args_with_free_text_reason(self):
        self.assertEqual(
            parse_bot_ban_args(["@SomeUser", "acted", "up"]),
            ("someuser", "acted up"),
        )

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


class BotMentionSimpArgParsingTests(SimpleTestCase):
    def test_requires_target_mention(self):
        self.assertIsNone(parse_bot_simp_args([]))
        self.assertIsNone(parse_bot_simp_args(["riikarii"]))

    def test_rejects_empty_mention_target(self):
        self.assertIsNone(parse_bot_simp_args(["@"]))

    def test_target_only(self):
        self.assertEqual(parse_bot_simp_args(["@Riikarii"]), ("riikarii", ""))

    def test_target_with_reason_keyword(self):
        self.assertEqual(
            parse_bot_simp_args(["@riikarii", "reason", "gifted", "10", "subs"]),
            ("riikarii", "gifted 10 subs"),
        )

    def test_target_with_because_keyword(self):
        self.assertEqual(
            parse_bot_simp_args(["@riikarii", "because", "sent", "another", "dono"]),
            ("riikarii", "sent another dono"),
        )

    def test_non_keyword_tail_does_not_become_reason(self):
        self.assertEqual(
            parse_bot_simp_args(["@riikarii", "absolutely", "down", "bad"]),
            ("riikarii", ""),
        )

    def test_keyword_without_text_returns_empty_reason(self):
        self.assertEqual(parse_bot_simp_args(["@riikarii", "reason"]), ("riikarii", ""))
        self.assertEqual(
            parse_bot_simp_args(["@riikarii", "because"]),
            ("riikarii", ""),
        )
