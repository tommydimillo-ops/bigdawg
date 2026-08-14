"""Tests for agent/agents/worker.py's main() -- the stdin/stdout JSON
contract agent/agents/manager.py's execute_agent() relies on when it
spawns `python -m agent.agents.worker` as a real subprocess (see
tests/test_agents_manager.py's TestExecuteAgent for that side of the
boundary, mocked at subprocess.run).

Calls main() directly, in-process, rather than through a real subprocess
launch -- this is deliberately just as safe/hermetic as calling any other
function in this project's test suite (no real subprocess, no real
network/memory call), and lets a fake, in-process-only Agent stand in via
the real registry exactly like tests/test_agents_manager.py's _FakeAgent
does.

Run with: python -m unittest tests.test_agents_worker -v
"""
import io
import json
import os
import tempfile
import unittest
from unittest.mock import patch

import agent.usage as usage
from agent.agents import manager
from agent.agents.base import Agent, AgentMetadata
from agent.agents.models import AgentResult
from agent.request_context import RequestContext, get_current_request_id


class _FakeAgent(Agent):

    def __init__(self, name="research", result_text="ok", raises=None):
        self._name = name
        self._result_text = result_text
        self._raises = raises
        self.executed_with = None

    @property
    def metadata(self) -> AgentMetadata:
        return AgentMetadata(name=self._name, description="test agent")

    def execute(self, task, context):
        self.executed_with = (task, context)
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

    def _run_main(self, payload):
        from agent.agents import worker
        stdin = io.StringIO(json.dumps(payload))
        stdout = io.StringIO()
        with patch("sys.stdin", stdin), patch("sys.stdout", stdout):
            worker.main()
        return stdout.getvalue()


class TestWorkerMain(IsolatedRegistryTestCase):

    def test_runs_the_named_agent_and_writes_its_result_as_json(self):
        manager.register(_FakeAgent(name="research", result_text="found some laptops"))

        output = self._run_main({"agent_name": "research", "task": "best laptops", "request_id": "req-1"})

        data = json.loads(output)
        self.assertTrue(data["success"])
        self.assertEqual(data["result"], "found some laptops")
        self.assertEqual(data["agent_name"], "research")

    def test_unknown_agent_writes_an_error_object(self):
        output = self._run_main({"agent_name": "nonexistent", "task": "x", "request_id": "req-1"})

        data = json.loads(output)
        self.assertIn("error", data)
        self.assertNotIn("success", data)

    def test_agent_receives_a_context_carrying_the_task_and_request_id(self):
        agent = _FakeAgent(name="research")
        manager.register(agent)

        self._run_main({"agent_name": "research", "task": "best laptops", "request_id": "req-99"})

        task, context = agent.executed_with
        self.assertEqual(task, "best laptops")
        self.assertEqual(context.request_id, "req-99")
        self.assertEqual(context.user_input, "best laptops")

    def test_autonomy_level_is_propagated_onto_the_context(self):
        agent = _FakeAgent(name="research")
        manager.register(agent)

        self._run_main({
            "agent_name": "research", "task": "best laptops",
            "request_id": "req-1", "autonomy_level": 2,
        })

        _, context = agent.executed_with
        self.assertEqual(context.autonomy_level, 2)

    def test_binds_request_id_onto_the_contextvar_for_nested_lookups(self):
        agent = _FakeAgent(name="research")
        seen = {}

        class _CapturingAgent(Agent):
            @property
            def metadata(self):
                return AgentMetadata(name="research", description="test")

            def execute(self, task, context):
                seen["request_id"] = get_current_request_id()
                return AgentResult(success=True, agent_name="research", request_id=context.request_id, result="ok")

        manager.register(_CapturingAgent())

        self._run_main({"agent_name": "research", "task": "x", "request_id": "req-contextvar"})

        self.assertEqual(seen["request_id"], "req-contextvar")

    def test_an_exception_inside_agent_execute_propagates_so_the_subprocess_exits_nonzero(self):
        # execute_agent (the parent side) relies on a nonzero exit code to
        # detect an in-process failure -- see tests/test_agents_manager.
        # py's test_nonzero_exit_code_is_reported_not_raised. Confirms
        # main() doesn't swallow the exception itself.
        manager.register(_FakeAgent(name="research", raises=RuntimeError("boom")))

        with self.assertRaises(RuntimeError):
            self._run_main({"agent_name": "research", "task": "x", "request_id": "req-1"})


class TestUsageIsRecordedThroughTheWorkerBoundary(IsolatedRegistryTestCase):
    """Phase 8 part 6's explicit "agent usage is recorded" requirement --
    proves usage recorded by an agent's own execute() (the same
    record_llm_usage call every real agent, e.g. agent/research_agent.py,
    already makes) actually reaches USAGE_FILE when run through worker.
    main(), the same code path a real `python -m agent.agents.worker`
    subprocess runs. USAGE_FILE is a real filesystem path shared by
    parent and child regardless of which process writes to it (fcntl-
    locked, same as every other write to it) -- isolated to a temp file
    here so this never touches the real usage history."""

    def setUp(self):
        super().setUp()
        self._real_usage_file = usage.USAGE_FILE
        usage.USAGE_FILE = tempfile.mktemp(suffix=".json")

    def tearDown(self):
        for path in (usage.USAGE_FILE, f"{usage.USAGE_FILE}.lock"):
            if os.path.exists(path):
                os.remove(path)
        usage.USAGE_FILE = self._real_usage_file
        super().tearDown()

    def test_usage_recorded_inside_agent_execute_is_persisted(self):
        from agent.usage import get_recent, record_llm_usage

        class _UsageRecordingAgent(Agent):
            @property
            def metadata(self):
                return AgentMetadata(name="research", description="test")

            def execute(self, task, context):
                record_llm_usage(
                    provider="anthropic", model="claude-sonnet-5", operation="research",
                    request_id=get_current_request_id(), agent="research",
                    input_tokens=100, output_tokens=20,
                )
                return AgentResult(success=True, agent_name="research", request_id=context.request_id, result="ok")

        manager.register(_UsageRecordingAgent())

        self._run_main({"agent_name": "research", "task": "x", "request_id": "req-usage-1"})

        recorded = get_recent()
        self.assertEqual(len(recorded), 1)
        self.assertEqual(recorded[0].request_id, "req-usage-1")
        self.assertEqual(recorded[0].agent, "research")
        self.assertEqual(recorded[0].input_tokens, 100)


if __name__ == "__main__":
    unittest.main()
