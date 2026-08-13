"""Tests for agent/voice_session.py -- the orchestration glue between
voice and the existing Jarvis core. run_request() exercises the real
execute_task_stream() with only the network call mocked (matching this
project's established policy -- see tests/test_planner.py's docstring).
The interrupt-watcher functions mock voice.listen's audio functions
directly rather than sounddevice, so no real microphone is needed.

Run with: python -m unittest tests.test_voice_session -v
"""
import ast
import inspect
import os
import tempfile
import threading
import unittest
from unittest.mock import MagicMock, patch

import agent.execution_history as execution_history
import agent.jarvis_state as jarvis_state
import agent.voice_session as voice_session
from agent.execution_state import ExecutionState, register_active, unregister_active


class IsolatedExecutorTestCase(unittest.TestCase):
    """Redirects both execution_history.HISTORY_FILE and
    jarvis_state.STATE_FILE -- run_request() drives the real
    execute_task_stream(), which writes both for real."""

    def setUp(self):
        self._real_history_file = execution_history.HISTORY_FILE
        self._real_state_file = jarvis_state.STATE_FILE
        execution_history.HISTORY_FILE = tempfile.mktemp(suffix=".json")
        jarvis_state.STATE_FILE = tempfile.mktemp(suffix=".json")

    def tearDown(self):
        for path in (
            execution_history.HISTORY_FILE, f"{execution_history.HISTORY_FILE}.tmp",
            jarvis_state.STATE_FILE, f"{jarvis_state.STATE_FILE}.tmp",
        ):
            if os.path.exists(path):
                os.remove(path)
        execution_history.HISTORY_FILE = self._real_history_file
        jarvis_state.STATE_FILE = self._real_state_file


class _MockStream:
    def __init__(self, chunks, final_message):
        self._chunks = chunks
        self._final_message = final_message

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        return False

    @property
    def text_stream(self):
        return iter(self._chunks)

    def get_final_message(self):
        return self._final_message


class TestVoiceRunResult(unittest.TestCase):

    def test_no_state_means_no_confirmation_no_cancellation(self):
        result = voice_session.VoiceRunResult(text="hi")
        self.assertFalse(result.needs_confirmation)
        self.assertFalse(result.was_cancelled)
        self.assertIsNone(result.pending_tool)
        self.assertIsNone(result.request_id)

    def test_confirmation_pending_reflected(self):
        state = ExecutionState(max_iterations=8)
        state.request_confirmation("send_email")
        result = voice_session.VoiceRunResult(text="please confirm", state=state)
        self.assertTrue(result.needs_confirmation)
        self.assertEqual(result.pending_tool, "send_email")

    def test_cancelled_reflected(self):
        state = ExecutionState(max_iterations=8)
        state.cancel()
        result = voice_session.VoiceRunResult(text="stopped", state=state)
        self.assertTrue(result.was_cancelled)


class TestRunRequest(IsolatedExecutorTestCase):

    @patch("agent.executor.claude_client")
    def test_simple_request_returns_text_and_state(self, mock_client):
        response = MagicMock(stop_reason="end_turn")
        mock_client.messages.stream.return_value = _MockStream(["Hi there!"], response)

        result = voice_session.run_request("say hi")

        self.assertEqual(result.text, "Hi there!")
        self.assertIsNotNone(result.state)
        self.assertFalse(result.needs_confirmation)
        self.assertFalse(result.was_cancelled)

    @patch("agent.executor.claude_client")
    def test_source_is_chat_not_scheduled(self, mock_client):
        # Voice input is untrusted input, held to the same rules as a
        # typed chat message (section 18) -- not the more permissive
        # "scheduled" source, and definitely not something new.
        response = MagicMock(stop_reason="end_turn")
        mock_client.messages.stream.return_value = _MockStream(["ok"], response)
        result = voice_session.run_request("do something")
        self.assertEqual(result.state.tools_executed, [])  # sanity: request completed
        # source is only observable indirectly here (no tool call was made
        # to inspect); the structural test below confirms the literal
        # source="chat" argument.

    def test_source_argument_is_literally_chat(self):
        source = inspect.signature(voice_session.run_request)
        tree = ast.parse(inspect.getsource(voice_session.run_request))
        calls = [n for n in ast.walk(tree) if isinstance(n, ast.Call)]
        execute_call = next(c for c in calls if getattr(c.func, "attr", getattr(c.func, "id", None)) == "execute_task_stream")
        source_kwarg = next(kw for kw in execute_call.keywords if kw.arg == "source")
        self.assertEqual(source_kwarg.value.value, "chat")

    @patch("agent.voice_session.execute_task_stream")
    def test_captures_its_state_explicitly_not_from_global_active_order(self, mock_execute):
        own_state = ExecutionState(max_iterations=8)

        def _stream(*args, **kwargs):
            kwargs["on_state_created"](own_state)
            yield "done"

        mock_execute.side_effect = _stream

        unrelated = ExecutionState(max_iterations=8)
        register_active("unrelated-request", unrelated)
        try:
            result = voice_session.run_request("test")
        finally:
            unregister_active("unrelated-request")

        self.assertIs(result.state, own_state)
        self.assertIsNot(result.state, unrelated)


