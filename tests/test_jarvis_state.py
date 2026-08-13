"""Tests for agent/jarvis_state.py -- the cross-interface "what is Jarvis
doing right now" snapshot, shared via a JSON file since the Streamlit app,
the menu-bar app, and any future client are separate OS processes.

STATE_FILE is redirected to a temp file for every test here (module-wide,
matching this project's established pattern for every other file-backed
store's tests -- see tests/test_memory.py), so none of this ever touches
the real ~/Library/.../jarvis_state.json the live apps read from.

Run with: python -m unittest tests.test_jarvis_state -v
"""
import json
import os
import tempfile
import unittest

import agent.jarvis_state as jarvis_state
from agent.execution_state import ExecutionStatus


class IsolatedJarvisStateTestCase(unittest.TestCase):

    def setUp(self):
        self._real_state_file = jarvis_state.STATE_FILE
        jarvis_state.STATE_FILE = tempfile.mktemp(suffix=".json")

    def tearDown(self):
        for path in (jarvis_state.STATE_FILE, f"{jarvis_state.STATE_FILE}.tmp"):
            if os.path.exists(path):
                os.remove(path)
        jarvis_state.STATE_FILE = self._real_state_file


class TestDefaultState(IsolatedJarvisStateTestCase):

    def test_missing_file_returns_idle_default(self):
        state = jarvis_state.get_state()
        self.assertEqual(state.status, ExecutionStatus.IDLE.value)
        self.assertIsNone(state.active_request_id)
        self.assertFalse(state.confirmation_pending)

    def test_corrupt_file_degrades_to_idle_default_instead_of_raising(self):
        with open(jarvis_state.STATE_FILE, "w") as f:
            f.write("{not valid json")
        state = jarvis_state.get_state()
        self.assertEqual(state.status, ExecutionStatus.IDLE.value)


class TestSetStatus(IsolatedJarvisStateTestCase):

    def test_set_status_persists_all_fields(self):
        jarvis_state.set_status(
            ExecutionStatus.EXECUTING,
            active_request_id="req1",
            current_task="research laptops",
            current_tool="research_agent",
            plan_progress="1/3",
            confirmation_pending=False,
        )
        state = jarvis_state.get_state()
        self.assertEqual(state.status, "executing")
        self.assertEqual(state.active_request_id, "req1")
        self.assertEqual(state.current_task, "research laptops")
        self.assertEqual(state.current_tool, "research_agent")
        self.assertEqual(state.plan_progress, "1/3")

    def test_set_status_accepts_a_plain_string_too(self):
        jarvis_state.set_status("thinking", active_request_id="req2")
        self.assertEqual(jarvis_state.get_state().status, "thinking")

    def test_set_status_overwrites_previous_state_entirely(self):
        jarvis_state.set_status(ExecutionStatus.EXECUTING, current_tool="tool_a")
        jarvis_state.set_status(ExecutionStatus.THINKING)
        state = jarvis_state.get_state()
        self.assertEqual(state.status, "thinking")
        # A fresh set_status call is a full snapshot, not a merge -- the
        # previous call's current_tool must not leak into this one.
        self.assertIsNone(state.current_tool)

    def test_write_is_a_real_json_file_on_disk(self):
        jarvis_state.set_status(ExecutionStatus.EXECUTING, current_tool="get_weather")
        with open(jarvis_state.STATE_FILE) as f:
            raw = json.load(f)
        self.assertEqual(raw["status"], "executing")
        self.assertEqual(raw["current_tool"], "get_weather")


class TestResetToIdle(IsolatedJarvisStateTestCase):

    def test_reset_to_idle_clears_everything(self):
        jarvis_state.set_status(
            ExecutionStatus.EXECUTING, active_request_id="req1",
            current_tool="tool_a", confirmation_pending=True,
        )
        jarvis_state.reset_to_idle()
        state = jarvis_state.get_state()
        self.assertEqual(state.status, ExecutionStatus.IDLE.value)
        self.assertIsNone(state.active_request_id)
        self.assertIsNone(state.current_tool)
        self.assertFalse(state.confirmation_pending)


class TestIsBusy(IsolatedJarvisStateTestCase):

    def test_idle_is_not_busy(self):
        self.assertFalse(jarvis_state.is_busy())

    def test_executing_is_busy(self):
        jarvis_state.set_status(ExecutionStatus.EXECUTING)
        self.assertTrue(jarvis_state.is_busy())

    def test_thinking_is_busy(self):
        jarvis_state.set_status(ExecutionStatus.THINKING)
        self.assertTrue(jarvis_state.is_busy())

    def test_completed_is_not_busy(self):
        jarvis_state.set_status(ExecutionStatus.COMPLETED)
        self.assertFalse(jarvis_state.is_busy())

    def test_cancelled_is_not_busy(self):
        jarvis_state.set_status(ExecutionStatus.CANCELLED)
        self.assertFalse(jarvis_state.is_busy())

    def test_reset_to_idle_makes_it_not_busy_again(self):
        jarvis_state.set_status(ExecutionStatus.EXECUTING)
        self.assertTrue(jarvis_state.is_busy())
        jarvis_state.reset_to_idle()
        self.assertFalse(jarvis_state.is_busy())


class TestFromDictForwardCompatibility(IsolatedJarvisStateTestCase):

    def test_unknown_fields_in_the_file_are_ignored_not_fatal(self):
        with open(jarvis_state.STATE_FILE, "w") as f:
            json.dump({"status": "executing", "a_future_field": "whatever"}, f)
        state = jarvis_state.get_state()
        self.assertEqual(state.status, "executing")


if __name__ == "__main__":
    unittest.main()
