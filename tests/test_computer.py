"""Tests for tools/computer.py's open_application -- no real `open`
calls or filesystem scans happen here: subprocess.run and the installed-
app directory scan are both mocked, since this module's own logic
(alias lookup, filler-word stripping, fuzzy-match fallback) is what's
under test, not macOS's actual app resolution.

Run with: python -m unittest tests.test_computer -v
"""
import unittest
from unittest.mock import MagicMock, patch

import tools.computer as computer


def _ok():
    return MagicMock(returncode=0, stderr="")


def _fail(message="Unable to find application named 'x'"):
    return MagicMock(returncode=1, stderr=message)


class TestOpenApplicationHappyPath(unittest.TestCase):

    @patch("tools.computer.subprocess.run")
    def test_exact_app_name_opens_directly(self, mock_run):
        mock_run.return_value = _ok()
        result = computer.open_application("Calculator")
        self.assertEqual(result, "Opening Calculator")
        mock_run.assert_called_once_with(
            ["open", "-a", "Calculator"], capture_output=True, text=True,
        )

    @patch("tools.computer.subprocess.run")
    def test_known_alias_is_expanded(self, mock_run):
        mock_run.return_value = _ok()
        result = computer.open_application("calc")
        self.assertEqual(result, "Opening Calculator")

    @patch("tools.computer.subprocess.run")
    def test_alias_lookup_is_case_insensitive(self, mock_run):
        mock_run.return_value = _ok()
        result = computer.open_application("CHROME")
        self.assertEqual(result, "Opening Google Chrome")


class TestOpenApplicationFillerWordFallback(unittest.TestCase):
    """Regression coverage for a real, reported problem: `open -a`
    requires a close-to-exact application name and fails outright on
    anything extra, but casual/voice-transcribed phrasing routinely
    includes words like "the" and "app" that aren't part of any real
    app name -- confirmed live that "the calculator app" fails where
    "Calculator" succeeds."""

    @patch("tools.computer._find_close_match", return_value="Calculator")
    @patch("tools.computer.subprocess.run")
    def test_filler_words_fall_back_to_a_fuzzy_match(self, mock_run, mock_match):
        mock_run.side_effect = [_fail(), _ok()]
        result = computer.open_application("the calculator app")
        self.assertEqual(result, "Opening Calculator")
        self.assertEqual(mock_run.call_count, 2)
        mock_run.assert_any_call(["open", "-a", "the calculator app"], capture_output=True, text=True)
        mock_run.assert_any_call(["open", "-a", "Calculator"], capture_output=True, text=True)

    @patch("tools.computer._find_close_match", return_value=None)
    @patch("tools.computer.subprocess.run")
    def test_no_fuzzy_match_found_reports_the_original_failure(self, mock_run, mock_match):
        mock_run.return_value = _fail("Unable to find application named 'not a real app'")
        result = computer.open_application("not a real app")
        self.assertIn("Could not open", result)
        self.assertIn("not a real app", result)

    @patch("tools.computer._find_close_match", return_value="Calculator")
    @patch("tools.computer.subprocess.run")
    def test_fuzzy_match_also_failing_to_open_still_reports_original_failure(self, mock_run, mock_match):
        mock_run.side_effect = [_fail(), _fail("still broken")]
        result = computer.open_application("the calculator app")
        self.assertIn("Could not open", result)


class TestFindCloseMatch(unittest.TestCase):

    @patch("tools.computer._installed_app_names", return_value=["Calculator", "Calendar", "Safari"])
    def test_exact_case_insensitive_match_wins(self, mock_installed):
        self.assertEqual(computer._find_close_match("calculator"), "Calculator")

    @patch("tools.computer._installed_app_names", return_value=["Calculator", "Calendar", "Safari"])
    def test_substring_match(self, mock_installed):
        self.assertEqual(computer._find_close_match("calc"), "Calculator")

    @patch("tools.computer._installed_app_names", return_value=["Calculator", "Calendar", "Safari"])
    def test_typo_tolerant_fuzzy_match(self, mock_installed):
        self.assertEqual(computer._find_close_match("Calculater"), "Calculator")

    @patch("tools.computer._installed_app_names", return_value=["Calculator", "Calendar", "Safari"])
    def test_no_reasonable_match_returns_none(self, mock_installed):
        self.assertIsNone(computer._find_close_match("walmart.com"))

    @patch("tools.computer._installed_app_names", return_value=[])
    def test_no_installed_apps_returns_none(self, mock_installed):
        self.assertIsNone(computer._find_close_match("Calculator"))


class TestStripFillerWords(unittest.TestCase):

    def test_strips_leading_and_trailing_filler(self):
        self.assertEqual(computer._strip_filler_words("the calculator app"), "calculator")

    def test_no_filler_words_unchanged(self):
        self.assertEqual(computer._strip_filler_words("Calculator"), "Calculator")

    def test_multi_word_app_name_preserved(self):
        self.assertEqual(computer._strip_filler_words("the google chrome app"), "google chrome")

    def test_all_filler_words_falls_back_to_original(self):
        self.assertEqual(computer._strip_filler_words("the app"), "the app")


if __name__ == "__main__":
    unittest.main()
