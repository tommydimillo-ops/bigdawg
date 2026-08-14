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
import dataclasses
import threading
import unittest
from unittest.mock import MagicMock, patch

import numpy as np

import voice.listen as listen
from agent.voice_state import VoiceState
from config.settings import settings


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


class TestTranscriptCleaning(unittest.TestCase):

    def test_rejects_real_prompt_echo_hallucinations(self):
        for value in (
            "context:",
            "context: ### Casual spoken request to a personal assistant named Jarvis.",
            "###",
            "May include American slang, filler words and informal phrasing.",
        ):
            with self.subTest(value=value):
                self.assertEqual(listen.clean_transcript(value), "")

    def test_preserves_real_short_replies_and_commands(self):
        for value in (
            "yes", "no", "quiet", "Jarvis.", "Hey Jarvis.",
            "Jarvis, what's the weather?",
        ):
            with self.subTest(value=value):
                self.assertEqual(listen.clean_transcript(value), value)


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


class TestSelectInputDevice(unittest.TestCase):
    """Regression tests for a real, confirmed production bug: the OS
    "default" input device silently became a paired iPhone's Continuity
    Camera mic instead of the Mac's own microphone, so the always-on
    listener was picking up near-silence from a phone across the room
    with no error to signal it. _select_input_device must always avoid
    an iPhone/iPad device when any other input device exists."""

    @patch("voice.listen.sd.query_devices")
    def test_prefers_macbook_over_iphone(self, mock_query):
        mock_query.return_value = [
            {"name": "iPhone Microphone", "max_input_channels": 1},
            {"name": "MacBook Pro Microphone", "max_input_channels": 1},
        ]
        self.assertEqual(listen._select_input_device(), 1)

    @patch("voice.listen.sd.query_devices")
    def test_skips_ipad_too(self, mock_query):
        mock_query.return_value = [
            {"name": "iPad Microphone", "max_input_channels": 1},
            {"name": "External USB Mic", "max_input_channels": 2},
        ]
        self.assertEqual(listen._select_input_device(), 1)

    @patch("voice.listen.sd.query_devices")
    def test_falls_back_to_iphone_if_nothing_else_has_input(self, mock_query):
        mock_query.return_value = [
            {"name": "iPhone Microphone", "max_input_channels": 1},
            {"name": "MacBook Pro Speakers", "max_input_channels": 0},
        ]
        self.assertIsNone(listen._select_input_device())

    @patch("voice.listen.sd.query_devices")
    def test_no_input_devices_at_all_returns_none(self, mock_query):
        mock_query.return_value = [
            {"name": "MacBook Pro Speakers", "max_input_channels": 0},
        ]
        self.assertIsNone(listen._select_input_device())

    @patch("voice.listen.sd.query_devices")
    def test_query_failure_degrades_to_none_not_raise(self, mock_query):
        mock_query.side_effect = RuntimeError("PortAudio error")
        self.assertIsNone(listen._select_input_device())

    @patch("voice.listen.sd.query_devices")
    def test_non_macbook_non_phone_device_is_used_over_the_fallback_search(self, mock_query):
        mock_query.return_value = [
            {"name": "Microsoft Teams Audio", "max_input_channels": 2},
        ]
        self.assertEqual(listen._select_input_device(), 0)


