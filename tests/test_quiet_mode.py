import os
import tempfile
import time
import unittest
from unittest.mock import patch

import agent.quiet_mode as quiet_mode
from agent.quiet_mode import QuietAction


class IsolatedQuietModeTest(unittest.TestCase):

    def setUp(self):
        self.real_file = quiet_mode.QUIET_MODE_FILE
        quiet_mode.QUIET_MODE_FILE = tempfile.mktemp(suffix=".json")

    def tearDown(self):
        if os.path.exists(quiet_mode.QUIET_MODE_FILE):
            os.remove(quiet_mode.QUIET_MODE_FILE)
        quiet_mode.QUIET_MODE_FILE = self.real_file

    def test_quiet_variants_enter_without_model_semantics(self):
        for phrase in ("quiet", "quiet jarvis", "Jarvis, be quiet", "mute Jarvis", "stop talking"):
            with self.subTest(phrase=phrase):
                quiet_mode.set_quiet(False)
                self.assertEqual(quiet_mode.classify(phrase), QuietAction.ENTER_QUIET)
                self.assertTrue(quiet_mode.is_quiet())

    def test_arbitrary_requests_are_ignored_while_quiet(self):
        quiet_mode.set_quiet(True)
        self.assertEqual(quiet_mode.classify("Jarvis what's the weather"), QuietAction.IGNORE)
        self.assertTrue(quiet_mode.is_quiet())

    def test_wake_variants_resume(self):
        for phrase in ("wake up jarvis", "hi jarvis", "hello Jarvis", "hey", "I'm back"):
            with self.subTest(phrase=phrase):
                quiet_mode.set_quiet(True)
                self.assertEqual(quiet_mode.classify(phrase), QuietAction.WAKE)
                self.assertFalse(quiet_mode.is_quiet())

    def test_wake_phrase_can_include_first_command(self):
        for phrase in (
            "Hi Jarvis, what time is it?",
            "Hello Jarvis play some music",
            "Wake up Jarvis and show my schedule",
        ):
            with self.subTest(phrase=phrase):
                quiet_mode.set_quiet(True)
                self.assertEqual(quiet_mode.classify(phrase), QuietAction.WAKE)
                self.assertFalse(quiet_mode.is_quiet())

    def test_unrelated_prefix_does_not_wake(self):
        quiet_mode.set_quiet(True)
        self.assertEqual(quiet_mode.classify("wake me at seven"), QuietAction.IGNORE)
        self.assertTrue(quiet_mode.is_quiet())

    def test_normal_request_passes_when_not_quiet(self):
        self.assertEqual(quiet_mode.classify("what is the weather"), QuietAction.PASS_THROUGH)

    def test_similar_sentence_does_not_accidentally_enable_quiet(self):
        self.assertFalse(quiet_mode.is_quiet_phrase("find me a quiet place to study"))
        self.assertFalse(quiet_mode.is_quiet_phrase("set a quiet reminder"))


class TestTimedSleepAndOff(IsolatedQuietModeTest):

    def test_sleep_variants_enter_with_a_10_minute_timer(self):
        for phrase in ("sleep", "go to sleep", "Jarvis, sleep mode", "take a nap"):
            with self.subTest(phrase=phrase):
                quiet_mode.set_quiet(False)
                self.assertEqual(quiet_mode.classify(phrase), QuietAction.ENTER_SLEEP)
                self.assertTrue(quiet_mode.is_quiet())
                remaining = quiet_mode.remaining_seconds()
                self.assertIsNotNone(remaining)
                self.assertLessEqual(remaining, quiet_mode.SLEEP_DURATION_SECONDS)
                self.assertGreater(remaining, quiet_mode.SLEEP_DURATION_SECONDS - 5)

    def test_off_variants_enter_with_a_30_minute_timer(self):
        for phrase in ("off", "turn off", "Jarvis, shut off", "power off"):
            with self.subTest(phrase=phrase):
                quiet_mode.set_quiet(False)
                self.assertEqual(quiet_mode.classify(phrase), QuietAction.ENTER_OFF)
                self.assertTrue(quiet_mode.is_quiet())
                remaining = quiet_mode.remaining_seconds()
                self.assertIsNotNone(remaining)
                self.assertLessEqual(remaining, quiet_mode.OFF_DURATION_SECONDS)
                self.assertGreater(remaining, quiet_mode.OFF_DURATION_SECONDS - 5)

    def test_indefinite_quiet_has_no_remaining_seconds(self):
        quiet_mode.set_quiet(True)
        self.assertIsNone(quiet_mode.remaining_seconds())

    def test_not_quiet_has_no_remaining_seconds(self):
        quiet_mode.set_quiet(False)
        self.assertIsNone(quiet_mode.remaining_seconds())

    def test_timer_expiry_auto_wakes_without_an_explicit_wake_phrase(self):
        quiet_mode.set_quiet(True, duration_seconds=0.01)
        self.assertTrue(quiet_mode.is_quiet())
        time.sleep(0.05)
        self.assertFalse(quiet_mode.is_quiet())
        self.assertEqual(quiet_mode.classify("what's the weather"), QuietAction.PASS_THROUGH)

    def test_wake_phrase_still_cancels_a_timed_sleep_early(self):
        quiet_mode.set_quiet(True, duration_seconds=quiet_mode.SLEEP_DURATION_SECONDS)
        self.assertEqual(quiet_mode.classify("hey jarvis"), QuietAction.WAKE)
        self.assertFalse(quiet_mode.is_quiet())

    def test_arbitrary_requests_are_ignored_while_sleeping(self):
        quiet_mode.set_quiet(True, duration_seconds=quiet_mode.SLEEP_DURATION_SECONDS)
        self.assertEqual(quiet_mode.classify("what's on my calendar"), QuietAction.IGNORE)
        self.assertTrue(quiet_mode.is_quiet())

    def test_off_said_while_already_quiet_upgrades_to_a_bounded_timer(self):
        quiet_mode.set_quiet(True)  # indefinite quiet, no timer
        self.assertIsNone(quiet_mode.remaining_seconds())
        self.assertEqual(quiet_mode.classify("off"), QuietAction.ENTER_OFF)
        self.assertIsNotNone(quiet_mode.remaining_seconds())


if __name__ == "__main__":
    unittest.main()
