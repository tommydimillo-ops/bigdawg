import os
import tempfile
import unittest

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


if __name__ == "__main__":
    unittest.main()