class TestListenForUtterance(unittest.TestCase):

    def setUp(self):
        listen_state_patcher = patch("voice.listen.voice_state")
        self.mock_voice_state = listen_state_patcher.start()
        self.addCleanup(listen_state_patcher.stop)

        device_patcher = patch("voice.listen._select_input_device", return_value=None)
        device_patcher.start()
        self.addCleanup(device_patcher.stop)

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
        # calibration_chunks (5 for the 1.0s default / 0.2s chunks)
        # of quiet chunks, then loud chunks (speech), then enough quiet
        # chunks to cross silence_seconds and stop recording.
        chunks = [_quiet_chunk()] * 5 + [_loud_chunk()] * 3 + [_quiet_chunk()] * 10
        mock_input_stream.return_value = _MockInputStream(chunks)
        audio, samplerate = listen.listen_for_utterance(silence_seconds=1.0)
        self.assertIsNotNone(audio)
        self.assertGreater(len(audio), 0)

    @patch("voice.listen.sd.InputStream")
    def test_single_noise_spike_does_not_trigger_recording(self, mock_input_stream):
        stop_flag = threading.Event()
        chunks = (
            [_quiet_chunk()] * 5
            + [_loud_chunk()]
            + [_quiet_chunk()] * 8
        )
        mock_input_stream.return_value = _MockInputStream(chunks)
        audio, _ = listen.listen_for_utterance(
            stop_flag=stop_flag, max_wait_seconds=1.2,
        )
        self.assertIsNone(audio)

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
        chunks = [_quiet_chunk()] * 5 + [_loud_chunk()] * 3  # no silence after -- would run to max_seconds
        mock_input_stream.return_value = _MockInputStream(chunks + [_loud_chunk()] * 1000)
        with patch("voice.listen.settings") as mock_settings:
            mock_settings.voice_listen_timeout = 0.4  # tiny -- a handful of chunks
            mock_settings.voice_min_signal_level = 250.0
            mock_settings.voice_trigger_chunks = 2
            audio, _ = listen.listen_for_utterance(silence_seconds=999)  # never triggers on silence
        self.assertIsNotNone(audio)
        # 0.4s / 0.2s chunk_duration = 2 recording chunks plus the 2
        # sustained trigger chunks, not hundreds.
        self.assertLessEqual(len(audio), 4 * 800)


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

    @patch("voice.listen.os.remove")
    @patch("voice.listen.wave.open")
    @patch("voice.listen.openai_client")
    @patch("voice.listen.voice_state")
    def test_transcribe_filters_prompt_echo(self, mock_voice_state, mock_client, mock_wave, mock_remove):
        mock_client.audio.transcriptions.create.return_value = MagicMock(
            text="context: ### Casual spoken request to a personal assistant named Jarvis."
        )
        with patch("builtins.open", MagicMock()):
            result = listen.transcribe(np.zeros((10, 1), dtype="int16"), 16000)
        self.assertEqual(result, "")


