"""Security-focused tests for Phase 6 (voice-first Jarvis) -- exercising
the real mechanisms (matching this project's established policy, see
tests/test_phase4_security.py and tests/test_phase5_security.py), not just
the pure decision functions in isolation.

Three guarantees this covers, specific to voice:
1. A request that came from voice is held to the same permission system
   with an additional voice-only confirmation gate for reminders --
   nothing voice-specific ever loosens security.
2. A spoken "yes" cannot make a tool call run that wasn't the exact one
   already pending -- classify_confirmation_response() is pure text
   classification with no side effects; the only thing that can actually
   let a tool through is agent.autonomy.is_confirmed()'s existing exact-
   match ledger, completely untouched by anything in this phase.
3. The menu-bar app's single-instance lock actually prevents a second
   process from starting a second, competing microphone listener.

Run with: python -m unittest tests.test_phase6_security -v
"""
import os
import signal
import tempfile
import threading
import unittest
from unittest.mock import MagicMock, patch

import tools.schemas  # noqa: F401 -- populates the registry
import agent.execution_history as execution_history
import agent.jarvis_state as jarvis_state
import agent.quiet_mode as quiet_mode
import agent.usage as usage
import agent.voice_session as voice_session
import ui.menu_bar as menu_bar
from tools import registry


class IsolatedExecutorTestCase(unittest.TestCase):

    def setUp(self):
        self._real_history_file = execution_history.HISTORY_FILE
        self._real_state_file = jarvis_state.STATE_FILE
        self._real_usage_file = usage.USAGE_FILE
        execution_history.HISTORY_FILE = tempfile.mktemp(suffix=".json")
        jarvis_state.STATE_FILE = tempfile.mktemp(suffix=".json")
        usage.USAGE_FILE = tempfile.mktemp(suffix=".json")

    def tearDown(self):
        for path in (
            execution_history.HISTORY_FILE, f"{execution_history.HISTORY_FILE}.tmp",
            jarvis_state.STATE_FILE, f"{jarvis_state.STATE_FILE}.tmp",
            usage.USAGE_FILE, f"{usage.USAGE_FILE}.lock",
        ):
            if os.path.exists(path):
                os.remove(path)
        execution_history.HISTORY_FILE = self._real_history_file
        jarvis_state.STATE_FILE = self._real_state_file
        usage.USAGE_FILE = self._real_usage_file


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


def _text_block(text):
    block = MagicMock()
    block.type = "text"
    block.text = text
    return block


def _tool_use_block(tool_id, name, tool_input):
    block = MagicMock()
    block.type = "tool_use"
    block.id = tool_id
    block.name = name
    block.input = tool_input
    return block


class TestVoiceRequestsGetFullPermissionEnforcement(IsolatedExecutorTestCase):

    @patch("agent.executor.claude_client")
    def test_a_gated_tool_call_from_voice_is_held_for_confirmation_not_run(self, mock_client):
        # A fake permission_level-5-equivalent tool that would be
        # immediately obvious if it ran -- confirms voice routing gets the
        # same permission gate any chat message would, never a bypass.
        mock_handler = MagicMock(return_value="EXECUTED")
        registry.register(registry.ToolSpec(
            name="_phase6_security_test_tool",
            description="test only",
            input_schema={"type": "object", "properties": {}, "required": []},
            permission_level=5,
            handler=mock_handler,
        ))
        try:
            turn1 = MagicMock(stop_reason="tool_use")
            turn1.content = [
                _text_block("Let me do that..."),
                _tool_use_block("tu1", "_phase6_security_test_tool", {}),
            ]
            turn2 = MagicMock(stop_reason="end_turn")
            mock_client.messages.stream.side_effect = [
                _MockStream(["Let me do that..."], turn1),
                _MockStream(["I need your OK first."], turn2),
            ]

            result = voice_session.run_request("do the dangerous thing")

            mock_handler.assert_not_called()
            self.assertTrue(result.needs_confirmation)
            self.assertEqual(result.pending_tool, "_phase6_security_test_tool")
        finally:
            registry._REGISTRY.pop("_phase6_security_test_tool", None)

    @patch("tools.schemas.productivity.add_reminder")
    @patch("agent.executor.claude_client")
    def test_voice_reminder_requires_confirmation_before_dispatch(
        self, mock_client, mock_add_reminder,
    ):
        turn1 = MagicMock(stop_reason="tool_use")
        turn1.content = [
            _text_block("I'll set that reminder."),
            _tool_use_block("reminder1", "add_reminder", {"title": "Call mom"}),
        ]
        turn2 = MagicMock(stop_reason="end_turn")
        mock_client.messages.stream.side_effect = [
            _MockStream(["I'll set that reminder."], turn1),
            _MockStream(["Please confirm first."], turn2),
        ]

        result = voice_session.run_request("remind me to call mom")

        mock_add_reminder.assert_not_called()
        self.assertTrue(result.needs_confirmation)
        self.assertEqual(result.pending_tool, "add_reminder")


