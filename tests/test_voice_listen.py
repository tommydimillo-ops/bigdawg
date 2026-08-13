"""Tests for voice/listen.py -- wake-word detection/stripping, the exit-
phrase check, and listen_for_utterance's VAD/timeout/on_ready mechanics.
No real microphone hardware or network calls are used: sounddevice's
InputStream is mocked for the low-level recording tests, and
listen_for_utterance/transcribe are mocked for the higher-level wake-word
tests (matching this project's established policy -- see
tests/test_planner.py's docstring -- of not requiring paid API calls or,
here, real audio hardware in the automated suite).

Run with: python -m unittest tests.test_voice_listen -v
"""
import threading
import unittest
from unittest.mock import MagicMock, patch

import numpy as np

import voice.listen as listen
from agent.voice_state import VoiceState


class TestStripWakeWord(unittest.TestCase):

    def test_strips_wake_word_from_the_start(self):
        self.assertEqual(listen.strip_wake_word("jarvis what's the weather"), "what's the weather")

    def test_strips_wake_word_from_the_middle(self):
        self.assertEqual(listen.strip_wake_word("hi jarvis how are you"), "hi how are you")

    def test_is_case_insensitive(self):
        self.assertEqual(listen.strip_wake_word("Jarvis stop"), "stop")

    def test_wake_word_alone_leaves_empty_string(self):
        self.assertEqual(listen.strip_wake_word("jarvis"), "")

    def test_no_wake_word_present_leaves_text_unchanged(self):
        self.assertEqual(listen.strip_wake_word("what's the weather"), "what's the weather")

    def test_trailing_punctuation_is_cleaned_up(self):
        self.assertEqual(listen.strip_wake_word("jarvis, stop."), "stop")


class TestIsExitPhrase(unittest.TestCase):

    def test_wake_word_plus_stop_is_an_exit_phrase(self):
        self.assertTrue(listen.is_exit_phrase("jarvis stop"))

    def test_wake_word_plus_thats_all_is_an_exit_phrase(self):
        self.assertTrue(listen.is_exit_phrase("jarvis that's all"))

    def test_stop_word_alone_without_wake_word_is_not_an_exit_phrase(self):
        # The whole point of requiring the wake word -- ordinary speech
        # containing "stop" mid-command must not end the conversation.
        self.assertFalse(listen.is_exit_phrase("remind me to stop by the store"))

    def test_wake_word_alone_without_a_stop_word_is_not_an_exit_phrase(self):
        self.assertFalse(listen.is_exit_phrase("jarvis what time is it"))


class _MockInputStream:
    """Stands in for sd.InputStream -- a context manager whose .read(n)
    pops pre-scripted (block, overflow) chunks in order, returning
    silence once exhausted."""

    def __init__(self, chunks):
        self._chunks = list(chunks)

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        return False

    def read(self, n):
        if self._chunks:
            return self._chunks.pop(0), False
        return np.zeros((n, 1), dtype="int16"), False


def _quiet_chunk(n=800):
    return np.zeros((n, 1), dtype="int16")


def _loud_chunk(n=800):
    return np.full((n, 1), 5000, dtype="int16")


class TestListenForUtterance(unittest.TestCase):

    def setUp(self):
        listen_state_patcher = patch("voice.listen.voice_state")
        self.mock_voice_state = listen_state_patcher.start()
        self.addCleanup(listen_state_patcher.stop)

    @patch("voice.listen.sd.InputStream")
    def test_returns_none_when_stop_flag_already_set(self, mock_input_stream):
        mock_input_stream.return_value = _MockInputStream([_quiet_chunk()] * 10)
        stop_flag = threading.Event()
        stop_flag.set()
        audio, samplerate = listen.listen_for_utterance(stop_flag=stop_flag)
        self.assertIsNone(audio)
        self.assertIsNone(samplerate)

    @patch("voice.listen.sd.InputStream")
    def test_returns_none_after_max_wait_seconds_of_silence(self, mock_input_stream):
        mock_input_stream.return_value = _MockInputStream([_quiet_chunk()] * 50)
        audio, samplerate = listen.listen_for_utterance(max_wait_seconds=0.5)
        self.assertIsNone(audio)

    @patch("voice.listen.sd.InputStream")
    def test_detects_loud_speech_and_records_until_silence(self, mock_input_stream):
        # calibration_chunks (3, given the 1.0s default / 0.2s chunk floor)
        # of quiet chunks, then loud chunks (speech), then enough quiet
        # chunks to cross silence_seconds and stop recording.
        chunks = [_quiet_chunk()] * 3 + [_loud_chunk()] * 3 + [_quiet_chunk()] * 10
        mock_input_stream.return_value = _MockInputStream(chunks)
        audio, samplerate = listen.listen_for_utterance(silence_seconds=1.0)
        self.assertIsNotNone(audio)
        self.assertGreater(len(audio), 0)

    @patch("voice.listen.sd.InputStream")
    def test_on_ready_is_called_after_calibration(self, mock_input_stream):
        mock_input_stream.return_value = _MockInputStream([_quiet_chunk()] * 50)
        on_ready = MagicMock()
        listen.listen_for_utterance(max_wait_seconds=0.5, on_ready=on_ready)
        on_ready.assert_called_once()

    @patch("voice.listen.sd.InputStream")
    def test_sets_voice_state_to_listening(self, mock_input_stream):
        mock_input_stream.return_value = _MockInputStream([_quiet_chunk()] * 50)
        listen.listen_for_utterance(max_wait_seconds=0.5)
        self.mock_voice_state.set_status.assert_any_call(VoiceState.LISTENING)

    @patch("voice.listen.sd.InputStream")
    def test_default_max_seconds_comes_from_settings(self, mock_input_stream):
        # A regression guard for "don't hardcode configuration values
        # throughout the voice code" (Phase 6 section 16) -- confirms the
        # timeout is genuinely read from settings, not a hardcoded literal,
        # by overriding it and confirming behavior changes accordingly.
        chunks = [_quiet_chunk()] * 3 + [_loud_chunk()] * 3  # no silence after -- would run to max_seconds
        mock_input_stream.return_value = _MockInputStream(chunks + [_loud_chunk()] * 1000)
        with patch("voice.listen.settings") as mock_settings:
            mock_settings.voice_listen_timeout = 0.4  # tiny -- a handful of chunks
            audio, _ = listen.listen_for_utterance(silence_seconds=999)  # never triggers on silence
        self.assertIsNotNone(audio)
        # 0.4s / 0.2s chunk_duration = 2 recording chunks + the first
        # speech chunk = 3 total frames, not hundreds.
        self.assertLessEqual(len(audio), 3 * 800)


class TestTranscribeSetsVoiceState(unittest.TestCase):

    @patch("voice.listen.os.remove")
    @patch("voice.listen.wave.open")
    @patch("voice.listen.openai_client")
    @patch("voice.listen.voice_state")
    def test_transcribe_sets_transcribing_state(self, mock_voice_state, mock_client, mock_wave, mock_remove):
        mock_response = MagicMock()
        mock_response.text = "what's the weather"
        mock_client.audio.transcriptions.create.return_value = mock_response

        with patch("builtins.open", MagicMock()):
            result = listen.transcribe(np.zeros((10, 1), dtype="int16"), 16000)

        mock_voice_state.set_status.assert_any_call(VoiceState.TRANSCRIBING)
        self.assertEqual(result, "what's the weather")


if __name__ == "__main__":
    unittest.main()
