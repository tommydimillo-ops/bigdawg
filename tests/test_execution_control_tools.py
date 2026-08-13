"""Tests for tools/schemas/execution_control.py -- the view_task_history
and cancel_request tools that give the model (and, via the same registry,
the dashboard) visibility into and control over Jarvis's own execution.

Exercises the real tools.registry.dispatch() path, same as
tests/test_registry.py does for the rest of the tool set. HISTORY_FILE is
redirected to a fresh temp file before every test (matching every other
file-backed store's tests in this project) so this never touches the real
~/Library/.../execution_history.json, and so one test's recorded history
never leaks into the next test's "is it empty" assertions.

Run with: python -m unittest tests.test_execution_control_tools -v
"""
import os
import tempfile
import unittest

import tools.schemas  # noqa: F401 -- populates the registry
import agent.execution_history as execution_history
from agent.execution_state import ExecutionState, register_active, unregister_active
from tools import registry


class IsolatedHistoryFileTestCase(unittest.TestCase):

    def setUp(self):
        self._real_history_file = execution_history.HISTORY_FILE
        execution_history.HISTORY_FILE = tempfile.mktemp(suffix=".json")

    def tearDown(self):
        for path in (execution_history.HISTORY_FILE, f"{execution_history.HISTORY_FILE}.tmp"):
            if os.path.exists(path):
                os.remove(path)
        execution_history.HISTORY_FILE = self._real_history_file


class TestToolsAreRegistered(unittest.TestCase):

    def test_view_task_history_is_registered(self):
        self.assertIn("view_task_history", registry.all_names())

    def test_cancel_request_is_registered(self):
        self.assertIn("cancel_request", registry.all_names())

    def test_view_task_history_is_read_only(self):
        self.assertEqual(registry.permission_level("view_task_history"), 0)

    def test_cancel_request_is_not_a_hard_gated_tool(self):
        # Cancellation itself isn't a dangerous or consequential action --
        # it should never require a live confirmation or be blocked
        # unattended (a scheduled task should be able to cancel another).
        self.assertFalse(registry.requires_live_confirmation("cancel_request"))
        self.assertTrue(registry.unattended_allowed("cancel_request"))


class TestViewTaskHistory(IsolatedHistoryFileTestCase):

    def test_reports_nothing_active_and_nothing_recent_when_empty(self):
        result = registry.dispatch("view_task_history", {})
        self.assertIn("No requests are currently active", result)
        self.assertIn("No past executions recorded yet", result)

    def test_reports_a_real_active_execution(self):
        state = ExecutionState(max_iterations=8)
        register_active("view-history-active-test", state)
        try:
            result = registry.dispatch("view_task_history", {})
            self.assertIn("view-history-active-test", result)
        finally:
            unregister_active("view-history-active-test")

    def test_reports_recent_completed_executions(self):
        state = ExecutionState(max_iterations=8)
        state.finish(result="ok")
        execution_history.record_completed("hist1", "check the weather", state)
        result = registry.dispatch("view_task_history", {})
        self.assertIn("check the weather", result)
        self.assertIn("completed", result)

    def test_limit_caps_the_number_of_recent_entries_shown(self):
        for i in range(5):
            state = ExecutionState(max_iterations=8)
            state.finish(result="ok")
            execution_history.record_completed(f"hist-limit-{i}", f"distinct request {i}", state)
        result = registry.dispatch("view_task_history", {"limit": 2})
        self.assertEqual(result.count("distinct request "), 2)

    def test_non_integer_limit_falls_back_to_default_instead_of_raising(self):
        result = registry.dispatch("view_task_history", {"limit": "not a number"})
        self.assertIsInstance(result, str)


class TestCancelRequest(unittest.TestCase):

    def test_missing_request_id_returns_a_clear_message_not_an_error(self):
        result = registry.dispatch("cancel_request", {})
        self.assertIn("request_id is required", result)

    def test_unknown_request_id_reports_no_active_request(self):
        result = registry.dispatch("cancel_request", {"request_id": "not-real"})
        self.assertIn("No active request found", result)

    def test_cancels_a_real_active_request(self):
        state = ExecutionState(max_iterations=8)
        register_active("cancel-tool-test", state)
        try:
            result = registry.dispatch("cancel_request", {"request_id": "cancel-tool-test"})
            self.assertIn("Cancellation requested", result)
            self.assertTrue(state.cancelled)
        finally:
            unregister_active("cancel-tool-test")


if __name__ == "__main__":
    unittest.main()