class TestConfirmationClassificationHasNoSideEffects(unittest.TestCase):
    """classify_confirmation_response is pure text classification -- these
    confirm it never reaches into the registry, autonomy ledger, or any
    other real system, regardless of what's said."""

    @patch("tools.registry.dispatch")
    def test_affirmative_classification_never_dispatches_anything(self, mock_dispatch):
        for phrase in ("yes", "go ahead", "send it", "do it", "confirmed"):
            with self.subTest(phrase=phrase):
                voice_session.classify_confirmation_response(phrase)
        mock_dispatch.assert_not_called()

    @patch("tools.registry.dispatch")
    def test_negative_classification_never_dispatches_anything(self, mock_dispatch):
        for phrase in ("no", "cancel", "don't do that", "never mind"):
            with self.subTest(phrase=phrase):
                voice_session.classify_confirmation_response(phrase)
        mock_dispatch.assert_not_called()

    def test_classification_is_a_pure_function_of_its_input(self):
        # Same input, same output, no hidden state -- calling it twice in
        # a row can't itself "confirm" or "consume" anything (unlike
        # agent.autonomy.is_confirmed, which is intentionally one-time-use).
        self.assertEqual(
            voice_session.classify_confirmation_response("yes"),
            voice_session.classify_confirmation_response("yes"),
        )


class TestSingleInstanceLock(unittest.TestCase):

    def setUp(self):
        self._real_lock_file = menu_bar.APP_LOCK_FILE
        menu_bar.APP_LOCK_FILE = tempfile.mktemp(suffix=".pid")

    def tearDown(self):
        if os.path.exists(menu_bar.APP_LOCK_FILE):
            os.remove(menu_bar.APP_LOCK_FILE)
        menu_bar.APP_LOCK_FILE = self._real_lock_file

    def test_first_acquire_succeeds(self):
        self.assertTrue(menu_bar._acquire_single_instance_lock())
        self.assertTrue(os.path.exists(menu_bar.APP_LOCK_FILE))

    def test_second_acquire_while_first_process_alive_fails(self):
        # Lock the file to OUR OWN real pid (definitely alive) to
        # simulate "another live instance is running" without needing to
        # actually spawn a second process.
        with open(menu_bar.APP_LOCK_FILE, "w") as f:
            f.write(str(os.getpid()))
        self.assertFalse(menu_bar._acquire_single_instance_lock())

    def test_stale_lock_from_a_dead_process_is_taken_over(self):
        # PID 999999 is never a real running process.
        with open(menu_bar.APP_LOCK_FILE, "w") as f:
            f.write("999999")
        self.assertTrue(menu_bar._acquire_single_instance_lock())

    def test_corrupt_lock_file_is_taken_over(self):
        with open(menu_bar.APP_LOCK_FILE, "w") as f:
            f.write("not-a-pid")
        self.assertTrue(menu_bar._acquire_single_instance_lock())

    def test_release_only_removes_a_lock_this_process_owns(self):
        with open(menu_bar.APP_LOCK_FILE, "w") as f:
            f.write("999999")  # some other (dead) process's lock
        menu_bar._release_single_instance_lock()
        self.assertTrue(os.path.exists(menu_bar.APP_LOCK_FILE))  # untouched

    @patch("ui.menu_bar.os._exit")
    def test_sigterm_handler_releases_lock_before_exiting(self, mock_exit):
        # Regression test for a real, confirmed bug: atexit callbacks don't
        # reliably fire when a rumps app is sent SIGTERM (its AppKit run
        # loop doesn't unwind back through normal Python interpreter
        # shutdown), so a plain `kill -TERM` left the lock file behind
        # forever. os._exit is mocked here so the handler's cleanup can be
        # observed without actually terminating the test process.
        menu_bar._acquire_single_instance_lock()
        menu_bar._handle_termination_signal(signal.SIGTERM, None)
        self.assertFalse(os.path.exists(menu_bar.APP_LOCK_FILE))
        mock_exit.assert_called_once_with(0)

    def test_release_removes_our_own_lock(self):
        menu_bar._acquire_single_instance_lock()
        menu_bar._release_single_instance_lock()
        self.assertFalse(os.path.exists(menu_bar.APP_LOCK_FILE))


