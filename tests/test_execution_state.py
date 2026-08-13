"""Tests for agent/execution_state.py.

Run with: python -m unittest tests.test_execution_state -v
"""
import time
import unittest

from agent.execution_state import (
    ExecutionState,
    cancel_active,
    get_active,
    register_active,
    unregister_active,
)
from agent.planner import Plan, PlanStep, StepStatus


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


class TestToolResultRecording(unittest.TestCase):

    def test_record_tool_with_preview_appends_a_record(self):
        state = ExecutionState(max_iterations=8)
        state.record_tool("get_weather", result_preview="Sunny, 75F", ok=True, duration_seconds=0.5)
        self.assertEqual(len(state.tool_results), 1)
        record = state.tool_results[0]
        self.assertEqual(record.name, "get_weather")
        self.assertEqual(record.result_preview, "Sunny, 75F")
        self.assertTrue(record.ok)
        self.assertEqual(record.duration_seconds, 0.5)

    def test_record_tool_without_preview_does_not_append_a_record(self):
        # Backward-compat call shape (Phase 2/3 code paths just pass the
        # name) -- should still work and just skip the richer record.
        state = ExecutionState(max_iterations=8)
        state.record_tool("get_weather")
        self.assertEqual(state.tool_results, [])
        self.assertEqual(state.tools_executed, ["get_weather"])

    def test_long_preview_is_truncated(self):
        state = ExecutionState(max_iterations=8)
        state.record_tool("get_weather", result_preview="x" * 500)
        self.assertLessEqual(len(state.tool_results[0].result_preview), 161)  # 160 + ellipsis


class TestMemoryRetrievalRecording(unittest.TestCase):

    def test_record_memory_retrieval(self):
        state = ExecutionState(max_iterations=8)
        state.record_memory_retrieval(
            memory_id="abc123", memory_type="pattern",
            reason="relevant to current request", score=4.5,
        )
        self.assertEqual(len(state.memories_retrieved), 1)
        ref = state.memories_retrieved[0]
        self.assertEqual(ref.memory_id, "abc123")
        self.assertEqual(ref.memory_type, "pattern")
        self.assertEqual(ref.score, 4.5)
        self.assertTrue(ref.included)

    def test_does_not_store_memory_content(self):
        # MemoryReference has no "content" field at all -- this is a
        # structural guarantee, not just a convention.
        state = ExecutionState(max_iterations=8)
        state.record_memory_retrieval(memory_id="x", memory_type="fact", reason="r")
        ref = state.memories_retrieved[0]
        self.assertFalse(hasattr(ref, "content"))


class TestConfirmationTracking(unittest.TestCase):

    def test_request_confirmation_sets_pending(self):
        state = ExecutionState(max_iterations=8)
        state.request_confirmation("run_python")
        self.assertTrue(state.confirmation_pending)
        self.assertEqual(state.pending_confirmation_tool, "run_python")

    def test_clear_confirmation_resets(self):
        state = ExecutionState(max_iterations=8)
        state.request_confirmation("run_python")
        state.clear_confirmation()
        self.assertFalse(state.confirmation_pending)
        self.assertIsNone(state.pending_confirmation_tool)


class TestPlanIntegration(unittest.TestCase):

    def _plan(self):
        steps = [PlanStep(step_id=i + 1, description=f"Step {i + 1}") for i in range(3)]
        steps[0].status = StepStatus.IN_PROGRESS
        return Plan(request="test", steps=steps)

    def test_no_plan_by_default(self):
        state = ExecutionState(max_iterations=8)
        self.assertIsNone(state.plan)

    def test_record_tool_advances_the_plan_when_one_is_attached(self):
        state = ExecutionState(max_iterations=8)
        state.plan = self._plan()
        state.record_tool("get_weather", result_preview="ok", ok=True)
        self.assertEqual(state.plan.completed_count, 1)
        self.assertEqual(state.plan.current_step.step_id, 2)

    def test_record_tool_failure_marks_plan_step_failed(self):
        state = ExecutionState(max_iterations=8)
        state.plan = self._plan()
        state.record_tool("get_weather", result_preview="error", ok=False)
        self.assertEqual(state.plan.failed_count, 1)

    def test_record_tool_without_preview_does_not_advance_plan(self):
        # No result_preview means this call site isn't reporting a
        # meaningful outcome -- shouldn't silently consume a plan step.
        state = ExecutionState(max_iterations=8)
        state.plan = self._plan()
        state.record_tool("get_weather")
        self.assertEqual(state.plan.completed_count, 0)


class TestCancellation(unittest.TestCase):

    def test_starts_not_cancelled(self):
        state = ExecutionState(max_iterations=8)
        self.assertFalse(state.cancelled)

    def test_cancel_sets_flag(self):
        state = ExecutionState(max_iterations=8)
        state.cancel()
        self.assertTrue(state.cancelled)


class TestActiveExecutionRegistry(unittest.TestCase):

    def tearDown(self):
        unregister_active("test-request-id")

    def test_register_and_get(self):
        state = ExecutionState(max_iterations=8)
        register_active("test-request-id", state)
        self.assertIs(get_active("test-request-id"), state)

    def test_unregister_removes_it(self):
        state = ExecutionState(max_iterations=8)
        register_active("test-request-id", state)
        unregister_active("test-request-id")
        self.assertIsNone(get_active("test-request-id"))

    def test_get_unknown_id_returns_none(self):
        self.assertIsNone(get_active("no-such-request-id"))

    def test_cancel_active_by_id(self):
        state = ExecutionState(max_iterations=8)
        register_active("test-request-id", state)
        result = cancel_active("test-request-id")
        self.assertTrue(result)
        self.assertTrue(state.cancelled)

    def test_cancel_active_unknown_id_returns_false(self):
        self.assertFalse(cancel_active("no-such-request-id"))


if __name__ == "__main__":
    unittest.main()
