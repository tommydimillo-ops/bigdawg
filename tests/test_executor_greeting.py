"""Integration tests for the "Say hi" structural fix wired into
agent/executor.py's execute_task_stream (ROADMAP.md's "Say hi" entry):
for a detected bare greeting, get_system_status + get_weather are pre-run
*before* the first model completion and their results are placed in the
system prompt, so the model produces one reply with the data in hand
instead of a narrate-then-tool-call round trip.

Exercised through the real loop with only the network call
(claude_client) and the tool-dispatch seam (_run_tool) mocked -- the same
"don't require paid API calls in the automated suite" policy the rest of
this project's executor tests follow.

Run with: python -m unittest tests.test_executor_greeting -v
"""
import os
import tempfile
import unittest
from unittest.mock import MagicMock, patch

import tools.schemas  # noqa: F401 -- populates the registry
import agent.execution_history as execution_history
import agent.history_store as history_store
import agent.jarvis_state as jarvis_state
import agent.usage as usage
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


_STATUS = "Battery: 82% | Disk free: 40 GB | Wi-Fi: HomeNet | Uptime: 3h"
_WEATHER = "Location: Tampa, Florida\nNow: 88°F, Partly cloudy (feels like 95°F)"
_WEATHER_FAILED = "Couldn't fetch weather: Expecting value: line 1 column 1 (char 0)"


def _fake_run_tool(results):
    """A _run_tool stand-in returning canned strings by tool name."""
    calls = []

    def _inner(name, tool_input, source="chat", context=None, state=None):
        calls.append(name)
        return results[name]

    _inner.calls = calls
    return _inner


class IsolatedExecutorTestCase(unittest.TestCase):
    def setUp(self):
        self._real_history_file = execution_history.HISTORY_FILE
        self._real_state_file = jarvis_state.STATE_FILE
        self._real_usage_file = usage.USAGE_FILE
        self._real_history_db = history_store.HISTORY_DB
        execution_history.HISTORY_FILE = tempfile.mktemp(suffix=".json")
        jarvis_state.STATE_FILE = tempfile.mktemp(suffix=".json")
        usage.USAGE_FILE = tempfile.mktemp(suffix=".json")
        history_store.HISTORY_DB = tempfile.mktemp(suffix=".db")

    def tearDown(self):
        for path in (
            execution_history.HISTORY_FILE, f"{execution_history.HISTORY_FILE}.tmp",
            jarvis_state.STATE_FILE, f"{jarvis_state.STATE_FILE}.tmp",
            usage.USAGE_FILE, f"{usage.USAGE_FILE}.lock",
            history_store.HISTORY_DB, f"{history_store.HISTORY_DB}-wal", f"{history_store.HISTORY_DB}-shm",
        ):
            if os.path.exists(path):
                os.remove(path)
        execution_history.HISTORY_FILE = self._real_history_file
        jarvis_state.STATE_FILE = self._real_state_file
        usage.USAGE_FILE = self._real_usage_file
        history_store.HISTORY_DB = self._real_history_db

    def _system_text(self, mock_client):
        system = mock_client.messages.stream.call_args.kwargs["system"]
        # _run_claude_loop_stream passes a one-element list of text blocks.
        return system[0]["text"] if isinstance(system, list) else system


class TestGreetingPrefetch(IsolatedExecutorTestCase):

    @patch("agent.executor.claude_client")
    def test_bare_greeting_prefetches_and_injects_and_makes_one_model_call(self, mock_client):
        mock_client.messages.stream.return_value = _MockStream(
            ["Hello, master. The current time is 2:00 PM..."],
            MagicMock(stop_reason="end_turn"),
        )
        fake = _fake_run_tool({"get_system_status": _STATUS, "get_weather": _WEATHER})

        with patch("agent.executor._run_tool", fake):
            chunks = list(execute_task_stream("hi"))

        self.assertEqual(fake.calls, ["get_system_status", "get_weather"])
        self.assertEqual(mock_client.messages.stream.call_count, 1)
        system_text = self._system_text(mock_client)
        self.assertIn("GREETING —", system_text)
        self.assertIn("Location: Tampa, Florida", system_text)
        self.assertIn("Battery: 82%", system_text)
        self.assertTrue("".join(chunks).startswith("Hello, master."))

    @patch("agent.executor.claude_client")
    def test_non_greeting_does_not_prefetch_or_inject(self, mock_client):
        mock_client.messages.stream.return_value = _MockStream(
            ["4"], MagicMock(stop_reason="end_turn"),
        )
        fake = _fake_run_tool({"get_system_status": _STATUS, "get_weather": _WEATHER})

        with patch("agent.executor._run_tool", fake):
            list(execute_task_stream("what is 2 + 2"))

        self.assertEqual(fake.calls, [])
        self.assertNotIn("GREETING —", self._system_text(mock_client))

    @patch("agent.executor.claude_client")
    def test_weather_fetch_failure_falls_back_to_ordinary_path(self, mock_client):
        mock_client.messages.stream.return_value = _MockStream(
            ["Hello, master."], MagicMock(stop_reason="end_turn"),
        )
        fake = _fake_run_tool({"get_system_status": _STATUS, "get_weather": _WEATHER_FAILED})

        with patch("agent.executor._run_tool", fake):
            chunks = list(execute_task_stream("hey"))

        # It still tried both, but the failed weather result means no
        # block is injected -- the model would fetch weather itself.
        self.assertEqual(fake.calls, ["get_system_status", "get_weather"])
        self.assertNotIn("GREETING —", self._system_text(mock_client))
        self.assertEqual("".join(chunks), "Hello, master.")

    @patch("agent.executor.claude_client")
    def test_prefetch_tool_exception_is_swallowed(self, mock_client):
        mock_client.messages.stream.return_value = _MockStream(
            ["Hello, master."], MagicMock(stop_reason="end_turn"),
        )

        def _boom(name, tool_input, source="chat", context=None, state=None):
            raise RuntimeError("tool blew up")

        with patch("agent.executor._run_tool", _boom):
            chunks = list(execute_task_stream("good morning"))

        self.assertEqual("".join(chunks), "Hello, master.")
        self.assertNotIn("GREETING —", self._system_text(mock_client))

    @patch("agent.executor.claude_client")
    def test_scheduled_source_never_prefetches(self, mock_client):
        mock_client.messages.stream.return_value = _MockStream(
            ["Hello, master."], MagicMock(stop_reason="end_turn"),
        )
        fake = _fake_run_tool({"get_system_status": _STATUS, "get_weather": _WEATHER})

        with patch("agent.executor._run_tool", fake):
            list(execute_task_stream("hi", source="scheduled"))

        self.assertEqual(fake.calls, [])
        self.assertNotIn("GREETING —", self._system_text(mock_client))


if __name__ == "__main__":
    unittest.main()
