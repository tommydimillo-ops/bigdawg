"""Tests for tools/schemas/agents.py's consult_coworker_agent -- the
real execution entry point for coworker agents, exercised through the
real tools.registry.dispatch() path, matching tests/test_registry.py's
established pattern. Underlying agent execution is mocked at the same
boundary as agent/agents/*'s own tests (research()/remember()/recall()),
so nothing here makes a real network call or memory write.

Run with: python -m unittest tests.test_agents_tool -v
"""
import unittest
from unittest.mock import patch

import tools.schemas  # noqa: F401 -- populates the registry
from agent.agents import manager
from tools import registry


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

    @patch("agent.agents.research.research")
    def test_research_agent_reachable_through_the_tool(self, mock_research):
        mock_research.return_value = "found some laptops"
        result = registry.dispatch(
            "consult_coworker_agent", {"agent_name": "research", "task": "best laptops under $1000"},
        )
        mock_research.assert_called_once()
        self.assertEqual(result, "found some laptops")

    @patch("agent.agents.memory.remember")
    def test_memory_agent_reachable_through_the_tool(self, mock_remember):
        mock_remember.return_value = "I'll remember that."
        result = registry.dispatch(
            "consult_coworker_agent", {"agent_name": "memory", "task": "remember that I prefer dark mode"},
        )
        mock_remember.assert_called_once()
        self.assertEqual(result, "I'll remember that.")

    def test_coding_agent_reports_deferred(self):
        result = registry.dispatch(
            "consult_coworker_agent", {"agent_name": "coding", "task": "fix this bug"},
        )
        self.assertIn("doesn't handle this directly yet", result)

    def test_qa_agent_defers_for_non_test_requests(self):
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

    @patch("agent.agents.research.research")
    def test_agent_exception_is_reported_not_raised(self, mock_research):
        mock_research.side_effect = RuntimeError("network down")
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
