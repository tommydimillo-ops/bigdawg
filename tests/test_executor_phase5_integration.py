"""Integration tests for Phase 5's wiring into agent/executor.py's
execute_task_stream -- status transitions, execution-history recording,
and cross-interface jarvis_state updates -- exercised through the real
loop with only the network call itself mocked (matching this project's
established policy of not requiring paid API calls in the automated
suite -- see tests/test_planner.py's docstring).

Run with: python -m unittest tests.test_executor_phase5_integration -v
"""
import os
import tempfile
import unittest
from unittest.mock import MagicMock, patch

import tools.schemas  # noqa: F401 -- populates the registry
import agent.execution_history as execution_history
import agent.jarvis_state as jarvis_state
import agent.usage as usage
from agent.execution_state import ExecutionStatus, cancel_active, list_active
from agent.executor import execute_task_stream


class _MockStream:
    """Stands in for the object claude_client.messages.stream(...)
    returns -- a context manager exposing .text_stream (an iterator) and
    .get_final_message()."""

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


class IsolatedExecutorTestCase(unittest.TestCase):
    """Redirects execution_history.HISTORY_FILE, jarvis_state.STATE_FILE,
    and agent.usage.USAGE_FILE (execute_task_stream writes all three for
    real -- usage.py since Phase 8 part 1, whenever a mocked response's
    `.usage` attribute auto-vivifies as a truthy MagicMock), matching
    every other file-backed store's tests in this project."""

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


class TestSuccessfulRequest(IsolatedExecutorTestCase):

    @patch("agent.executor.claude_client")
    def test_simple_completion_records_a_completed_history_entry(self, mock_client):
        response = MagicMock(stop_reason="end_turn")
        mock_client.messages.stream.return_value = _MockStream(["Hi there!"], response)

        chunks = list(execute_task_stream("say hi"))

        self.assertEqual("".join(chunks), "Hi there!")
        records = execution_history.get_recent()
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0].status, "completed")

    @patch("agent.executor.claude_client")
    def test_jarvis_state_ends_idle_after_a_successful_request(self, mock_client):
        response = MagicMock(stop_reason="end_turn")
        mock_client.messages.stream.return_value = _MockStream(["Hi there!"], response)

        list(execute_task_stream("say hi"))

        self.assertEqual(jarvis_state.get_state().status, ExecutionStatus.IDLE.value)

    @patch("agent.executor.claude_client")
    def test_no_active_execution_remains_registered_after_completion(self, mock_client):
        response = MagicMock(stop_reason="end_turn")
        mock_client.messages.stream.return_value = _MockStream(["Hi there!"], response)

        list(execute_task_stream("say hi"))

        self.assertEqual(list_active(), [])

    @patch("agent.executor.claude_client")
    def test_jarvis_state_reflects_thinking_while_the_model_call_is_in_flight(self, mock_client):
        # Captures jarvis_state at the moment get_final_message() is
        # called (i.e. while still "inside" the model call from the
        # loop's point of view) to confirm the THINKING transition landed
        # before the call, not just at the very end of the request.
        seen = {}

        def _get_final_message():
            seen["status"] = jarvis_state.get_state().status
            return MagicMock(stop_reason="end_turn")

        stream = _MockStream(["Hi there!"], None)
        stream.get_final_message = _get_final_message
        mock_client.messages.stream.return_value = stream

        list(execute_task_stream("say hi"))

        self.assertEqual(seen["status"], ExecutionStatus.THINKING.value)


class TestMidFlightCancellation(IsolatedExecutorTestCase):

    @patch("agent.executor.claude_client")
    def test_cancelling_between_iterations_stops_the_loop_and_records_cancelled(self, mock_client):
        turn1_response = MagicMock(stop_reason="tool_use")
        turn1_response.content = [
            _text_block("Let me check..."),
            _tool_use_block("tu1", "get_system_status", {}),
        ]
        turn2_response = MagicMock(stop_reason="end_turn")

        mock_client.messages.stream.side_effect = [
            _MockStream(["Let me check..."], turn1_response),
            _MockStream(["(should never be reached)"], turn2_response),
        ]

        gen = execute_task_stream("check status")
        # Consuming exactly one chunk pauses execution right after the
        # turn's text streamed but *before* get_final_message()/tool
        # dispatch runs -- cancelling here lands squarely on the "before
        # each tool call" checkpoint inside _run_tool, the earliest of
        # the checkpoints this phase adds.
        first_chunk = next(gen)
        self.assertEqual(first_chunk, "Let me check...")

        active = list_active()
        self.assertEqual(len(active), 1)
        request_id = active[0].request_id
        self.assertTrue(cancel_active(request_id))

        remaining = list(gen)
        self.assertIn("Stopped, as requested.", remaining)
        # The second (mocked) model turn must never have been reached --
        # the top-of-loop cancellation check stopped it first.
        self.assertNotIn("(should never be reached)", remaining)

        record = execution_history.get_by_id(request_id)
        self.assertIsNotNone(record)
        self.assertEqual(record.status, "cancelled")
        # get_system_status was never actually dispatched -- the
        # before-each-tool-call checkpoint caught the cancellation first,
        # so it correctly never shows up as something that ran.
        self.assertEqual(record.tools_used, [])

        self.assertEqual(jarvis_state.get_state().status, ExecutionStatus.IDLE.value)
        self.assertEqual(list_active(), [])


if __name__ == "__main__":
    unittest.main()
