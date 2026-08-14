"""Tests for agent/agents/models.py and agent/agents/base.py -- the
shared AgentResult dataclass and the Agent interface's default
can_handle() behavior.

Run with: python -m unittest tests.test_agents_base_and_models -v
"""
import unittest

from agent.agents.base import Agent, AgentMetadata
from agent.agents.models import AgentResult, AgentTaskType
from agent.request_context import RequestContext


class _MinimalAgent(Agent):
    @property
    def metadata(self) -> AgentMetadata:
        return AgentMetadata(name="minimal", description="d")

    def execute(self, task, context):
        return AgentResult(success=True, agent_name="minimal", request_id=context.request_id, result="")


class TestAgentResult(unittest.TestCase):

    def test_defaults(self):
        result = AgentResult(success=True, agent_name="x", request_id="abc", result="done")
        self.assertEqual(result.error, None)
        self.assertEqual(result.duration_seconds, 0.0)
        self.assertEqual(result.tools_used, [])
        self.assertEqual(result.metadata, {})
        self.assertFalse(result.cancelled)

    def test_is_frozen(self):
        result = AgentResult(success=True, agent_name="x", request_id="abc", result="done")
        with self.assertRaises(Exception):
            result.success = False


class TestAgentTaskType(unittest.TestCase):

    def test_values(self):
        self.assertEqual(AgentTaskType.CODING.value, "coding")
        self.assertEqual(AgentTaskType.RESEARCH.value, "research")
        self.assertEqual(AgentTaskType.QA.value, "qa")
        self.assertEqual(AgentTaskType.MEMORY.value, "memory")


class TestAgentMetadata(unittest.TestCase):

    def test_defaults(self):
        meta = AgentMetadata(name="x", description="d")
        self.assertTrue(meta.enabled)
        self.assertEqual(meta.priority, 0)
        self.assertEqual(meta.capabilities, [])


class TestAgentCanHandleDefault(unittest.TestCase):

    def setUp(self):
        self.agent = _MinimalAgent()

    def test_nonempty_task_can_be_handled(self):
        self.assertTrue(self.agent.can_handle("do something"))

    def test_empty_task_cannot_be_handled(self):
        self.assertFalse(self.agent.can_handle(""))

    def test_whitespace_only_cannot_be_handled(self):
        self.assertFalse(self.agent.can_handle("   "))

    def test_none_cannot_be_handled(self):
        self.assertFalse(self.agent.can_handle(None))

    def test_execute_returns_agent_result(self):
        ctx = RequestContext.create("do something", source="test")
        result = self.agent.execute("do something", ctx)
        self.assertIsInstance(result, AgentResult)


if __name__ == "__main__":
    unittest.main()
