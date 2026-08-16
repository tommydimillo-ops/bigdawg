"""Tests for agent/agents/manager.py -- the registry (register/
unregister/list/find), route_and_execute's failure handling (unknown
agent, disabled agent, exception, timeout, cancellation, recursion
depth), and execute_agent's subprocess-isolated failure handling (Phase 8
part 4). Uses fake, minimal Agent implementations throughout -- no real
network calls, no real Speech/tool access, matching this project's
established policy of mocking at the external-call boundary. Three
layers are tested at their own appropriate boundary: TestExecuteAgent
mocks _run_agent_subprocess (execute_agent's own response-interpretation
logic, not subprocess mechanics); TestRunAgentSubprocess mocks
subprocess.Popen itself (Phase 9 Milestone 3's cancellation-aware
replacement for subprocess.run -- see that function's own docstring);
TestRunAgentSubprocessRealProcess spawns one genuine, separate OS process
to prove the mechanism against reality, not just a mock (see
tools/agenda.py's AppleScript subprocess tests, agent/agents/qa.py's own
test-suite subprocess, and tests/test_browser_lock.py's real cross-
process test for the same "at least one real boundary test" precedent
elsewhere in this project).

Run with: python -m unittest tests.test_agents_manager -v
"""
import json
import os
import subprocess
import sys
import tempfile
import threading
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
    not route_and_execute. Phase 9 Milestone 3 replaced the internal
    subprocess.run call with _run_agent_subprocess (a Popen-based helper
    that additionally supports mid-flight cancellation -- see
    TestRunAgentSubprocess below for tests of THAT boundary, mocked at
    subprocess.Popen itself). These tests mock _run_agent_subprocess
    directly -- they're about execute_agent's OWN response-interpretation
    logic (returncode handling, JSON parsing, error surfacing), not
    subprocess mechanics, the same layering
    tests/test_agents_tool.py already uses one level further out (mocking
    execute_agent itself). Nothing here ever spawns a real process or
    makes a real network/memory call."""

    def _context(self, text="task", request_id="req-1"):
        return RequestContext.create(text, source="test", request_id=request_id)

    def _completed(self, stdout, returncode=0, stderr=""):
        return subprocess.CompletedProcess(
            args=["python", "-m", "agent.agents.worker"], returncode=returncode,
            stdout=stdout, stderr=stderr,
        )

    @patch("agent.agents.manager._run_agent_subprocess")
    def test_successful_execution_returns_parsed_result(self, mock_run):
        manager.register(_FakeAgent(name="research"))
        mock_run.return_value = (self._completed(json.dumps({
            "success": True, "agent_name": "research", "request_id": "req-1",
            "result": "found some laptops", "error": None, "duration_seconds": 1.2,
            "tools_used": [], "model_used": None, "provider_used": None,
            "verification_status": None, "cancelled": False, "metadata": {},
        })), False)

        result = manager.execute_agent("research", "best laptops", self._context())

        self.assertTrue(result.success)
        self.assertEqual(result.result, "found some laptops")
        mock_run.assert_called_once()

    @patch("agent.agents.manager._run_agent_subprocess")
    def test_passes_task_request_id_and_autonomy_level_to_the_subprocess(self, mock_run):
        manager.register(_FakeAgent(name="research"))
        mock_run.return_value = (self._completed(json.dumps({
            "success": True, "agent_name": "research", "request_id": "req-1",
            "result": "ok", "error": None,
        })), False)

        context = self._context("best laptops", request_id="req-42")
        context.autonomy_level = 2
        manager.execute_agent("research", "best laptops", context)

        args, _ = mock_run.call_args
        cmd, payload_str, timeout, cwd, request_id = args
        payload = json.loads(payload_str)
        self.assertEqual(payload["agent_name"], "research")
        self.assertEqual(payload["task"], "best laptops")
        self.assertEqual(payload["request_id"], "req-42")
        self.assertEqual(payload["autonomy_level"], 2)
        self.assertEqual(timeout, manager.settings.agent_timeout_seconds)
        self.assertEqual(request_id, "req-42")

    @patch("agent.agents.manager._run_agent_subprocess")
    def test_timeout_is_reported_as_a_normal_result(self, mock_run):
        # _run_agent_subprocess itself guarantees the kill on
        # TimeoutExpired (see TestRunAgentSubprocess below) -- this test
        # proves execute_agent surfaces that as a normal AgentResult
        # rather than letting the exception escape.
        manager.register(_FakeAgent(name="research"))
        mock_run.side_effect = subprocess.TimeoutExpired(cmd="agent.agents.worker", timeout=60)

        result = manager.execute_agent("research", "best laptops", self._context())

        self.assertFalse(result.success)
        self.assertIn("timed out", result.error)

    @patch("agent.agents.manager._run_agent_subprocess")
    def test_midflight_cancellation_is_reported_as_a_cancelled_result(self, mock_run):
        # The (completed, cancelled=True) shape _run_agent_subprocess
        # returns when it terminated an already-running subprocess
        # because the parent request was cancelled mid-flight.
        manager.register(_FakeAgent(name="research"))
        mock_run.return_value = (self._completed("", returncode=-15), True)

        result = manager.execute_agent("research", "best laptops", self._context())

        self.assertFalse(result.success)
        self.assertTrue(result.cancelled)
        self.assertIn("cancelled", result.error)

    @patch("agent.agents.manager._run_agent_subprocess")
    def test_nonzero_exit_code_is_reported_not_raised(self, mock_run):
        manager.register(_FakeAgent(name="research"))
        mock_run.return_value = (self._completed("", returncode=1, stderr="Traceback..."), False)

        result = manager.execute_agent("research", "best laptops", self._context())

        self.assertFalse(result.success)
        self.assertIn("exited with code 1", result.error)

    @patch("agent.agents.manager._run_agent_subprocess")
    def test_malformed_stdout_is_reported_not_raised(self, mock_run):
        manager.register(_FakeAgent(name="research"))
        mock_run.return_value = (self._completed("not valid json"), False)

        result = manager.execute_agent("research", "best laptops", self._context())

        self.assertFalse(result.success)
        self.assertIn("unreadable", result.error)

    @patch("agent.agents.manager._run_agent_subprocess")
    def test_worker_reported_error_is_surfaced(self, mock_run):
        manager.register(_FakeAgent(name="research"))
        mock_run.return_value = (self._completed(json.dumps({"error": "Agent 'research' is not registered."})), False)

        result = manager.execute_agent("research", "best laptops", self._context())

        self.assertFalse(result.success)
        self.assertEqual(result.error, "Agent 'research' is not registered.")

    @patch("agent.agents.manager._run_agent_subprocess")
    def test_unknown_agent_never_spawns_a_subprocess(self, mock_run):
        result = manager.execute_agent("nonexistent", "task", self._context())

        self.assertFalse(result.success)
        self.assertIn("not registered", result.error)
        mock_run.assert_not_called()

    @patch("agent.agents.manager._run_agent_subprocess")
    def test_disabled_agent_never_spawns_a_subprocess(self, mock_run):
        manager.register(_FakeAgent(name="research", enabled=False))

        result = manager.execute_agent("research", "task", self._context())

        self.assertFalse(result.success)
        self.assertIn("disabled", result.error)
        mock_run.assert_not_called()

    @patch("agent.agents.manager.cancellation_requested", return_value=True)
    @patch("agent.agents.manager._run_agent_subprocess")
    def test_cancelled_before_start_never_spawns_a_subprocess(self, mock_run, mock_cancelled):
        manager.register(_FakeAgent(name="research"))

        result = manager.execute_agent("research", "task", self._context())

        self.assertTrue(result.cancelled)
        mock_run.assert_not_called()

    @patch("agent.agents.manager._run_agent_subprocess")
    def test_max_depth_never_spawns_a_subprocess(self, mock_run):
        manager.register(_FakeAgent(name="research"))

        result = manager.execute_agent("research", "task", self._context(), depth=manager.MAX_AGENT_DEPTH)

        self.assertFalse(result.success)
        self.assertIn("depth", result.error)
        mock_run.assert_not_called()


class _FakePopen:
    """A precise stand-in for subprocess.Popen -- generic enough to drive
    _run_agent_subprocess's communicate()-based retry loop the same way
    the real thing behaves (communicate() callable more than once after
    a TimeoutExpired, per the stdlib's own documented contract), but with
    every action scripted so these tests are deterministic and instant
    (no real sleeping, no real process). `script` is a list of actions
    consumed one per communicate() call: "timeout" raises
    subprocess.TimeoutExpired; anything else finishes with that as the
    (stdout, stderr) pair. Once the script is exhausted, further calls
    finish with empty output -- terminate()/kill() also mark the process
    finished, matching real Popen semantics closely enough for these
    tests' purposes."""

    def __init__(self, script, returncode=0):
        self._script = list(script)
        self.returncode = None
        self._final_returncode = returncode
        self.terminate_calls = 0
        self.kill_calls = 0
        self.communicate_calls = 0

    def communicate(self, input=None, timeout=None):
        self.communicate_calls += 1
        action = self._script.pop(0) if self._script else ("finish", "", "")
        if action[0] == "timeout":
            raise subprocess.TimeoutExpired(cmd="x", timeout=timeout)
        self.returncode = self._final_returncode
        return action[1], action[2]

    def terminate(self):
        self.terminate_calls += 1

    def kill(self):
        self.kill_calls += 1
        self.returncode = self._final_returncode

    def poll(self):
        return self.returncode


class TestRunAgentSubprocess(unittest.TestCase):
    """Direct tests of _run_agent_subprocess -- the Popen-based helper
    introduced in Phase 9 Milestone 3 so a cancelled parent request can
    terminate an already-running coworker subprocess. Mocked at
    subprocess.Popen itself (the real external-call boundary), per this
    project's established test policy -- see tests/test_agents_manager.py's
    own module docstring and TestExecuteAgent's for why THOSE tests mock
    one layer up instead."""

    @patch("agent.agents.manager.subprocess.Popen")
    def test_normal_completion_returns_completed_process_and_not_cancelled(self, mock_popen_cls):
        fake = _FakePopen(script=[("finish", "output", "")])
        mock_popen_cls.return_value = fake

        completed, cancelled = manager._run_agent_subprocess(["cmd"], "payload", 10, "/tmp", "req-1")

        self.assertFalse(cancelled)
        self.assertEqual(completed.stdout, "output")
        self.assertEqual(completed.returncode, 0)
        self.assertEqual(fake.kill_calls, 0)
        self.assertEqual(fake.terminate_calls, 0)

    @patch("agent.agents.manager.time.time")
    @patch("agent.agents.manager.subprocess.Popen")
    def test_timeout_kills_the_process_and_raises(self, mock_popen_cls, mock_time):
        # Every communicate() call times out; time.time() is patched to
        # jump straight past the deadline so this test doesn't actually
        # sleep through real poll intervals.
        fake = _FakePopen(script=[("timeout",)] * 5)
        mock_popen_cls.return_value = fake
        mock_time.side_effect = [0, 100, 100, 100, 100, 100, 100]  # start, then far past any timeout

        with self.assertRaises(subprocess.TimeoutExpired):
            manager._run_agent_subprocess(["cmd"], "payload", 5, "/tmp", "req-1")

        self.assertGreaterEqual(fake.kill_calls, 1)

    @patch("agent.agents.manager.cancellation_requested", return_value=True)
    @patch("agent.agents.manager.subprocess.Popen")
    def test_midflight_cancellation_terminates_gracefully_first(self, mock_popen_cls, mock_cancelled):
        # First communicate() call times out (still "running"), so the
        # cancellation check runs and reports True; terminate() is tried
        # first, and the process exits cleanly within the grace period
        # (the post-terminate communicate() call succeeds).
        fake = _FakePopen(script=[("timeout",), ("finish", "", "")])
        mock_popen_cls.return_value = fake

        completed, cancelled = manager._run_agent_subprocess(["cmd"], "payload", 30, "/tmp", "req-1")

        self.assertTrue(cancelled)
        self.assertEqual(fake.terminate_calls, 1)
        self.assertEqual(fake.kill_calls, 0)

    @patch("agent.agents.manager.cancellation_requested", return_value=True)
    @patch("agent.agents.manager.subprocess.Popen")
    def test_midflight_cancellation_kills_if_graceful_termination_does_not_finish_in_time(
        self, mock_popen_cls, mock_cancelled,
    ):
        # terminate() is tried, but the post-terminate communicate() ALSO
        # times out (the process didn't exit cleanly) -- must fall
        # through to kill(), then reap with one final communicate().
        fake = _FakePopen(script=[("timeout",), ("timeout",), ("finish", "", "")])
        mock_popen_cls.return_value = fake

        completed, cancelled = manager._run_agent_subprocess(["cmd"], "payload", 30, "/tmp", "req-1")

        self.assertTrue(cancelled)
        self.assertEqual(fake.terminate_calls, 1)
        self.assertEqual(fake.kill_calls, 1)

    @patch("agent.agents.manager.subprocess.Popen")
    def test_no_request_id_never_checks_cancellation_and_still_completes(self, mock_popen_cls):
        fake = _FakePopen(script=[("finish", "ok", "")])
        mock_popen_cls.return_value = fake

        completed, cancelled = manager._run_agent_subprocess(["cmd"], "payload", 10, "/tmp", None)

        self.assertFalse(cancelled)
        self.assertEqual(completed.stdout, "ok")

    @patch("agent.agents.manager.subprocess.Popen")
    def test_unexpected_exception_still_reaps_the_process(self, mock_popen_cls):
        fake = _FakePopen(script=[])
        calls = {"count": 0}
        real_communicate = fake.communicate

        def _first_call_raises(*args, **kwargs):
            calls["count"] += 1
            if calls["count"] == 1:
                raise RuntimeError("something unexpected")
            return real_communicate(*args, **kwargs)

        fake.communicate = _first_call_raises
        mock_popen_cls.return_value = fake

        with self.assertRaises(RuntimeError):
            manager._run_agent_subprocess(["cmd"], "payload", 10, "/tmp", "req-1")

        # The finally block's backstop must still have tried to reap it
        # (poll() still showed it running, since returncode was never set
        # by the failed first call) -- and the retry's own communicate()
        # call succeeds cleanly rather than raising a second time.
        self.assertEqual(fake.kill_calls, 1)


class TestRunAgentSubprocessRealProcess(unittest.TestCase):
    """One real-subprocess test (a genuine, separate OS process, not a
    mocked Popen) per this milestone's own instruction to use real
    subprocess behavior in at least one cancellation test -- proves
    _run_agent_subprocess's mid-flight cancellation actually terminates a
    real child and leaves no orphan, using the real cancellation-marker
    mechanism (agent/cancellation.py's own file-backed marker), not a
    mocked cancellation_requested."""

    def setUp(self):
        from agent.cancellation import _marker_path
        self._request_id = "real-subprocess-cancel-test"
        self._marker_path = _marker_path(self._request_id)
        self._pid_file = tempfile.mktemp(suffix=".pid")

    def tearDown(self):
        if self._marker_path and os.path.exists(self._marker_path):
            os.remove(self._marker_path)
        if os.path.exists(self._pid_file):
            os.remove(self._pid_file)

    def test_cancellation_terminates_a_real_subprocess_with_no_orphan(self):
        from agent.cancellation import _write_marker

        # A real, separate Python process that writes its own PID
        # immediately, then sleeps far longer than this test should ever
        # take -- if _run_agent_subprocess's cancellation didn't actually
        # terminate it, it would still be alive long after this test
        # finishes.
        script = f"import os; open({self._pid_file!r}, 'w').write(str(os.getpid())); import time; time.sleep(30)"
        cmd = [sys.executable, "-c", script]

        result_holder = {}

        def _run():
            result_holder["outcome"] = manager._run_agent_subprocess(
                cmd, "", 60, os.getcwd(), self._request_id,
            )

        thread = threading.Thread(target=_run)
        thread.start()

        # Wait for the child to actually start and record its own PID
        # (real process startup, not instantaneous).
        deadline = time.time() + 5
        while not os.path.exists(self._pid_file) and time.time() < deadline:
            time.sleep(0.05)
        self.assertTrue(os.path.exists(self._pid_file), "the real subprocess never started")
        with open(self._pid_file) as f:
            child_pid = int(f.read().strip())

        _write_marker(self._request_id)  # the real cancellation marker

        thread.join(timeout=10)
        self.assertFalse(thread.is_alive(), "_run_agent_subprocess did not return after cancellation")

        completed, cancelled = result_holder["outcome"]
        self.assertTrue(cancelled)

        # No orphan: the real child process must actually be gone.
        with self.assertRaises(ProcessLookupError):
            os.kill(child_pid, 0)


if __name__ == "__main__":
    unittest.main()
