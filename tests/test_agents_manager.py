"""Tests for agent/agents/manager.py -- the registry (register/
unregister/list/find) and route_and_execute's failure handling (unknown
agent, disabled agent, exception, timeout, cancellation, recursion
depth). Uses fake, minimal Agent implementations throughout -- no real
network calls, no real Speech/tool access, matching this project's
established policy of mocking at the external-call boundary.

Run with: python -m unittest tests.test_agents_manager -v
"""
import time
import unittest
from unittest.mock import patch

from agent.agents import manager
from agent.agents.base import Agent, AgentMetadata
from agent.agents.models import AgentResult
from agent.request_context import RequestContext


class _FakeAgent(Agent):
    """A minimal, fully-controllable Agent for testing the manager
    itself, independent of any real agent's behavior."""

    def __init__(self, name="fake", enabled=True, result_text="ok", raises=None, sleep_seconds=0.0):
        self._name = name
        self._enabled = enabled
        self._result_text = result_text
        self._raises = raises
        self._sleep_seconds = sleep_seconds
        self.executed_with = None

    @property
    def metadata(self) -> AgentMetadata:
        return AgentMetadata(name=self._name, description="test agent", enabled=self._enabled)

    def execute(self, task, context):
        self.executed_with = (task, context)
        if self._sleep_seconds:
            time.sleep(self._sleep_seconds)
        if self._raises:
            raise self._raises
        return AgentResult(success=True, agent_name=self._name, request_id=context.request_id, result=self._result_text)


class IsolatedRegistryTestCase(unittest.TestCase):

    def setUp(self):
        self._real_registry = dict(manager._REGISTRY)
        manager.clear()

    def tearDown(self):
        manager.clear()
        manager._REGISTRY.update(self._real_registry)


class TestRegistry(IsolatedRegistryTestCase):

    def test_register_and_get(self):
        agent = _FakeAgent(name="alpha")
        manager.register(agent)
        self.assertIs(manager.get("alpha"), agent)

    def test_register_duplicate_name_raises(self):
        manager.register(_FakeAgent(name="alpha"))
        with self.assertRaises(ValueError):
            manager.register(_FakeAgent(name="alpha"))

    def test_unregister(self):
        manager.register(_FakeAgent(name="alpha"))
        manager.unregister("alpha")
        self.assertIsNone(manager.get("alpha"))

    def test_unregister_unknown_is_a_safe_no_op(self):
        manager.unregister("does-not-exist")  # must not raise

    def test_get_unknown_returns_none(self):
        self.assertIsNone(manager.get("nope"))

    def test_list_agents(self):
        manager.register(_FakeAgent(name="alpha"))
        manager.register(_FakeAgent(name="beta"))
        names = {m.name for m in manager.list_agents()}
        self.assertEqual(names, {"alpha", "beta"})

    def test_available_agents_excludes_disabled(self):
        manager.register(_FakeAgent(name="alpha", enabled=True))
        manager.register(_FakeAgent(name="beta", enabled=False))
        names = {m.name for m in manager.available_agents()}
        self.assertEqual(names, {"alpha"})


class TestRouteAndExecute(IsolatedRegistryTestCase):

    def _context(self, text):
        return RequestContext.create(text, source="test")

    @patch("agent.agents.manager.route")
    def test_direct_returns_none(self, mock_route):
        from agent.agents.router import AgentDecision, AgentDestination
        mock_route.return_value = AgentDecision(destination=AgentDestination.DIRECT, reason="no match")
        result = manager.route_and_execute("what's 2+2?", self._context("what's 2+2?"))
        self.assertIsNone(result)

    def test_unknown_agent_fails_safely_returns_none(self):
        # "research" is routed to but never registered here.
        result = manager.route_and_execute("Research the best laptops.", self._context("Research the best laptops."))
        self.assertIsNone(result)

    def test_disabled_agent_fails_safely_returns_none(self):
        manager.register(_FakeAgent(name="research", enabled=False))
        result = manager.route_and_execute("Research the best laptops.", self._context("Research the best laptops."))
        self.assertIsNone(result)

    def test_successful_execution_returns_result(self):
        agent = _FakeAgent(name="research", result_text="found some laptops")
        manager.register(agent)
        result = manager.route_and_execute("Research the best laptops.", self._context("Research the best laptops."))
        self.assertIsNotNone(result)
        self.assertTrue(result.success)
        self.assertEqual(result.result, "found some laptops")

    def test_agent_exception_is_caught_and_reported(self):
        manager.register(_FakeAgent(name="research", raises=RuntimeError("boom")))
        result = manager.route_and_execute("Research the best laptops.", self._context("Research the best laptops."))
        self.assertIsNotNone(result)
        self.assertFalse(result.success)
        self.assertIn("RuntimeError", result.error)

    @patch("agent.agents.manager.settings")
    def test_agent_timeout_is_caught_and_reported(self, mock_settings):
        mock_settings.agent_timeout_seconds = 0.05
        manager.register(_FakeAgent(name="research", sleep_seconds=1.0))
        result = manager.route_and_execute("Research the best laptops.", self._context("Research the best laptops."))
        self.assertIsNotNone(result)
        self.assertFalse(result.success)
        self.assertIn("timed out", result.error)

    @patch("agent.agents.manager.cancellation_requested", return_value=True)
    def test_cancelled_before_start_is_reported_and_does_not_execute(self, mock_cancelled):
        agent = _FakeAgent(name="research")
        manager.register(agent)
        result = manager.route_and_execute("Research the best laptops.", self._context("Research the best laptops."))
        self.assertIsNotNone(result)
        self.assertTrue(result.cancelled)
        self.assertIsNone(agent.executed_with)  # never actually ran

    def test_max_depth_blocks_execution(self):
        agent = _FakeAgent(name="research")
        manager.register(agent)
        result = manager.route_and_execute(
            "Research the best laptops.", self._context("Research the best laptops."),
            depth=manager.MAX_AGENT_DEPTH,
        )
        self.assertIsNone(result)
        self.assertIsNone(agent.executed_with)  # never actually ran

    def test_agent_receives_the_task_and_context(self):
        agent = _FakeAgent(name="research")
        manager.register(agent)
        ctx = self._context("Research the best laptops.")
        manager.route_and_execute("Research the best laptops.", ctx)
        self.assertEqual(agent.executed_with, ("Research the best laptops.", ctx))


if __name__ == "__main__":
    unittest.main()
