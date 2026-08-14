"""Tests for tools/schemas/agents.py's consult_coworker_agent -- the
real execution entry point for coworker agents, exercised through the
real tools.registry.dispatch() path, matching tests/test_registry.py's
established pattern. Since Phase 8 part 4, real agent execution goes
through agent.agents.manager.execute_agent (a subprocess launch) rather
than an agent's own execute() being called directly in-process -- so
that's the boundary these tests mock (see tools.schemas.agents.
execute_agent, patched here as imported into that module), matching this
project's established "mock at the external-call boundary" policy the
same way execute_agent's own tests (tests/test_agents_manager.py) mock
subprocess.run rather than reaching further in. Nothing here spawns a
real subprocess, makes a real network call, or writes to real memory.

Run with: python -m unittest tests.test_agents_tool -v
"""
import unittest
from unittest.mock import ANY, patch

import tools.schemas  # noqa: F401 -- populates the registry
from agent.agents import manager
from agent.agents.models import AgentResult
from tools import registry


def _result(agent_name, success=True, result="", error=None, metadata=None):
    return AgentResult(
        success=success, agent_name=agent_name, request_id="req-1",
        result=result, error=error, metadata=metadata or {},
    )


class TestToolIsRegistered(unittest.TestCase):

    def test_registered(self):
        self.assertIn("consult_coworker_agent", registry.all_names())

    def test_permission_level(self):
        self.assertEqual(registry.permission_level("consult_coworker_agent"), 1)


class TestConsultCoworkerAgent(unittest.TestCase):

    def test_unknown_agent_name_is_rejected(self):
        result = registry.dispatch("consult_coworker_agent", {"agent_name": "not-a-real-agent", "task": "x"})
        self.assertIn("Unknown agent", result)

    def test_missing_task_is_rejected(self):
        result = registry.dispatch("consult_coworker_agent", {"agent_name": "research", "task": ""})
        self.assertIn("task description is required", result)

    @patch("tools.schemas.agents.execute_agent")
    def test_research_agent_reachable_through_the_tool(self, mock_execute):
        mock_execute.return_value = _result("research", result="found some laptops")
        result = registry.dispatch(
            "consult_coworker_agent", {"agent_name": "research", "task": "best laptops under $1000"},
        )
        mock_execute.assert_called_once_with("research", "best laptops under $1000", ANY)
        self.assertEqual(result, "found some laptops")

    @patch("tools.schemas.agents.execute_agent")
    def test_memory_agent_reachable_through_the_tool(self, mock_execute):
        mock_execute.return_value = _result("memory", result="I'll remember that.")
        result = registry.dispatch(
            "consult_coworker_agent", {"agent_name": "memory", "task": "remember that I prefer dark mode"},
        )
        mock_execute.assert_called_once()
        self.assertEqual(result, "I'll remember that.")

    @patch("tools.schemas.agents.execute_agent")
    def test_coding_agent_reports_deferred(self, mock_execute):
        mock_execute.return_value = _result("coding", metadata={"deferred_to_executor": True})
        result = registry.dispatch(
            "consult_coworker_agent", {"agent_name": "coding", "task": "fix this bug"},
        )
        self.assertIn("doesn't handle this directly yet", result)

    @patch("tools.schemas.agents.execute_agent")
    def test_qa_agent_defers_for_non_test_requests(self, mock_execute):
        mock_execute.return_value = _result("qa", metadata={"deferred_to_executor": True})
        result = registry.dispatch(
            "consult_coworker_agent", {"agent_name": "qa", "task": "verify the email was sent"},
        )
        self.assertIn("doesn't handle this directly yet", result)

    def test_disabled_agent_is_rejected(self):
        from agent.agents.base import Agent, AgentMetadata

        class _DisabledFakeAgent(Agent):
            @property
            def metadata(self):
                return AgentMetadata(name="research", description="d", enabled=False)

            def execute(self, task, context):
                raise AssertionError("must never execute a disabled agent")

        real_agent = manager.get("research")
        manager.unregister("research")
        manager.register(_DisabledFakeAgent())
        try:
            result = registry.dispatch(
                "consult_coworker_agent", {"agent_name": "research", "task": "x"},
            )
            self.assertIn("disabled", result)
        finally:
            manager.unregister("research")
            manager.register(real_agent)

    @patch("tools.schemas.agents.execute_agent")
    def test_agent_exception_is_reported_not_raised(self, mock_execute):
        # execute_agent itself is what turns an in-subprocess exception
        # (or a timeout, or a crash) into a failed AgentResult rather than
        # letting anything raise -- see tests/test_agents_manager.py's
        # TestExecuteAgent for that. This test only proves the tool
        # correctly surfaces a failed result as "could not complete".
        mock_execute.return_value = _result("research", success=False, error="RuntimeError: network down")
        result = registry.dispatch(
            "consult_coworker_agent", {"agent_name": "research", "task": "best laptops"},
        )
        self.assertIn("could not complete", result)

    def test_no_unrestricted_shell_access(self):
        # Structural guarantee: the tool's own input schema has no field
        # that could plausibly be interpreted as a shell command --
        # confirms this stays a task-description-only interface.
        spec = registry.get("consult_coworker_agent")
        self.assertEqual(set(spec.input_schema["properties"].keys()), {"agent_name", "task"})


if __name__ == "__main__":
    unittest.main()