class TestWatchForCancellation(unittest.TestCase):

    def tearDown(self):
        unregister_active("voice-cancel-test")

    @patch("agent.voice_session.transcribe")
    @patch("agent.voice_session.listen_for_utterance")
    def test_cancellation_phrase_cancels_the_active_request(self, mock_listen, mock_transcribe):
        state = ExecutionState(max_iterations=8)
        register_active("voice-cancel-test", state)

        mock_listen.return_value = (MagicMock(), 16000)
        mock_transcribe.return_value = "Jarvis, stop"

        done = threading.Event()
        voice_session.watch_for_cancellation(done)

        self.assertTrue(state.cancelled)

    @patch("agent.voice_session.transcribe")
    @patch("agent.voice_session.listen_for_utterance")
    def test_unrelated_speech_does_not_cancel(self, mock_listen, mock_transcribe):
        state = ExecutionState(max_iterations=8)
        register_active("voice-cancel-test", state)

        done = threading.Event()

        def _listen_then_finish(**kwargs):
            if mock_listen.call_count == 1:
                return MagicMock(), 16000
            done.set()
            return None, None

        mock_listen.side_effect = _listen_then_finish
        mock_transcribe.return_value = "what's the weather like"

        voice_session.watch_for_cancellation(done)

        self.assertFalse(state.cancelled)
        self.assertEqual(mock_listen.call_count, 2)

    @patch("agent.voice_session.transcribe")
    @patch("agent.voice_session.listen_for_utterance")
    def test_unrelated_speech_then_stop_still_cancels(self, mock_listen, mock_transcribe):
        state = ExecutionState(max_iterations=8)
        register_active("voice-cancel-test", state)

        mock_listen.side_effect = [
            (MagicMock(), 16000),
            (MagicMock(), 16000),
        ]
        mock_transcribe.side_effect = ["background conversation", "Jarvis, stop"]

        voice_session.watch_for_cancellation(threading.Event())

        self.assertTrue(state.cancelled)
        self.assertEqual(mock_listen.call_count, 2)

    @patch("agent.voice_session.listen_for_utterance")
    def test_done_event_already_set_never_transcribes(self, mock_listen):
        mock_listen.return_value = (None, None)
        done = threading.Event()
        done.set()
        with patch("agent.voice_session.transcribe") as mock_transcribe:
            voice_session.watch_for_cancellation(done)
            mock_transcribe.assert_not_called()


class TestSpeakWithInterruptionWatch(unittest.TestCase):

    @patch("agent.voice_session.settings")
    @patch("agent.voice_session.speak_natural")
    def test_interruption_disabled_just_speaks(self, mock_speak, mock_settings):
        mock_settings.voice_interruption_enabled = False
        result = voice_session.speak_with_interruption_watch("hello")
        mock_speak.assert_called_once_with("hello")
        self.assertIsNone(result)

    @patch("agent.voice_session.tts_control")
    @patch("agent.voice_session.transcribe")
    @patch("agent.voice_session.listen_for_utterance")
    @patch("agent.voice_session.speak_natural")
    def test_wake_word_heard_interrupts_and_returns_command(
        self, mock_speak, mock_listen, mock_transcribe, mock_tts_control,
    ):
        def _fake_listen(stop_flag=None, max_wait_seconds=None, on_ready=None):
            if on_ready:
                on_ready()
            return MagicMock(), 16000

        mock_listen.side_effect = _fake_listen
        mock_transcribe.return_value = "jarvis play some music"

        result = voice_session.speak_with_interruption_watch("a long reply", max_wait_seconds=1)

        mock_tts_control.stop_speaking.assert_called_once()
        self.assertEqual(result, "play some music")

    @patch("agent.voice_session.transcribe")
    @patch("agent.voice_session.listen_for_utterance")
    @patch("agent.voice_session.speak_natural")
    def test_no_wake_word_heard_returns_none(self, mock_speak, mock_listen, mock_transcribe):
        def _fake_listen(stop_flag=None, max_wait_seconds=None, on_ready=None):
            if on_ready:
                on_ready()
            return None, None  # nothing heard before done_event was set

        mock_listen.side_effect = _fake_listen

        result = voice_session.speak_with_interruption_watch("a reply", max_wait_seconds=1)

        mock_speak.assert_called_once_with("a reply")
        self.assertIsNone(result)

    @patch("agent.voice_session.tts_control")
    @patch("agent.voice_session.transcribe")
    @patch("agent.voice_session.listen_for_utterance")
    def test_unrelated_speech_then_wake_word_still_interrupts(
        self, mock_listen, mock_transcribe, mock_tts_control,
    ):
        def _fake_listen(stop_flag=None, max_wait_seconds=None, on_ready=None):
            if on_ready:
                on_ready()
            return MagicMock(), 16000

        mock_listen.side_effect = _fake_listen
        mock_transcribe.side_effect = ["background conversation", "jarvis stop talking"]

        result = voice_session._watch_for_speech_interrupt(
            threading.Event(), threading.Event(), max_wait_seconds=1,
        )

        mock_tts_control.stop_speaking.assert_called_once()
        self.assertEqual(result, "stop talking")
        self.assertEqual(mock_listen.call_count, 2)


