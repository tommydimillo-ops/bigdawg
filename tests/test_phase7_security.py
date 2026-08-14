"""Security-focused tests for Phase 7 (Agent Manager + Coworker Agents)
-- exercising the real mechanisms (matching this project's established
policy, see tests/test_phase4_security.py through test_phase6_security.py
and test_skills_security.py), not just the pure decision functions in
isolation.

Guarantees this covers, specific to this phase:
1. consult_coworker_agent (the real agent-execution entry point) is
   dispatched through the exact same tools.registry/agent.autonomy/
   agent.executor._run_tool funnel every other tool uses -- a hard
   permission gate on ANOTHER tool still applies even with an agent
   "involved" earlier in the same request, because agent involvement
   never sets any flag _run_tool reads.
2. agent.agents.manager.route_and_execute refuses to run anything past
   MAX_AGENT_DEPTH -- no agent can trigger a second round of agent
   selection (Phase 7 section 24's "no recursive delegation").
3. agent.executor.execute_task_stream's own routing check is read-only:
   it never calls any agent's execute(), confirmed directly against the
   real function object, not a description of it.

Run with: python -m unittest tests.test_phase7_security -v
"""
import inspect
import tempfile
import unittest
from unittest.mock import MagicMock, patch

import tools.schemas  # noqa: F401 -- populates the registry
import agent.jarvis_state as jarvis_state
from agent.agents import manager as agent_manager
from agent.agents.base import Agent, AgentMetadata
from agent.agents.models import AgentResult
from agent.execution_state import ExecutionState
from agent.executor import _run_tool
from agent.request_context import RequestContext

_real_state_file = jarvis_state.STATE_FILE


def setUpModule():
    jarvis_state.STATE_FILE = tempfile.mktemp(suffix=".json")


def tearDownModule():
    jarvis_state.STATE_FILE = _real_state_file


class TestAgentToolCannotBypassConfirmation(unittest.TestCase):
    """The definitive test: consult_coworker_agent existing as a
    registered, dispatchable tool changes nothing about how a
    DIFFERENT, gated tool call is handled in the same request -- this
    exercises the real agent.executor._run_tool funnel point, exactly
    like every prior phase's security tests do."""

    def test_gated_tool_still_requires_confirmation_after_an_agent_ran(self):
        ctx = RequestContext.create("do the risky thing", source="chat")
        ctx.autonomy_level = 4
        state = ExecutionState(max_iterations=8)
        state.record_agent("research", "some earlier research task", "completed")

        # computer_confirm_action is permission_level 5 -- always CONFIRM
        # at every defined autonomy level, agent-attributed or not. A
        # description unique to this test file/method, not shared with
        # any other test -- agent.autonomy's pending-confirmation ledger
        # is keyed on the exact (tool_name, tool_input) pair and isn't
        # reset between tests, so reusing another test's exact input
        # here would make this test's own confirmation request appear
        # already-satisfied by that other test's residual ledger entry.
        result = _run_tool(
            "computer_confirm_action", {"description": "phase7 security test action -- gated tool after agent"},
            source="chat", context=ctx, state=state,
        )
        self.assertIn("OK first", result)
        self.assertNotIn("Confirmed action executed", result)

    def test_scheduled_hard_gates_still_apply_with_an_agent_attributed(self):
        ctx = RequestContext.create("do something", source="scheduled")
        ctx.autonomy_level = 4
        state = ExecutionState(max_iterations=8)
        state.record_agent("coding", "some task", "completed")

        result = _run_tool(
            "send_email", {"to": "phase7-test@example.com"}, source="scheduled", context=ctx, state=state,
        )
        self.assertIn("Skipped", result)

    def test_consult_coworker_agent_itself_goes_through_run_tool(self):
        # Dispatched via the real funnel, not called directly -- proves
        # it's an ordinary tool subject to the same logging/permission
        # machinery as everything else, not a special bypass path. Real
        # execution (Phase 8 part 4) happens in a subprocess spawned by
        # agent.agents.manager.execute_agent, so that's the boundary
        # mocked here -- mocking agent.agents.research.research (an
        # in-process function) would have zero effect on that subprocess
        # and would let this test make a real, unmocked network call.
        ctx = RequestContext.create("research something", source="chat")
        state = ExecutionState(max_iterations=8)
        fake_result = AgentResult(
            success=True, agent_name="research", request_id=ctx.request_id, result="a real answer",
        )
        with patch("tools.schemas.agents.execute_agent", return_value=fake_result):
            result = _run_tool(
                "consult_coworker_agent",
                {"agent_name": "research", "task": "best laptops"},
                source="chat", context=ctx, state=state,
            )
        self.assertEqual(result, "a real answer")
        self.assertIn("consult_coworker_agent", state.tools_executed)


