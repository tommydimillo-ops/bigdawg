"""Tests for voice/speak.py -- speak_natural()'s enabled/disabled gate and
its primary (OpenAI TTS)/fallback (macOS `say`) failure handling. No real
audio is played: _speak_openai/_speak_fallback are mocked directly, since
what's under test here is the control flow and logging around them, not
audio playback itself (that's covered by real, manual smoke testing).

Run with: python -m unittest tests.test_voice_speak -v
"""
import io
import json
import logging
import unittest
from unittest.mock import patch

import voice.speak as speak
from agent.observability import _logger


class _CaptureLogs:
    """Mirrors tests/test_observability.py's capture helper -- attaches a
    stream handler to the real "jarvis" logger for the duration of a test,
    parsing each line back out as JSON."""

    def __enter__(self):
        self.buffer = io.StringIO()
        self.handler = logging.StreamHandler(self.buffer)
        _logger.addHandler(self.handler)
        return self

    def __exit__(self, *exc_info):
        _logger.removeHandler(self.handler)

    def lines(self):
        return [json.loads(line) for line in self.buffer.getvalue().splitlines() if line.strip()]


class TestSpeakNaturalGating(unittest.TestCase):

    @patch("voice.speak._speak_openai")
    def test_disabled_is_a_no_op(self, mock_speak_openai):
        with patch("voice.speak.settings") as mock_settings:
            mock_settings.tts_enabled = False
            speak.speak_natural("hello")
        mock_speak_openai.assert_not_called()

    @patch("voice.speak._speak_openai")
    def test_empty_text_is_a_no_op(self, mock_speak_openai):
        speak.speak_natural("")
        mock_speak_openai.assert_not_called()

    @patch("voice.speak._speak_fallback")
    @patch("voice.speak._speak_openai")
    def test_normal_success_never_touches_fallback(self, mock_openai, mock_fallback):
        speak.speak_natural("hello")
        mock_openai.assert_called_once_with("hello")
        mock_fallback.assert_not_called()


class TestSpeakNaturalFailureHandling(unittest.TestCase):

    @patch("voice.speak._speak_fallback")
    @patch("voice.speak._speak_openai", side_effect=RuntimeError("network down"))
    def test_primary_failure_falls_back_and_logs(self, mock_openai, mock_fallback):
        with _CaptureLogs() as capture:
            speak.speak_natural("hello")
        mock_fallback.assert_called_once_with("hello")
        events = [line["event"] for line in capture.lines()]
        self.assertIn("tts_primary_failed", events)
        self.assertNotIn("tts_completely_failed", events)

    @patch("voice.speak._speak_fallback", side_effect=RuntimeError("say missing"))
    @patch("voice.speak._speak_openai", side_effect=RuntimeError("network down"))
    def test_total_failure_is_logged_not_silently_swallowed(self, mock_openai, mock_fallback):
        with _CaptureLogs() as capture:
            speak.speak_natural("hello")  # must not raise
        events = {line["event"]: line for line in capture.lines()}
        self.assertIn("tts_completely_failed", events)
        self.assertEqual(events["tts_completely_failed"]["level"], "error")

    @patch("voice.speak._speak_fallback", side_effect=RuntimeError("say missing"))
    @patch("voice.speak._speak_openai", side_effect=RuntimeError("network down"))
    def test_total_failure_does_not_leak_full_text_beyond_preview(self, mock_openai, mock_fallback):
        long_text = "x" * 500
        with _CaptureLogs() as capture:
            speak.speak_natural(long_text)
        events = {line["event"]: line for line in capture.lines()}
        self.assertLess(len(events["tts_completely_failed"]["text_preview"]), 500)


if __name__ == "__main__":
    unittest.main()
