"""Security-focused tests for Phase 6 (voice-first Jarvis) -- exercising
the real mechanisms (matching this project's established policy, see
tests/test_phase4_security.py and tests/test_phase5_security.py), not just
the pure decision functions in isolation.

Three guarantees this covers, specific to voice:
1. A request that came from voice is held to the exact same permission/
   confirmation rules as a typed chat message -- source="chat", nothing
   voice-specific ever loosens it.
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
import tempfile
import unittest
from unittest.mock import MagicMock, patch

import tools.schemas  # noqa: F401 -- populates the registry
import agent.execution_history as execution_history
import agent.jarvis_state as jarvis_state
import agent.voice_session as voice_session
import ui.menu_bar as menu_bar
from tools import registry


class IsolatedExecutorTestCase(unittest.TestCase):

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
        # immediately obvious if it ran -- confirms voice's source="chat"
        # routing gets the SAME confirmation gate any chat message would,
        # never a bypass.
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

    def test_release_removes_our_own_lock(self):
        menu_bar._acquire_single_instance_lock()
        menu_bar._release_single_instance_lock()
        self.assertFalse(os.path.exists(menu_bar.APP_LOCK_FILE))


if __name__ == "__main__":
    unittest.main()
