"""Tests for agent/execution_state.py.

Run with: python -m unittest tests.test_execution_state -v
"""
import time
import unittest

from agent.execution_state import ExecutionState


class TestExecutionState(unittest.TestCase):

    def test_starts_at_iteration_zero_with_no_tools(self):
        state = ExecutionState(max_iterations=8)
        self.assertEqual(state.iteration, 0)
        self.assertEqual(state.tools_executed, [])
        self.assertFalse(state.failed)
        self.assertFalse(state.confirmation_pending)

    def test_record_iteration_increments(self):
        state = ExecutionState(max_iterations=8)
        state.record_iteration()
        state.record_iteration()
        self.assertEqual(state.iteration, 2)

    def test_record_tool_appends_in_order(self):
        state = ExecutionState(max_iterations=8)
        state.record_tool("get_weather")
        state.record_tool("add_reminder")
        self.assertEqual(state.tools_executed, ["get_weather", "add_reminder"])

    def test_record_model_sets_provider_and_model(self):
        state = ExecutionState(max_iterations=8)
        state.record_model("anthropic", "claude-sonnet-5")
        self.assertEqual(state.selected_provider, "anthropic")
        self.assertEqual(state.selected_model, "claude-sonnet-5")

    def test_finish_success_sets_result_and_not_failed(self):
        state = ExecutionState(max_iterations=8)
        state.finish(result="done")
        self.assertEqual(state.final_result, "done")
        self.assertFalse(state.failed)
        self.assertIsNone(state.error)
        self.assertIsNotNone(state.finished_at)

    def test_finish_failure_sets_failed_and_error(self):
        state = ExecutionState(max_iterations=8)
        state.finish(failed=True, error="boom")
        self.assertTrue(state.failed)
        self.assertEqual(state.error, "boom")

    def test_duration_increases_before_finish(self):
        state = ExecutionState(max_iterations=8)
        time.sleep(0.01)
        self.assertGreater(state.duration_seconds, 0)

    def test_duration_freezes_after_finish(self):
        state = ExecutionState(max_iterations=8)
        state.finish(result="done")
        first = state.duration_seconds
        time.sleep(0.01)
        second = state.duration_seconds
        self.assertEqual(first, second)

    def test_independent_instances_do_not_share_tools_list(self):
        # Regression guard for the classic mutable-default-argument bug --
        # tools_executed uses field(default_factory=list), so this must
        # stay independent per instance.
        state_a = ExecutionState(max_iterations=8)
        state_b = ExecutionState(max_iterations=8)
        state_a.record_tool("get_weather")
        self.assertEqual(state_b.tools_executed, [])


if __name__ == "__main__":
    unittest.main()
