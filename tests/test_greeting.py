"""Tests for agent/greeting.py -- the deterministic bare-greeting
detector and the prefetch-context formatter behind the "Say hi"
structural fix (ROADMAP.md's "Say hi" entry).

Pure functions, no mocks: detection is string matching, and the formatter
just wraps two strings.

Run with: python -m unittest tests.test_greeting -v
"""
import unittest

from agent.greeting import (
    format_greeting_context,
    is_bare_greeting,
    weather_result_looks_usable,
)


class TestIsBareGreeting(unittest.TestCase):

    def test_plain_greetings_match(self):
        for text in [
            "hi", "Hi", "  hi  ", "hi!", "hi.", "hi?", "hello", "Hello!!",
            "hey", "heyyy", "hey there", "yo", "howdy", "sup", "what's up",
            "whats up", "good morning", "Good Morning", "morning", "gm",
        ]:
            with self.subTest(text=text):
                self.assertTrue(is_bare_greeting(text))

    def test_wake_ups_and_arrivals_match(self):
        for text in [
            "wake up", "wakey wakey", "you up", "you there", "you awake",
            "are you there", "daddy's home", "daddys home", "dad's home",
            "dads home", "i'm home", "im home", "i'm back", "knock knock",
        ]:
            with self.subTest(text=text):
                self.assertTrue(is_bare_greeting(text))

    def test_trailing_or_leading_address_token_is_stripped(self):
        for text in [
            "hey jarvis", "hi Jarvis", "jarvis hi", "hey, jarvis",
            "hey there jarvis", "jarvis, wake up", "wake up jarvis!",
        ]:
            with self.subTest(text=text):
                self.assertTrue(is_bare_greeting(text))

    def test_curly_apostrophe_is_normalized(self):
        self.assertTrue(is_bare_greeting("daddy’s home"))
        self.assertTrue(is_bare_greeting("i’m home"))

    def test_greeting_with_a_real_request_does_not_match(self):
        for text in [
            "hey what's the weather",
            "hi can you help me with something",
            "hello world this is a test",
            "good morning what's on my calendar",
            "wake up and check my email",
            "hey jarvis open the browser",
            "yo remind me to call mom",
        ]:
            with self.subTest(text=text):
                self.assertFalse(is_bare_greeting(text))

    def test_empty_or_address_only_does_not_match(self):
        for text in ["", "   ", "jarvis", "jarvis!", "  jarvis  ", None]:
            with self.subTest(text=text):
                self.assertFalse(is_bare_greeting(text))

    def test_unrelated_short_messages_do_not_match(self):
        for text in ["what time is it", "thanks", "ok", "yes", "no", "stop"]:
            with self.subTest(text=text):
                self.assertFalse(is_bare_greeting(text))


class TestWeatherResultLooksUsable(unittest.TestCase):

    def test_real_forecast_is_usable(self):
        real = (
            "Location: Tampa, Florida\n"
            "Now: 88°F, Partly cloudy (feels like 95°F)\n"
            "Next few hours:\n  14:00 - 89°F, Sunny, 0% chance of rain"
        )
        self.assertTrue(weather_result_looks_usable(real))

    def test_get_weather_error_strings_are_not_usable(self):
        for bad in [
            "Couldn't fetch weather: Expecting value: line 1 column 1 (char 0)",
            "Got a weather response but couldn't parse it: 'nearest_area'",
            "",
            None,
        ]:
            with self.subTest(bad=bad):
                self.assertFalse(weather_result_looks_usable(bad))


class TestFormatGreetingContext(unittest.TestCase):

    def _block(self):
        return format_greeting_context(
            "Battery: 82% | Disk free: 40 GB | Wi-Fi: HomeNet | Uptime: 3h",
            "Location: Tampa, Florida\nNow: 88°F, Partly cloudy",
        )

    def test_block_contains_both_tool_results(self):
        block = self._block()
        self.assertIn("Battery: 82%", block)
        self.assertIn("Location: Tampa, Florida", block)
        self.assertIn("get_system_status:", block)
        self.assertIn("get_weather:", block)

    def test_block_instructs_a_single_reply_and_no_refetch(self):
        block = self._block()
        self.assertIn("Reply exactly once", block)
        self.assertIn("no second message", block)
        self.assertIn("Do not call get_system_status again", block)

    def test_block_starts_with_its_own_header(self):
        # brain.py appends this after only a "\n\n" separator, so the
        # header has to live in the returned text (same convention as
        # agent/history_context.py's prompt_text).
        self.assertTrue(self._block().startswith("GREETING —"))


if __name__ == "__main__":
    unittest.main()
