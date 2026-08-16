"""Tests for tools/schemas/agents.py's delegate_parallel_tasks -- the
real tool-registry entry point for Phase 9 Milestone 3's bounded-parallel
coworker delegation, exercised through tools.registry.dispatch() exactly
like tests/test_agents_tool.py does for the single-task
consult_coworker_agent. Mocks at the same execute_agents_parallel
boundary those tests mock execute_agent at -- nothing here spawns a real
subprocess or makes a real network call.

Run with: python -m unittest tests.test_agents_tool_batch -v
"""
import unittest
from unittest.mock import patch

import tools.schemas  # noqa: F401 -- populates the registry
from agent.agents.models import AgentBatchItem, AgentBatchResult, AgentResult, BatchStatus
from agent.execution_state import ExecutionState, register_active, unregister_active
from tools import registry


def _batch(status=BatchStatus.ALL_SUCCEEDED, items=None, note="all subtasks succeeded", cost_usd=None):
    return AgentBatchResult(
        status=status, items=items or [], request_id="req-1", note=note, cost_usd=cost_usd,
    )


def _item(agent_name, success=True, required=True, retried=False, result_text="ok", error=None):
    return AgentBatchItem(
        agent_name=agent_name, task_preview="preview", required=required, retried=retried,
        result=AgentResult(
            success=success, agent_name=agent_name, request_id="req-1",
            result=result_text if success else "", error=error,
        ),
    )


class TestToolIsRegistered(unittest.TestCase):

    def test_registered(self):
        self.assertIn("delegate_parallel_tasks", registry.all_names())

    def test_permission_level(self):
        self.assertEqual(registry.permission_level("delegate_parallel_tasks"), 1)

    def test_not_parallel_safe(self):
        # Deliberately excluded from tools.registry's generic
        # parallel_safe mechanism -- it already bounds its own internal
        # concurrency; being parallel_safe too would let the model call
        # it more than once in one turn and multiply concurrent
        # subprocesses past the configured ceiling.
        self.assertNotIn("delegate_parallel_tasks", registry.parallel_safe_tools())


class TestInputValidation(unittest.TestCase):

    def test_single_task_is_rejected(self):
        result = registry.dispatch("delegate_parallel_tasks", {
            "tasks": [{"agent_name": "research", "task": "a"}],
        })
        self.assertIn("At least two", result)

    def test_empty_tasks_is_rejected(self):
        result = registry.dispatch("delegate_parallel_tasks", {"tasks": []})
        self.assertIn("At least two", result)

    def test_missing_tasks_key_is_rejected(self):
        result = registry.dispatch("delegate_parallel_tasks", {})
        self.assertIn("At least two", result)

    def test_unknown_agent_name_is_rejected(self):
        result = registry.dispatch("delegate_parallel_tasks", {
            "tasks": [
                {"agent_name": "not-a-real-agent", "task": "a"},
                {"agent_name": "research", "task": "b"},
            ],
        })
        self.assertIn("Unknown agent", result)

    def test_missing_task_text_is_rejected(self):
        result = registry.dispatch("delegate_parallel_tasks", {
            "tasks": [
                {"agent_name": "research", "task": ""},
                {"agent_name": "memory", "task": "b"},
            ],
        })
        self.assertIn("task description", result)

    @patch("tools.schemas.agents.settings")
    def test_batch_larger_than_configured_max_is_rejected_before_dispatch(self, mock_settings):
        mock_settings.max_parallel_agents = 2
        result = registry.dispatch("delegate_parallel_tasks", {
            "tasks": [
                {"agent_name": "research", "task": "a"},
                {"agent_name": "memory", "task": "b"},
                {"agent_name": "qa", "task": "c"},
            ],
        })
        self.assertIn("Rejected", result)
        self.assertIn("exceeds", result)


