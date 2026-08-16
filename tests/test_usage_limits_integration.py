"""Integration tests for Phase 8 part 3 -- confirms agent/executor.py's
main loop and agent/research_agent.py's loop actually stop themselves once
agent/usage.py's check_request_limits reports a configured limit exceeded,
not just that the check function itself works in isolation (see
tests/test_usage.py for that). Only the network call itself is mocked,
matching this project's established policy (see
tests/test_executor_phase5_integration.py's docstring).

Run with: python -m unittest tests.test_usage_limits_integration -v
"""
import os
import tempfile
import unittest
from unittest.mock import MagicMock, patch

import tools.schemas  # noqa: F401 -- populates the registry
import agent.execution_history as execution_history
import agent.jarvis_state as jarvis_state
import agent.usage as usage
from agent.executor import execute_task_stream
from agent.research_agent import research
from config.settings import settings


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


def _tool_use_block(tool_id, name, tool_input):
    block = MagicMock()
    block.type = "tool_use"
    block.id = tool_id
    block.name = name
    block.input = tool_input
    return block


class IsolatedLimitsTestCase(unittest.TestCase):
    """Isolates every persisted store a limit check could either read from
    or write to, plus temporarily overrides the three Phase 8 part 3
    settings fields -- restored in tearDown regardless of test outcome, the
    same object.__setattr__-on-a-frozen-dataclass technique tests/
    test_usage.py already uses."""

    def setUp(self):
        self._real_history_file = execution_history.HISTORY_FILE
        self._real_state_file = jarvis_state.STATE_FILE
        self._real_usage_file = usage.USAGE_FILE
        execution_history.HISTORY_FILE = tempfile.mktemp(suffix=".json")
        jarvis_state.STATE_FILE = tempfile.mktemp(suffix=".json")
        usage.USAGE_FILE = tempfile.mktemp(suffix=".json")

        self._original_settings = {
            "max_requests_per_execution": settings.max_requests_per_execution,
            "max_tokens_per_request": settings.max_tokens_per_request,
            "max_cost_per_request_usd": settings.max_cost_per_request_usd,
        }

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
        for name, value in self._original_settings.items():
            object.__setattr__(settings, name, value)


class TestExecutorStopsOnRequestLimit(IsolatedLimitsTestCase):

    @patch("agent.executor.claude_client")
    def test_exceeding_max_requests_per_execution_stops_the_loop_early(self, mock_client):
        # Every mocked turn asks for the same safe, read-only, no-
        # confirmation tool again -- left unchecked this would run until
        # MAX_TOOL_ITERATIONS (8 by default). With the limit set to 2,
        # the loop must stop itself well before that.
        tool_use_response = MagicMock(stop_reason="tool_use")
        tool_use_response.content = [_tool_use_block("tu", "get_system_status", {})]
        mock_client.messages.stream.return_value = _MockStream([], tool_use_response)

        object.__setattr__(settings, "max_requests_per_execution", 2)

        chunks = list(execute_task_stream("check status repeatedly"))

        self.assertTrue(
            any("Stopping here" in chunk and "max_requests_per_execution" in chunk for chunk in chunks),
            chunks,
        )
        # Confirms it stopped early rather than exhausting the full 8-step
        # budget: only 2 model calls should have been recorded.
        self.assertEqual(mock_client.messages.stream.call_count, 2)

    @patch("agent.executor.claude_client")
    def test_under_the_limit_runs_to_normal_completion(self, mock_client):
        response = MagicMock(stop_reason="end_turn")
        mock_client.messages.stream.return_value = _MockStream(["Hi there!"], response)

        object.__setattr__(settings, "max_requests_per_execution", 25)

        chunks = list(execute_task_stream("say hi"))

        self.assertEqual("".join(chunks), "Hi there!")


class TestResearchAgentStopsOnRequestLimit(unittest.TestCase):

    def setUp(self):
        self._real_usage_file = usage.USAGE_FILE
        usage.USAGE_FILE = tempfile.mktemp(suffix=".json")
        self._original_limit = settings.max_requests_per_execution

    def tearDown(self):
        for path in (usage.USAGE_FILE, f"{usage.USAGE_FILE}.lock"):
            if os.path.exists(path):
                os.remove(path)
        usage.USAGE_FILE = self._real_usage_file
        object.__setattr__(settings, "max_requests_per_execution", self._original_limit)

    @patch("agent.research_agent.get_current_request_id", return_value="req-research-limit")
    @patch("agent.research_agent.anthropic_client")
    def test_exceeding_max_requests_per_execution_stops_research_loop(self, mock_client, mock_request_id):
        tool_use_response = MagicMock(stop_reason="tool_use")
        tool_use_response.content = [_tool_use_block("tu", "open_browser", {"target": "test query"})]
        mock_client.messages.create.return_value = tool_use_response

        object.__setattr__(settings, "max_requests_per_execution", 2)

        with patch("agent.research_agent.open_and_read", return_value="some page text"):
            result = research("What is the best laptop?")

        self.assertIn("Stopping the research here", result)
        self.assertIn("max_requests_per_execution", result)
        self.assertEqual(mock_client.messages.create.call_count, 2)


if __name__ == "__main__":
    unittest.main()
