"""Integration tests for Phase 7's wiring into agent/executor.py's
execute_task_stream -- confirms the routing decision is attributed onto
ExecutionState/ExecutionHistory, and -- critically -- that this NEVER
executes a real agent (no network call, no memory write) even for a
request whose text obviously matches a specialist's routing keywords.
Only the Claude client itself is mocked, matching every other executor
integration test in this project (see
tests/test_executor_phase5_integration.py's docstring).

Run with: python -m unittest tests.test_agents_executor_integration -v
"""
import os
import tempfile
import unittest
from unittest.mock import MagicMock, patch

import tools.schemas  # noqa: F401 -- populates the registry
import agent.execution_history as execution_history
import agent.jarvis_state as jarvis_state
from agent.executor import execute_task_stream


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


class TestAgentRoutingNeverExecutes(IsolatedExecutorTestCase):
    """The central claim: routing a request that matches RESEARCH/MEMORY
    keywords must NEVER make the real network call or memory write --
    only consult_coworker_agent (a real, permission-gated tool the model
    has to explicitly choose to call) can do that."""

    @patch("agent.agents.memory.remember")
    @patch("agent.agents.research.research")
    @patch("agent.executor.claude_client")
    def test_research_shaped_request_never_calls_the_real_research_function(
        self, mock_client, mock_research, mock_remember,
    ):
        response = MagicMock(stop_reason="end_turn")
        mock_client.messages.stream.return_value = _MockStream(["Sure!"], response)

        list(execute_task_stream("Research the best laptops under $1,000."))

        mock_research.assert_not_called()
        mock_remember.assert_not_called()

    @patch("agent.agents.memory.remember")
    @patch("agent.executor.claude_client")
    def test_memory_shaped_request_never_calls_the_real_remember_function(
        self, mock_client, mock_remember,
    ):
        response = MagicMock(stop_reason="end_turn")
        mock_client.messages.stream.return_value = _MockStream(["Sure!"], response)

        list(execute_task_stream("Remember that I prefer dark mode."))

        mock_remember.assert_not_called()

    @patch("agent.executor.claude_client")
    def test_normal_loop_still_runs_to_completion(self, mock_client):
        response = MagicMock(stop_reason="end_turn")
        mock_client.messages.stream.return_value = _MockStream(["Hi there!"], response)

        chunks = list(execute_task_stream("Research the best laptops under $1,000."))

        self.assertEqual("".join(chunks), "Hi there!")


class TestAgentAttribution(IsolatedExecutorTestCase):

    @patch("agent.executor.claude_client")
    def test_matching_request_records_agents_used_in_history(self, mock_client):
        response = MagicMock(stop_reason="end_turn")
        mock_client.messages.stream.return_value = _MockStream(["Sure!"], response)

        list(execute_task_stream("Remember that I prefer dark mode."))

        records = execution_history.get_recent()
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0].agents_used, ["memory"])

    @patch("agent.executor.claude_client")
    def test_direct_request_has_no_agents_used(self, mock_client):
        response = MagicMock(stop_reason="end_turn")
        mock_client.messages.stream.return_value = _MockStream(["Four."], response)

        list(execute_task_stream("What is 2+2?"))

        records = execution_history.get_recent()
        self.assertEqual(records[0].agents_used, [])

    @patch("agent.executor.claude_client")
    def test_state_observer_sees_active_agent_for_a_matching_request(self, mock_client):
        response = MagicMock(stop_reason="end_turn")
        mock_client.messages.stream.return_value = _MockStream(["Sure!"], response)

        captured = {}

        def _observer(state):
            captured["active_agent"] = state.active_agent

        list(execute_task_stream(
            "Remember that I prefer dark mode.", on_state_created=_observer,
        ))

        self.assertEqual(captured["active_agent"], "memory")


if __name__ == "__main__":
    unittest.main()