class TestClassifyConfirmationResponse(unittest.TestCase):

    def test_yes_variants_are_affirmative(self):
        for phrase in ("yes", "yeah", "yep", "sure", "go ahead", "do it", "send it", "okay"):
            with self.subTest(phrase=phrase):
                self.assertEqual(voice_session.classify_confirmation_response(phrase), "affirmative")

    def test_no_variants_are_negative(self):
        for phrase in ("no", "nope", "nah", "cancel", "don't do that", "never mind", "stop"):
            with self.subTest(phrase=phrase):
                self.assertEqual(voice_session.classify_confirmation_response(phrase), "negative")

    def test_unrelated_speech_is_unclear(self):
        self.assertEqual(voice_session.classify_confirmation_response("what time is it"), "unclear")

    def test_empty_response_is_unclear(self):
        self.assertEqual(voice_session.classify_confirmation_response(""), "unclear")
        self.assertEqual(voice_session.classify_confirmation_response("   "), "unclear")

    def test_full_sentence_affirmative(self):
        self.assertEqual(
            voice_session.classify_confirmation_response("Yeah, go ahead and send it"), "affirmative",
        )

    def test_full_sentence_negative(self):
        self.assertEqual(
            voice_session.classify_confirmation_response("No, don't do that"), "negative",
        )


class TestCancellationAndStopPhraseHelpers(unittest.TestCase):

    def test_is_cancellation_phrase_requires_wake_word(self):
        self.assertTrue(voice_session.is_cancellation_phrase("jarvis stop"))
        self.assertFalse(voice_session.is_cancellation_phrase("please stop by the store"))

    def test_is_bare_stop_phrase_exact_match_only(self):
        self.assertTrue(voice_session.is_bare_stop_phrase("stop"))
        self.assertTrue(voice_session.is_bare_stop_phrase("cancel"))
        self.assertTrue(voice_session.is_bare_stop_phrase("never mind"))
        self.assertFalse(voice_session.is_bare_stop_phrase("stop by the store"))
        self.assertFalse(voice_session.is_bare_stop_phrase("what is the weather"))
        self.assertFalse(voice_session.is_bare_stop_phrase(""))


class TestVoiceSessionNeverTouchesAutonomyOrDispatchDirectly(unittest.TestCase):
    """Structural check, matching tests/test_phase4_security.py's pattern
    for agent/planner.py -- confirms voice has no code path to directly
    grant a confirmation or dispatch a tool, even if a future bug were
    introduced elsewhere in this module. Everything must go through
    agent.executor.execute_task_stream, the same as every other
    interface."""

    def test_no_reference_to_autonomy_internals(self):
        # Checks the real import statements (AST), not a source-text
        # substring search -- the module's docstrings legitimately
        # *mention* agent.autonomy in prose to explain why the security
        # property holds, which a crude string search would misflag.
        tree = ast.parse(inspect.getsource(voice_session))
        imported_modules = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported_modules.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported_modules.add(node.module)
                imported_modules.update(f"{node.module}.{alias.name}" for alias in node.names)
        self.assertNotIn("agent.autonomy", imported_modules)
        self.assertFalse(any(m.startswith("agent.autonomy.") for m in imported_modules))

    def test_no_direct_tool_dispatch(self):
        tree = ast.parse(inspect.getsource(voice_session))
        dispatch_calls = [
            node for node in ast.walk(tree)
            if isinstance(node, ast.Attribute) and node.attr == "dispatch"
        ]
        self.assertEqual(dispatch_calls, [])


if __name__ == "__main__":
    unittest.main()