class TestTranscribeFallsBackToLocal(unittest.TestCase):
    """When the primary (OpenAI) transcription call fails -- e.g. no
    credits, network down -- transcribe() tries the on-device fallback
    before giving up entirely, IF local_transcription_fallback_enabled is
    on. It defaults to off: confirmed live via CPU profiling that
    SFSpeechRecognitionTask.cancel() doesn't actually stop macOS's
    on-device recognition work, so leaving this on by default left
    abandoned recognition tasks pegging the CPU indefinitely. The
    fallback mechanism itself is still tested here (patched on) since
    the code isn't gone, just off until that's root-caused."""

    @patch("voice.listen.settings", dataclasses.replace(settings, local_transcription_fallback_enabled=True))
    @patch("voice.listen.os.remove")
    @patch("voice.listen.wave.open")
    @patch("voice.listen.local_transcribe")
    @patch("voice.listen.openai_client")
    @patch("voice.listen.voice_state")
    def test_openai_failure_uses_local_fallback_result(
        self, mock_voice_state, mock_client, mock_local, mock_wave, mock_remove,
    ):
        mock_client.audio.transcriptions.create.side_effect = RuntimeError("no credits")
        mock_local.transcribe_local.return_value = "what's the weather"

        with patch("builtins.open", MagicMock()):
            result = listen.transcribe(np.zeros((10, 1), dtype="int16"), 16000)

        mock_local.transcribe_local.assert_called_once()
        self.assertEqual(result, "what's the weather")

    @patch("voice.listen.settings", dataclasses.replace(settings, local_transcription_fallback_enabled=False))
    @patch("voice.listen.os.remove")
    @patch("voice.listen.wave.open")
    @patch("voice.listen.local_transcribe")
    @patch("voice.listen.openai_client")
    @patch("voice.listen.voice_state")
    def test_disabled_by_default_skips_local_fallback_entirely(
        self, mock_voice_state, mock_client, mock_local, mock_wave, mock_remove,
    ):
        mock_client.audio.transcriptions.create.side_effect = RuntimeError("no credits")

        with patch("builtins.open", MagicMock()):
            result = listen.transcribe(np.zeros((10, 1), dtype="int16"), 16000)

        mock_local.transcribe_local.assert_not_called()
        self.assertEqual(result, "")

    @patch("voice.listen.settings", dataclasses.replace(settings, local_transcription_fallback_enabled=True))
    @patch("voice.listen.os.remove")
    @patch("voice.listen.wave.open")
    @patch("voice.listen.local_transcribe")
    @patch("voice.listen.openai_client")
    @patch("voice.listen.voice_state")
    def test_both_paths_failing_returns_empty_string_not_raise(
        self, mock_voice_state, mock_client, mock_local, mock_wave, mock_remove,
    ):
        mock_client.audio.transcriptions.create.side_effect = RuntimeError("no credits")
        mock_local.transcribe_local.return_value = None

        with patch("builtins.open", MagicMock()):
            result = listen.transcribe(np.zeros((10, 1), dtype="int16"), 16000)

        self.assertEqual(result, "")

    @patch("voice.listen.os.remove")
    @patch("voice.listen.wave.open")
    @patch("voice.listen.local_transcribe")
    @patch("voice.listen.openai_client")
    @patch("voice.listen.voice_state")
    def test_openai_success_never_touches_local_fallback(
        self, mock_voice_state, mock_client, mock_local, mock_wave, mock_remove,
    ):
        mock_client.audio.transcriptions.create.return_value = MagicMock(text="hi jarvis")
        with patch("builtins.open", MagicMock()):
            listen.transcribe(np.zeros((10, 1), dtype="int16"), 16000)
        mock_local.transcribe_local.assert_not_called()

    @patch("voice.listen.settings", dataclasses.replace(settings, local_transcription_fallback_enabled=True))
    @patch("voice.listen.os.remove")
    @patch("voice.listen.wave.open")
    @patch("voice.listen.local_transcribe")
    @patch("voice.listen.openai_client")
    @patch("voice.listen.voice_state")
    def test_local_fallback_result_still_goes_through_hallucination_filter(
        self, mock_voice_state, mock_client, mock_local, mock_wave, mock_remove,
    ):
        mock_client.audio.transcriptions.create.side_effect = RuntimeError("no credits")
        mock_local.transcribe_local.return_value = "context:"

        with patch("builtins.open", MagicMock()):
            result = listen.transcribe(np.zeros((10, 1), dtype="int16"), 16000)

        self.assertEqual(result, "")


class TestWakeDispatchGuards(unittest.TestCase):

    @patch("voice.listen.transcribe", return_value="Jarvis.")
    @patch("voice.listen.listen_for_utterance", return_value=(MagicMock(), 16000))
    def test_wake_word_alone_becomes_a_greeting(self, mock_listen, mock_transcribe):
        self.assertEqual(listen.wait_for_command(), "hello")

    @patch("voice.listen.transcribe")
    @patch("voice.listen.listen_for_utterance")
    def test_wake_word_alone_is_ignored_until_a_complete_command(self, mock_listen, mock_transcribe):
        mock_listen.side_effect = [
            (MagicMock(), 16000),
            (MagicMock(), 16000),
        ]
        mock_transcribe.side_effect = ["", "Jarvis, what's the weather?"]

        result = listen.wait_for_command()

        self.assertEqual(result, "what's the weather?")
        self.assertEqual(mock_listen.call_count, 2)

    @patch("voice.listen.transcribe", return_value="")
    @patch("voice.listen.listen_for_utterance", return_value=(MagicMock(), 16000))
    def test_rejected_followup_drops_back_to_passive(self, mock_listen, mock_transcribe):
        self.assertIsNone(listen.listen_for_followup())


if __name__ == "__main__":
    unittest.main()
