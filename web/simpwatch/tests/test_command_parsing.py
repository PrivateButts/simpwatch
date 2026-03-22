from django.test import SimpleTestCase

from simpwatch.command_parsing import parse_twitch_reason, parse_twitch_target


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
