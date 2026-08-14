"""Tests for agent/claude_gateway.py -- a thin, explicit interface for
invoking Claude with a skill's instructions, built entirely on the
existing execute_task_stream() (mocked here, matching this project's
established policy of not requiring paid API calls in the automated
suite -- see tests/test_planner.py's docstring).

Run with: python -m unittest tests.test_claude_gateway -v
"""
import os
import tempfile
import unittest
from unittest.mock import MagicMock, patch

import agent.execution_history as execution_history
import agent.jarvis_state as jarvis_state
import agent.claude_gateway as claude_gateway


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
        self._chunks, self._final_message = chunks, final_message

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        return False

    @property
    def text_stream(self):
        return iter(self._chunks)

    def get_final_message(self):
        return self._final_message


class TestInvoke(IsolatedExecutorTestCase):

    @patch("agent.executor.claude_client")
    def test_invoke_without_skill_runs_normally(self, mock_client):
        response = MagicMock(stop_reason="end_turn")
        mock_client.messages.stream.return_value = _MockStream(["hi"], response)

        chunks = list(claude_gateway.invoke("say hi"))

        self.assertEqual("".join(chunks), "hi")

    @patch("agent.executor.claude_client")
    def test_invoke_with_skill_sets_state_fields(self, mock_client):
        response = MagicMock(stop_reason="end_turn")
        mock_client.messages.stream.return_value = _MockStream(["ok"], response)

        captured = {}
        list(claude_gateway.invoke(
            "research this", skill_name="research",
            on_state_created=lambda state: captured.update(state=state),
        ))

        self.assertEqual(captured["state"].selected_skill, "research")
        self.assertEqual(captured["state"].delegation_destination, "claude_skill")

    @patch("agent.executor.claude_client")
    def test_invoke_without_skill_name_leaves_selected_skill_none(self, mock_client):
        response = MagicMock(stop_reason="end_turn")
        mock_client.messages.stream.return_value = _MockStream(["ok"], response)

        captured = {}
        list(claude_gateway.invoke(
            "say hi", on_state_created=lambda state: captured.update(state=state),
        ))

        self.assertIsNone(captured["state"].selected_skill)

    @patch("agent.executor.claude_client")
    def test_source_defaults_to_chat(self, mock_client):
        import ast
        import inspect
        tree = ast.parse(inspect.getsource(claude_gateway.invoke))
        source_default = None
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name == "invoke":
                for arg, default in zip(node.args.args[-len(node.args.defaults):], node.args.defaults):
                    if arg.arg == "source":
                        source_default = default.value
        self.assertEqual(source_default, "chat")


if __name__ == "__main__":
    unittest.main()