class TestSuccessfulDispatch(unittest.TestCase):

    @patch("tools.schemas.agents.execute_agents_parallel")
    def test_two_task_batch_reaches_execute_agents_parallel(self, mock_execute):
        mock_execute.return_value = _batch(items=[_item("research"), _item("memory")])
        result = registry.dispatch("delegate_parallel_tasks", {
            "tasks": [
                {"agent_name": "research", "task": "research X"},
                {"agent_name": "memory", "task": "remember Y"},
            ],
        })
        mock_execute.assert_called_once()
        tasks_arg = mock_execute.call_args[0][0]
        self.assertEqual([t.agent_name for t in tasks_arg], ["research", "memory"])
        self.assertEqual([t.task for t in tasks_arg], ["research X", "remember Y"])
        self.assertIn("all_succeeded", result)

    @patch("tools.schemas.agents.execute_agents_parallel")
    def test_required_flag_defaults_true(self, mock_execute):
        mock_execute.return_value = _batch(items=[_item("research"), _item("memory")])
        registry.dispatch("delegate_parallel_tasks", {
            "tasks": [
                {"agent_name": "research", "task": "a"},
                {"agent_name": "memory", "task": "b"},
            ],
        })
        tasks_arg = mock_execute.call_args[0][0]
        self.assertTrue(all(t.required for t in tasks_arg))

    @patch("tools.schemas.agents.execute_agents_parallel")
    def test_required_flag_can_be_set_false(self, mock_execute):
        mock_execute.return_value = _batch(items=[_item("research"), _item("memory")])
        registry.dispatch("delegate_parallel_tasks", {
            "tasks": [
                {"agent_name": "research", "task": "a", "required": False},
                {"agent_name": "memory", "task": "b"},
            ],
        })
        tasks_arg = mock_execute.call_args[0][0]
        self.assertFalse(tasks_arg[0].required)
        self.assertTrue(tasks_arg[1].required)

    @patch("tools.schemas.agents.execute_agents_parallel")
    def test_failed_subtask_is_represented_in_the_report(self, mock_execute):
        mock_execute.return_value = _batch(
            status=BatchStatus.PARTIAL,
            items=[_item("research"), _item("memory", success=False, error="RuntimeError: boom")],
            note="failed/unverified: memory",
        )
        result = registry.dispatch("delegate_parallel_tasks", {
            "tasks": [{"agent_name": "research", "task": "a"}, {"agent_name": "memory", "task": "b"}],
        })
        self.assertIn("partial", result)
        self.assertIn("memory", result)
        self.assertIn("FAILED", result)

    @patch("tools.schemas.agents.execute_agents_parallel")
    def test_cost_is_included_when_available(self, mock_execute):
        mock_execute.return_value = _batch(items=[_item("research"), _item("memory")], cost_usd=0.0123)
        result = registry.dispatch("delegate_parallel_tasks", {
            "tasks": [{"agent_name": "research", "task": "a"}, {"agent_name": "memory", "task": "b"}],
        })
        self.assertIn("0.0123", result)


class TestExecutionStateWiring(unittest.TestCase):
    """delegate_parallel_tasks recovers the live ExecutionState via the
    contextvar-based request_id pattern (see CLAUDE.md's note on this
    convention) -- confirms it actually updates the state a caller
    (dashboard, voice UI) would observe, the same way
    tests/test_agents_executor_integration.py confirms the singular
    active_agent field gets set for the single-task path."""

    def setUp(self):
        self.state = ExecutionState(max_iterations=8)
        register_active("req-batch-1", self.state)

    def tearDown(self):
        unregister_active("req-batch-1")

    @patch("tools.schemas.agents.get_current_request_id", return_value="req-batch-1")
    @patch("tools.schemas.agents.execute_agents_parallel")
    def test_batch_started_and_finished_update_the_live_execution_state(self, mock_execute, mock_request_id):
        mock_execute.return_value = _batch(items=[_item("research"), _item("memory")])
        registry.dispatch("delegate_parallel_tasks", {
            "tasks": [{"agent_name": "research", "task": "a"}, {"agent_name": "memory", "task": "b"}],
        })
        self.assertEqual(self.state.completed_agents, ["research", "memory"])
        self.assertEqual(self.state.failed_agents, [])
        self.assertEqual(self.state.verification_status, "all_succeeded")
        self.assertEqual(self.state.active_agents, [])

    @patch("tools.schemas.agents.get_current_request_id", return_value="req-batch-1")
    @patch("tools.schemas.agents.execute_agents_parallel")
    def test_partial_failure_is_reflected_in_failed_agents(self, mock_execute, mock_request_id):
        mock_execute.return_value = _batch(
            status=BatchStatus.PARTIAL,
            items=[_item("research"), _item("memory", success=False, error="boom")],
        )
        registry.dispatch("delegate_parallel_tasks", {
            "tasks": [{"agent_name": "research", "task": "a"}, {"agent_name": "memory", "task": "b"}],
        })
        self.assertEqual(self.state.completed_agents, ["research"])
        self.assertEqual(self.state.failed_agents, ["memory"])

    @patch("tools.schemas.agents.get_current_request_id", return_value="req-does-not-exist")
    @patch("tools.schemas.agents.execute_agents_parallel")
    def test_missing_execution_state_does_not_raise(self, mock_execute, mock_request_id):
        # No ExecutionState registered for this id -- must degrade
        # gracefully (e.g. a direct/test call outside execute_task_stream)
        # rather than raising, the same convention consult_coworker_agent
        # already follows.
        mock_execute.return_value = _batch(items=[_item("research"), _item("memory")])
        result = registry.dispatch("delegate_parallel_tasks", {
            "tasks": [{"agent_name": "research", "task": "a"}, {"agent_name": "memory", "task": "b"}],
        })
        self.assertIn("all_succeeded", result)


class TestNoUnrestrictedAccess(unittest.TestCase):

    def test_schema_only_exposes_agent_name_task_and_required(self):
        spec = registry.get("delegate_parallel_tasks")
        item_props = set(spec.input_schema["properties"]["tasks"]["items"]["properties"].keys())
        self.assertEqual(item_props, {"agent_name", "task", "required"})

    def test_agent_name_is_constrained_to_the_valid_enum(self):
        spec = registry.get("delegate_parallel_tasks")
        enum = spec.input_schema["properties"]["tasks"]["items"]["properties"]["agent_name"]["enum"]
        self.assertEqual(set(enum), {"coding", "research", "qa", "memory"})


if __name__ == "__main__":
    unittest.main()