class TestMenuBarTimeoutSafety(unittest.TestCase):

    @patch("ui.menu_bar.voice_session.request_cancel")
    def test_timeout_cancels_and_waits_for_worker_before_returning(self, mock_cancel):
        state = MagicMock(request_id="voice-timeout-request")
        completed = voice_session.VoiceRunResult(text="late result", state=state)
        future = MagicMock()

        def _result(timeout=None):
            if timeout is not None:
                raise menu_bar.concurrent.futures.TimeoutError
            return completed

        future.result.side_effect = _result
        executor = MagicMock()

        def _submit(function, request, history, on_state_created=None):
            on_state_created(state)
            return future

        executor.submit.side_effect = _submit

        app = MagicMock()
        app.executor = executor
        app.conversation = []
        app.events = MagicMock()
        app.stop_flag = threading.Event()
        app._speak.return_value = None

        result = menu_bar.CampusPilotApp._run_conversation_turn(app, "slow request")

        self.assertIsNone(result)
        mock_cancel.assert_called_once_with("voice-timeout-request")
        self.assertEqual(future.result.call_count, 2)
        app._speak.assert_called_once_with(
            "That took too long, so I gave up on it. Try again?"
        )


class TestMenuBarQuietMode(unittest.TestCase):

    def setUp(self):
        self.real_file = quiet_mode.QUIET_MODE_FILE
        quiet_mode.QUIET_MODE_FILE = tempfile.mktemp(suffix=".json")

    def tearDown(self):
        if os.path.exists(quiet_mode.QUIET_MODE_FILE):
            os.remove(quiet_mode.QUIET_MODE_FILE)
        quiet_mode.QUIET_MODE_FILE = self.real_file

    @patch("agent.tts_control.stop_speaking")
    def test_quiet_command_never_reaches_executor_or_speaker(self, mock_stop):
        app = MagicMock()

        menu_bar.CampusPilotApp._run_and_report(app, "quiet jarvis")

        app._run_conversation_turn.assert_not_called()
        app._speak_and_notify.assert_not_called()
        mock_stop.assert_called_once()
        self.assertTrue(quiet_mode.is_quiet())

    def test_non_wake_command_is_silently_ignored_while_quiet(self):
        quiet_mode.set_quiet(True)
        app = MagicMock()

        menu_bar.CampusPilotApp._run_and_report(app, "what time is it")

        app._run_conversation_turn.assert_not_called()
        app._speak_and_notify.assert_not_called()

    @patch("agent.tts_control.stop_speaking")
    def test_sleep_command_confirms_and_sets_a_10_minute_timer(self, mock_stop):
        app = MagicMock()

        menu_bar.CampusPilotApp._run_and_report(app, "sleep")

        app._run_conversation_turn.assert_not_called()
        app._speak_and_notify.assert_not_called()
        mock_stop.assert_called_once()
        app._speak.assert_called_once_with("Sleeping for 10 minutes.")
        self.assertTrue(quiet_mode.is_quiet())
        remaining = quiet_mode.remaining_seconds()
        self.assertIsNotNone(remaining)
        self.assertLessEqual(remaining, quiet_mode.SLEEP_DURATION_SECONDS)

    @patch("agent.tts_control.stop_speaking")
    def test_off_command_confirms_and_sets_a_30_minute_timer(self, mock_stop):
        app = MagicMock()

        menu_bar.CampusPilotApp._run_and_report(app, "off")

        app._run_conversation_turn.assert_not_called()
        app._speak_and_notify.assert_not_called()
        mock_stop.assert_called_once()
        app._speak.assert_called_once_with("Turning off for 30 minutes.")
        self.assertTrue(quiet_mode.is_quiet())
        remaining = quiet_mode.remaining_seconds()
        self.assertIsNotNone(remaining)
        self.assertLessEqual(remaining, quiet_mode.OFF_DURATION_SECONDS)

    def test_non_wake_command_is_silently_ignored_while_sleeping(self):
        quiet_mode.set_quiet(True, duration_seconds=quiet_mode.SLEEP_DURATION_SECONDS)
        app = MagicMock()

        menu_bar.CampusPilotApp._run_and_report(app, "what time is it")

        app._run_conversation_turn.assert_not_called()
        app._speak_and_notify.assert_not_called()
        app._speak.assert_not_called()


if __name__ == "__main__":
    unittest.main()
