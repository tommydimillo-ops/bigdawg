"""Tests for voice/speak.py -- speak_natural()'s enabled/disabled gate,
its primary (OpenAI TTS)/fallback (macOS `say`) failure handling, and the
sentence-chunked playback _speak_openai splits into (so the first, much
shorter sentence's TTS call is what gates when speech starts, not the
whole reply's). No real audio is played: _speak_openai/_speak_fallback
(or, for the chunking tests, _speak_openai_chunk/_play_and_track) are
mocked directly, since what's under test here is the control flow and
logging around them, not audio playback itself (that's covered by real,
manual smoke testing).

Run with: python -m unittest tests.test_voice_speak -v
"""
import io
import json
import logging
import os
import subprocess
import tempfile
import unittest
from unittest.mock import patch

import agent.tts_control as tts_control
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


class TestSplitIntoSentences(unittest.TestCase):

    def test_single_sentence_is_one_chunk(self):
        self.assertEqual(speak._split_into_sentences("It's 72 degrees."), ["It's 72 degrees."])

    def test_multiple_sentences_split(self):
        chunks = speak._split_into_sentences("It's sunny today. Bring a jacket tonight. Enjoy!")
        self.assertEqual(chunks, ["It's sunny today.", "Bring a jacket tonight.", "Enjoy!"])

    def test_decimal_numbers_are_not_split(self):
        chunks = speak._split_into_sentences("It's 3.5 miles away.")
        self.assertEqual(chunks, ["It's 3.5 miles away."])

    def test_empty_text_produces_no_chunks(self):
        self.assertEqual(speak._split_into_sentences(""), [])

    def test_concatenation_always_reconstructs_readable_content(self):
        # The exact split points are a best-effort heuristic (documented
        # in voice/speak.py as occasionally over-splitting on an
        # abbreviation) -- what must always hold is that no words are
        # dropped or duplicated.
        text = "Dr. Smith called. Your appointment is at 3 PM."
        chunks = speak._split_into_sentences(text)
        self.assertEqual(" ".join(chunks), text)


class TestSpeakOpenAIChunking(unittest.TestCase):
    """_speak_openai splits into sentences and calls _speak_openai_chunk
    once per sentence, stopping early if a chunk reports it was
    interrupted -- this is the actual latency fix (see voice/speak.py's
    _speak_openai docstring): the first, much shorter sentence's TTS call
    is what gates when speech starts, not the whole reply's."""

    @patch("voice.speak._speak_openai_chunk", return_value=False)
    def test_calls_once_per_sentence_in_order(self, mock_chunk):
        speak._speak_openai("First sentence. Second sentence. Third sentence.")
        calls = [call.args[0] for call in mock_chunk.call_args_list]
        self.assertEqual(calls, ["First sentence.", "Second sentence.", "Third sentence."])

    @patch("voice.speak._speak_openai_chunk", return_value=False)
    def test_single_sentence_reply_makes_exactly_one_call(self, mock_chunk):
        speak._speak_openai("Sure!")
        mock_chunk.assert_called_once_with("Sure!")

    @patch("voice.speak._speak_openai_chunk")
    def test_stops_at_the_first_interrupted_chunk(self, mock_chunk):
        mock_chunk.side_effect = [False, True, False]  # chunk 2 reports interrupted
        speak._speak_openai("One. Two. Three.")
        self.assertEqual(mock_chunk.call_count, 2)  # never reaches "Three."


class TestPlayAndTrackReturnCode(unittest.TestCase):
    """_speak_openai_chunk relies on _play_and_track's return value to
    tell "finished normally" apart from "was killed by stop_speaking()"
    -- exercised here against a real (harmless, no-audio) subprocess
    rather than mocking subprocess.Popen itself, since the exact sign of
    Popen.returncode after a signal kill is precisely the real behavior
    under test."""

    def setUp(self):
        self._real_pid_file = tts_control.TTS_PID_FILE
        tts_control.TTS_PID_FILE = tempfile.mktemp(suffix=".pid")

    def tearDown(self):
        if os.path.exists(tts_control.TTS_PID_FILE):
            os.remove(tts_control.TTS_PID_FILE)
        tts_control.TTS_PID_FILE = self._real_pid_file

    def test_normal_completion_returns_zero(self):
        returncode = speak._play_and_track(["python3", "-c", "pass"])
        self.assertEqual(returncode, 0)

    def test_signal_kill_returns_a_negative_code(self):
        # Starts a process that would otherwise run for a while, then
        # sends it SIGTERM the way stop_speaking() does -- reading the
        # pid directly from TTS_PID_FILE rather than going through
        # stop_speaking() itself, since that also filters by process
        # name (only "say"/"afplay"), which this fake test command isn't
        # and doesn't need to be -- tts_control's own name-matching is
        # covered by tests/test_tts_control.py, not this file.
        import signal
        import threading
        import time as time_module

        def _interrupt_shortly():
            time_module.sleep(0.05)
            with open(tts_control.TTS_PID_FILE) as f:
                pid = int(f.read().strip())
            os.kill(pid, signal.SIGTERM)

        threading.Thread(target=_interrupt_shortly, daemon=True).start()
        returncode = speak._play_and_track(["python3", "-c", "import time; time.sleep(5)"])
        self.assertLess(returncode, 0)


if __name__ == "__main__":
    unittest.main()