class TestNoRecursiveAgentDelegation(unittest.TestCase):
    """Phase 7 section 24: agents cannot freely delegate to other
    agents. Enforced structurally in agent.agents.manager, not just by
    convention -- these confirm the enforcement itself, not merely that
    no agent currently happens to call it recursively."""

    def setUp(self):
        self._real_registry = dict(agent_manager._REGISTRY)
        agent_manager.clear()

    def tearDown(self):
        agent_manager.clear()
        agent_manager._REGISTRY.update(self._real_registry)

    def test_depth_at_the_limit_refuses_to_execute(self):
        class _RecordingAgent(Agent):
            executed = False

            @property
            def metadata(self):
                return AgentMetadata(name="research", description="d")

            def execute(self, task, context):
                _RecordingAgent.executed = True
                return AgentResult(success=True, agent_name="research", request_id=context.request_id, result="x")

        agent_manager.register(_RecordingAgent())
        ctx = RequestContext.create("Research the best laptops.", source="test")

        result = agent_manager.route_and_execute(
            "Research the best laptops.", ctx, depth=agent_manager.MAX_AGENT_DEPTH,
        )

        self.assertIsNone(result)
        self.assertFalse(_RecordingAgent.executed)

    def test_a_hypothetical_agent_calling_route_and_execute_from_inside_execute_is_blocked(self):
        """Simulates the exact failure mode section 24 forbids: an
        agent's own execute() calling back into route_and_execute. Even
        if a future agent did this by mistake, the inner call must
        refuse to run anything rather than silently cascading."""
        inner_result_holder = {}

        class _RecursiveAgent(Agent):
            @property
            def metadata(self):
                return AgentMetadata(name="research", description="d")

            def execute(self, task, context):
                inner_result_holder["inner"] = agent_manager.route_and_execute(
                    task, context, depth=1,  # a well-behaved caller would never do this manually with depth=1, but a buggy one might
                )
                return AgentResult(success=True, agent_name="research", request_id=context.request_id, result="outer")

        agent_manager.register(_RecursiveAgent())
        ctx = RequestContext.create("Research the best laptops.", source="test")
        agent_manager.route_and_execute("Research the best laptops.", ctx, depth=0)

        self.assertIsNone(inner_result_holder["inner"])


class TestExecutorRoutingCheckIsReadOnly(unittest.TestCase):
    """Confirms agent/executor.py's own routing call site uses the
    pure agent.agents.router.route function, never agent.agents.manager's
    executing route_and_execute -- checked against the real imported
    object executor.py holds, not a description of intent."""

    def test_executor_imports_the_pure_router_function_not_the_executor(self):
        import agent.executor as executor_module
        from agent.agents.router import route as real_route

        self.assertIs(executor_module.route_agent_task, real_route)

    def test_pure_route_function_has_no_side_effects_by_inspection(self):
        # A light structural sanity check: the pure router module has no
        # dependency on agent.agents.manager (the executing half) at all.
        import agent.agents.router as router_module
        source = inspect.getsource(router_module)
        self.assertNotIn("import agent.agents.manager", source)
        self.assertNotIn("from agent.agents.manager", source)


if __name__ == "__main__":
    unittest.main()
