"""Tests for agent/agents/manager.py -- the registry (register/
unregister/list/find), route_and_execute's failure handling (unknown
agent, disabled agent, exception, timeout, cancellation, recursion
depth), and execute_agent's subprocess-isolated failure handling (Phase 8
part 4). Uses fake, minimal Agent implementations throughout -- no real
network calls, no real Speech/tool access, matching this project's
established policy of mocking at the external-call boundary; for
execute_agent that boundary is subprocess.run itself (a real,
separate OS process), exactly the same policy this project already
applies to every other external call (see tools/agenda.py's AppleScript
subprocess tests, agent/agents/qa.py's own test-suite subprocess).

Run with: python -m unittest tests.test_agents_manager -v
"""
import json
import subprocess
import time
import unittest
from unittest.mock import MagicMock, patch

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


class TestExecuteAgent(IsolatedRegistryTestCase):
    """execute_agent (Phase 8 part 4) is the real, live execution entry
    point -- tools/schemas/agents.py's consult_coworker_agent calls it,
    not route_and_execute. Every real subprocess launch is mocked at the
    subprocess.run boundary, so nothing here ever spawns a real process
    or makes a real network/memory call."""

    def _context(self, text="task", request_id="req-1"):
        return RequestContext.create(text, source="test", request_id=request_id)

    def _completed(self, stdout, returncode=0, stderr=""):
        return subprocess.CompletedProcess(
            args=["python", "-m", "agent.agents.worker"], returncode=returncode,
            stdout=stdout, stderr=stderr,
        )

    @patch("agent.agents.manager.subprocess.run")
    def test_successful_execution_returns_parsed_result(self, mock_run):
        manager.register(_FakeAgent(name="research"))
        mock_run.return_value = self._completed(json.dumps({
            "success": True, "agent_name": "research", "request_id": "req-1",
            "result": "found some laptops", "error": None, "duration_seconds": 1.2,
            "tools_used": [], "model_used": None, "provider_used": None,
            "verification_status": None, "cancelled": False, "metadata": {},
        }))

        result = manager.execute_agent("research", "best laptops", self._context())

        self.assertTrue(result.success)
        self.assertEqual(result.result, "found some laptops")
        mock_run.assert_called_once()

    @patch("agent.agents.manager.subprocess.run")
    def test_passes_task_request_id_and_autonomy_level_to_the_subprocess(self, mock_run):
        manager.register(_FakeAgent(name="research"))
        mock_run.return_value = self._completed(json.dumps({
            "success": True, "agent_name": "research", "request_id": "req-1",
            "result": "ok", "error": None,
        }))

        context = self._context("best laptops", request_id="req-42")
        context.autonomy_level = 2
        manager.execute_agent("research", "best laptops", context)

        _, kwargs = mock_run.call_args
        payload = json.loads(kwargs["input"])
        self.assertEqual(payload["agent_name"], "research")
        self.assertEqual(payload["task"], "best laptops")
        self.assertEqual(payload["request_id"], "req-42")
        self.assertEqual(payload["autonomy_level"], 2)
        self.assertEqual(kwargs["timeout"], manager.settings.agent_timeout_seconds)

    @patch("agent.agents.manager.subprocess.run")
    def test_timeout_kills_the_subprocess_and_reports_it(self, mock_run):
        # subprocess.run itself guarantees the kill on TimeoutExpired
        # (it SIGKILLs the child before raising) -- this test proves
        # execute_agent surfaces that as a normal AgentResult rather than
        # letting the exception escape.
        manager.register(_FakeAgent(name="research"))
        mock_run.side_effect = subprocess.TimeoutExpired(cmd="agent.agents.worker", timeout=60)

        result = manager.execute_agent("research", "best laptops", self._context())

        self.assertFalse(result.success)
        self.assertIn("timed out", result.error)

    @patch("agent.agents.manager.subprocess.run")
    def test_nonzero_exit_code_is_reported_not_raised(self, mock_run):
        manager.register(_FakeAgent(name="research"))
        mock_run.return_value = self._completed("", returncode=1, stderr="Traceback...")

        result = manager.execute_agent("research", "best laptops", self._context())

        self.assertFalse(result.success)
        self.assertIn("exited with code 1", result.error)

    @patch("agent.agents.manager.subprocess.run")
    def test_malformed_stdout_is_reported_not_raised(self, mock_run):
        manager.register(_FakeAgent(name="research"))
        mock_run.return_value = self._completed("not valid json")

        result = manager.execute_agent("research", "best laptops", self._context())

        self.assertFalse(result.success)
        self.assertIn("unreadable", result.error)

    @patch("agent.agents.manager.subprocess.run")
    def test_worker_reported_error_is_surfaced(self, mock_run):
        manager.register(_FakeAgent(name="research"))
        mock_run.return_value = self._completed(json.dumps({"error": "Agent 'research' is not registered."}))

        result = manager.execute_agent("research", "best laptops", self._context())

        self.assertFalse(result.success)
        self.assertEqual(result.error, "Agent 'research' is not registered.")

    @patch("agent.agents.manager.subprocess.run")
    def test_unknown_agent_never_spawns_a_subprocess(self, mock_run):
        result = manager.execute_agent("nonexistent", "task", self._context())

        self.assertFalse(result.success)
        self.assertIn("not registered", result.error)
        mock_run.assert_not_called()

    @patch("agent.agents.manager.subprocess.run")
    def test_disabled_agent_never_spawns_a_subprocess(self, mock_run):
        manager.register(_FakeAgent(name="research", enabled=False))

        result = manager.execute_agent("research", "task", self._context())

        self.assertFalse(result.success)
        self.assertIn("disabled", result.error)
        mock_run.assert_not_called()

    @patch("agent.agents.manager.cancellation_requested", return_value=True)
    @patch("agent.agents.manager.subprocess.run")
    def test_cancelled_before_start_never_spawns_a_subprocess(self, mock_run, mock_cancelled):
        manager.register(_FakeAgent(name="research"))

        result = manager.execute_agent("research", "task", self._context())

        self.assertTrue(result.cancelled)
        mock_run.assert_not_called()

    @patch("agent.agents.manager.subprocess.run")
    def test_max_depth_never_spawns_a_subprocess(self, mock_run):
        manager.register(_FakeAgent(name="research"))

        result = manager.execute_agent("research", "task", self._context(), depth=manager.MAX_AGENT_DEPTH)

        self.assertFalse(result.success)
        self.assertIn("depth", result.error)
        mock_run.assert_not_called()


if __name__ == "__main__":
    unittest.main()
