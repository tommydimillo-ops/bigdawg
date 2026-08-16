"""Tests for agent/agents/manager.py's execute_agents_parallel -- Phase 9
Milestone 3's bounded-parallel coworker delegation. Mocks at the same
boundary tests/test_agents_manager.py already established for
execute_agent's own callers (execute_agent itself, not subprocess.run
directly) -- execute_agent's own subprocess-isolation guarantees are
already covered there; these tests are about the BATCH coordination on
top of it: concurrency bound, retries, cancellation, cost pre-flight,
and combined verification status.

Run with: python -m unittest tests.test_agents_batch -v
"""
import threading
import time
import unittest
from unittest.mock import patch

from agent.agents.manager import MAX_AGENT_DEPTH, execute_agents_parallel
from agent.agents.models import AgentBatchResult, AgentResult, AgentTaskRequest, BatchStatus
from agent.provider_budget import BudgetStatus
from agent.request_context import RequestContext


def _ok(agent_name, request_id="req-1", result="done (see https://example.com)"):
    # The default result text passes verify_agent_result's research-
    # specific source-evidence heuristic too (agent/verification.py's
    # _verify_research_result), so this fixture works unmodified for
    # every agent_name -- a bare "done" would correctly, deliberately
    # fail that heuristic for agent_name="research" and isn't what these
    # tests are about.
    return AgentResult(success=True, agent_name=agent_name, request_id=request_id, result=result)


def _fail(agent_name, request_id="req-1", error="boom"):
    return AgentResult(success=False, agent_name=agent_name, request_id=request_id, result="", error=error)


def _context(request_id="req-1"):
    return RequestContext.create("batch", source="test", request_id=request_id)


def _not_over_budget(provider="__global__"):
    return BudgetStatus(provider=provider, spent_today_usd=0.0, limit_usd=None, at_warning=False, over_limit=False)


def _over_budget(provider="__global__"):
    return BudgetStatus(provider=provider, spent_today_usd=999.0, limit_usd=1.0, at_warning=True, over_limit=True)


class TestBatchSizeLimits(unittest.TestCase):

    def test_empty_batch_is_rejected(self):
        result = execute_agents_parallel([], _context())
        self.assertEqual(result.status, BatchStatus.FAILED)
        self.assertEqual(result.items, [])

    @patch("agent.agents.manager.settings")
    @patch("agent.agents.manager.execute_agent")
    def test_batch_larger_than_max_parallel_agents_is_rejected(self, mock_execute, mock_settings):
        mock_settings.max_parallel_agents = 2
        tasks = [
            AgentTaskRequest(agent_name="research", task="a"),
            AgentTaskRequest(agent_name="research", task="b"),
            AgentTaskRequest(agent_name="research", task="c"),
        ]
        result = execute_agents_parallel(tasks, _context())
        self.assertEqual(result.status, BatchStatus.FAILED)
        self.assertEqual(result.items, [])
        mock_execute.assert_not_called()
        self.assertIn("exceeds", result.note)

    @patch("agent.agents.manager.global_budget_status", return_value=_not_over_budget())
    @patch("agent.agents.manager.execute_agent")
    def test_batch_at_exactly_the_limit_is_allowed(self, mock_execute, mock_budget):
        mock_execute.side_effect = lambda name, task, ctx, depth=0: _ok(name)
        with patch("agent.agents.manager.settings") as mock_settings:
            mock_settings.max_parallel_agents = 2
            mock_settings.max_agent_batch_retries = 1
            tasks = [
                AgentTaskRequest(agent_name="research", task="a"),
                AgentTaskRequest(agent_name="memory", task="b"),
            ]
            result = execute_agents_parallel(tasks, _context())
        self.assertEqual(result.status, BatchStatus.ALL_SUCCEEDED)
        self.assertEqual(len(result.items), 2)


class TestMaxAgentDepthEnforced(unittest.TestCase):

    @patch("agent.agents.manager.execute_agent")
    def test_depth_at_or_above_max_blocks_the_whole_batch(self, mock_execute):
        tasks = [
            AgentTaskRequest(agent_name="research", task="a"),
            AgentTaskRequest(agent_name="memory", task="b"),
        ]
        result = execute_agents_parallel(tasks, _context(), depth=MAX_AGENT_DEPTH)
        self.assertEqual(result.status, BatchStatus.FAILED)
        self.assertEqual(result.items, [])
        mock_execute.assert_not_called()

    @patch("agent.agents.manager.global_budget_status", return_value=_not_over_budget())
    @patch("agent.agents.manager.execute_agent")
    def test_each_subtask_is_dispatched_at_the_same_depth_not_incremented(self, mock_execute, mock_budget):
        mock_execute.side_effect = lambda name, task, ctx, depth=0: _ok(name)
        tasks = [AgentTaskRequest(agent_name="research", task="a"), AgentTaskRequest(agent_name="memory", task="b")]
        execute_agents_parallel(tasks, _context(), depth=0)
        for call in mock_execute.call_args_list:
            self.assertEqual(call.kwargs.get("depth", call.args[3] if len(call.args) > 3 else None), 0)


class TestRealConcurrency(unittest.TestCase):
    """Uses real threads (not mocked concurrency) to prove subtasks
    actually overlap in time and that the overlap never exceeds
    settings.max_parallel_agents -- per this milestone's own instruction
    to use real concurrency primitives in tests where practical."""

    @patch("agent.agents.manager.global_budget_status", return_value=_not_over_budget())
    def test_two_independent_tasks_actually_run_concurrently(self, mock_budget):
        started = threading.Event()
        release = threading.Event()
        concurrent_count = {"value": 0}
        lock = threading.Lock()

        def _slow_execute(name, task, ctx, depth=0):
            with lock:
                concurrent_count["value"] += 1
            started.set()
            release.wait(timeout=2)
            with lock:
                concurrent_count["value"] -= 1
            return _ok(name)

        with patch("agent.agents.manager.execute_agent", side_effect=_slow_execute):
            tasks = [AgentTaskRequest(agent_name="research", task="a"), AgentTaskRequest(agent_name="memory", task="b")]

            result_holder = {}

            def _run():
                result_holder["result"] = execute_agents_parallel(tasks, _context())

            thread = threading.Thread(target=_run)
            thread.start()

            started.wait(timeout=2)
            time.sleep(0.05)  # give the second task a chance to also start
            peak = concurrent_count["value"]
            release.set()
            thread.join(timeout=2)

        self.assertEqual(peak, 2)
        self.assertEqual(result_holder["result"].status, BatchStatus.ALL_SUCCEEDED)

    @patch("agent.agents.manager.global_budget_status", return_value=_not_over_budget())
    @patch("agent.agents.manager.settings")
    def test_concurrency_never_exceeds_the_configured_maximum(self, mock_settings, mock_budget):
        mock_settings.max_parallel_agents = 3
        mock_settings.max_agent_batch_retries = 1
        peak = {"value": 0}
        current = {"value": 0}
        lock = threading.Lock()

        def _slow_execute(name, task, ctx, depth=0):
            with lock:
                current["value"] += 1
                peak["value"] = max(peak["value"], current["value"])
            time.sleep(0.05)
            with lock:
                current["value"] -= 1
            return _ok(name)

        with patch("agent.agents.manager.execute_agent", side_effect=_slow_execute):
            tasks = [AgentTaskRequest(agent_name="research", task=f"t{i}") for i in range(3)]
            result = execute_agents_parallel(tasks, _context())

        self.assertLessEqual(peak["value"], 3)
        self.assertEqual(result.status, BatchStatus.ALL_SUCCEEDED)


class TestFailureHandling(unittest.TestCase):

    @patch("agent.agents.manager.global_budget_status", return_value=_not_over_budget())
    @patch("agent.agents.manager.execute_agent")
    def test_one_failure_among_required_tasks_is_reported_as_failed(self, mock_execute, mock_budget):
        mock_execute.side_effect = lambda name, task, ctx, depth=0: (
            _ok(name) if name == "research" else _fail(name)
        )
        tasks = [
            AgentTaskRequest(agent_name="research", task="a", required=True),
            AgentTaskRequest(agent_name="memory", task="b", required=True),
        ]
        result = execute_agents_parallel(tasks, _context())
        self.assertEqual(result.status, BatchStatus.FAILED)
        names_ok = {item.agent_name: item.result.success for item in result.items}
        self.assertTrue(names_ok["research"])
        self.assertFalse(names_ok["memory"])

    @patch("agent.agents.manager.global_budget_status", return_value=_not_over_budget())
    @patch("agent.agents.manager.execute_agent")
    def test_optional_task_failure_still_yields_partial_not_failed(self, mock_execute, mock_budget):
        mock_execute.side_effect = lambda name, task, ctx, depth=0: (
            _ok(name) if name == "research" else _fail(name)
        )
        tasks = [
            AgentTaskRequest(agent_name="research", task="a", required=True),
            AgentTaskRequest(agent_name="memory", task="b", required=False),
        ]
        result = execute_agents_parallel(tasks, _context())
        self.assertEqual(result.status, BatchStatus.PARTIAL)

    @patch("agent.agents.manager.global_budget_status", return_value=_not_over_budget())
    @patch("agent.agents.manager.execute_agent")
    def test_a_browser_busy_style_failure_from_one_subtask_does_not_crash_the_batch(self, mock_execute, mock_budget):
        mock_execute.side_effect = lambda name, task, ctx, depth=0: (
            _ok(name) if name == "memory"
            else _fail(name, error="Another Jarvis process is already using the browser.")
        )
        tasks = [
            AgentTaskRequest(agent_name="research", task="a", required=False),
            AgentTaskRequest(agent_name="memory", task="b", required=True),
        ]
        result = execute_agents_parallel(tasks, _context())
        self.assertEqual(result.status, BatchStatus.PARTIAL)


class TestBoundedRetry(unittest.TestCase):

    @patch("agent.agents.manager.global_budget_status", return_value=_not_over_budget())
    @patch("agent.agents.manager.settings")
    @patch("agent.agents.manager.execute_agent")
    def test_a_failed_task_is_retried_up_to_the_configured_bound(self, mock_execute, mock_settings, mock_budget):
        mock_settings.max_parallel_agents = 3
        mock_settings.max_agent_batch_retries = 1
        mock_execute.side_effect = lambda name, task, ctx, depth=0: _fail(name)

        tasks = [AgentTaskRequest(agent_name="research", task="a")]
        result = execute_agents_parallel(tasks, _context())

        # 1 initial attempt + 1 retry (the configured bound) = 2 total calls.
        self.assertEqual(mock_execute.call_count, 2)
        self.assertTrue(result.items[0].retried)
        self.assertEqual(result.status, BatchStatus.FAILED)

    @patch("agent.agents.manager.global_budget_status", return_value=_not_over_budget())
    @patch("agent.agents.manager.settings")
    @patch("agent.agents.manager.execute_agent")
    def test_repeated_failure_does_not_loop_forever(self, mock_execute, mock_settings, mock_budget):
        mock_settings.max_parallel_agents = 3
        mock_settings.max_agent_batch_retries = 2
        mock_execute.side_effect = lambda name, task, ctx, depth=0: _fail(name)

        tasks = [AgentTaskRequest(agent_name="research", task="a")]
        execute_agents_parallel(tasks, _context())

        # 1 initial attempt + 2 retries = 3 total calls, never unbounded.
        self.assertEqual(mock_execute.call_count, 3)

    @patch("agent.agents.manager.global_budget_status", return_value=_not_over_budget())
    @patch("agent.agents.manager.execute_agent")
    def test_a_successful_first_attempt_is_never_retried(self, mock_execute, mock_budget):
        mock_execute.side_effect = lambda name, task, ctx, depth=0: _ok(name)
        tasks = [AgentTaskRequest(agent_name="research", task="a")]
        result = execute_agents_parallel(tasks, _context())
        self.assertEqual(mock_execute.call_count, 1)
        self.assertFalse(result.items[0].retried)

    @patch("agent.agents.manager.global_budget_status", return_value=_not_over_budget())
    @patch("agent.agents.manager.execute_agent")
    def test_a_cancelled_task_is_never_retried(self, mock_execute, mock_budget):
        cancelled_result = AgentResult(
            success=False, agent_name="research", request_id="req-1", result="",
            cancelled=True, error="cancelled before the agent started",
        )
        mock_execute.side_effect = lambda name, task, ctx, depth=0: cancelled_result
        tasks = [AgentTaskRequest(agent_name="research", task="a")]
        execute_agents_parallel(tasks, _context())
        self.assertEqual(mock_execute.call_count, 1)


class TestCancellation(unittest.TestCase):

    @patch("agent.agents.manager.cancellation_requested", return_value=True)
    @patch("agent.agents.manager.execute_agent")
    def test_cancelled_before_start_never_spawns_anything(self, mock_execute, mock_cancelled):
        tasks = [AgentTaskRequest(agent_name="research", task="a"), AgentTaskRequest(agent_name="memory", task="b")]
        result = execute_agents_parallel(tasks, _context())
        self.assertEqual(result.status, BatchStatus.FAILED)
        mock_execute.assert_not_called()
        self.assertTrue(all(item.result.cancelled for item in result.items))

    @patch("agent.agents.manager.global_budget_status", return_value=_not_over_budget())
    @patch("agent.agents.manager.settings")
    @patch("agent.agents.manager.execute_agent")
    def test_cancellation_mid_batch_skips_further_retries(self, mock_execute, mock_settings, mock_budget):
        mock_settings.max_parallel_agents = 3
        mock_settings.max_agent_batch_retries = 1
        mock_execute.side_effect = lambda name, task, ctx, depth=0: _fail(name)

        call_count = {"value": 0}

        def _cancelled_after_first_attempt(request_id):
            return call_count["value"] >= 1

        with patch("agent.agents.manager.cancellation_requested", side_effect=_cancelled_after_first_attempt):
            original_execute = mock_execute.side_effect

            def _counting_execute(name, task, ctx, depth=0):
                call_count["value"] += 1
                return original_execute(name, task, ctx, depth=depth)

            mock_execute.side_effect = _counting_execute
            tasks = [AgentTaskRequest(agent_name="research", task="a")]
            execute_agents_parallel(tasks, _context())

        # Only the initial attempt ran -- the retry was skipped once
        # cancellation was detected.
        self.assertEqual(call_count["value"], 1)


class TestCostPreflight(unittest.TestCase):

    @patch("agent.agents.manager.global_budget_status", return_value=_over_budget())
    @patch("agent.agents.manager.execute_agent")
    def test_over_budget_refuses_to_launch_the_batch(self, mock_execute, mock_budget):
        tasks = [AgentTaskRequest(agent_name="research", task="a"), AgentTaskRequest(agent_name="memory", task="b")]
        result = execute_agents_parallel(tasks, _context())
        self.assertEqual(result.status, BatchStatus.FAILED)
        mock_execute.assert_not_called()
        self.assertIn("budget", result.note.lower())

    @patch("agent.agents.manager.global_budget_status", return_value=_not_over_budget())
    @patch("agent.agents.manager.execute_agent")
    def test_within_budget_proceeds_normally(self, mock_execute, mock_budget):
        mock_execute.side_effect = lambda name, task, ctx, depth=0: _ok(name)
        tasks = [AgentTaskRequest(agent_name="research", task="a"), AgentTaskRequest(agent_name="memory", task="b")]
        result = execute_agents_parallel(tasks, _context())
        self.assertEqual(result.status, BatchStatus.ALL_SUCCEEDED)


class TestObservability(unittest.TestCase):

    @patch("agent.agents.manager.global_budget_status", return_value=_not_over_budget())
    @patch("agent.agents.manager.execute_agent")
    @patch("agent.agents.manager.log_event")
    def test_batch_started_and_completed_events_are_logged(self, mock_log, mock_execute, mock_budget):
        mock_execute.side_effect = lambda name, task, ctx, depth=0: _ok(name)
        tasks = [AgentTaskRequest(agent_name="research", task="a"), AgentTaskRequest(agent_name="memory", task="b")]
        execute_agents_parallel(tasks, _context())

        events = [call.args[0] for call in mock_log.call_args_list]
        self.assertIn("agent_batch_started", events)
        self.assertIn("agent_batch_completed", events)

    @patch("agent.agents.manager.global_budget_status", return_value=_not_over_budget())
    @patch("agent.agents.manager.execute_agent")
    @patch("agent.agents.manager.log_event")
    def test_batch_failed_event_logged_when_a_required_task_fails(self, mock_log, mock_execute, mock_budget):
        mock_execute.side_effect = lambda name, task, ctx, depth=0: _fail(name)
        with patch("agent.agents.manager.settings") as mock_settings:
            mock_settings.max_parallel_agents = 3
            mock_settings.max_agent_batch_retries = 0
            tasks = [AgentTaskRequest(agent_name="research", task="a")]
            execute_agents_parallel(tasks, _context())

        events = [call.args[0] for call in mock_log.call_args_list]
        self.assertIn("agent_batch_failed", events)

    @patch("agent.agents.manager.execute_agent")
    @patch("agent.agents.manager.log_event")
    def test_rejected_oversized_batch_is_logged(self, mock_log, mock_execute):
        with patch("agent.agents.manager.settings") as mock_settings:
            mock_settings.max_parallel_agents = 1
            tasks = [AgentTaskRequest(agent_name="research", task="a"), AgentTaskRequest(agent_name="memory", task="b")]
            execute_agents_parallel(tasks, _context())

        events = [call.args[0] for call in mock_log.call_args_list]
        self.assertIn("agent_batch_rejected", events)
        mock_execute.assert_not_called()


class TestCostAttribution(unittest.TestCase):

    @patch("agent.agents.manager.global_budget_status", return_value=_not_over_budget())
    @patch("agent.agents.manager.total_cost_for_request")
    @patch("agent.agents.manager.execute_agent")
    def test_cost_usd_is_the_delta_across_the_batch(self, mock_execute, mock_cost, mock_budget):
        mock_execute.side_effect = lambda name, task, ctx, depth=0: _ok(name)
        mock_cost.side_effect = [1.0, 1.5]  # before, after
        tasks = [AgentTaskRequest(agent_name="research", task="a")]
        result = execute_agents_parallel(tasks, _context())
        self.assertAlmostEqual(result.cost_usd, 0.5)

    @patch("agent.agents.manager.global_budget_status", return_value=_not_over_budget())
    @patch("agent.agents.manager.execute_agent")
    def test_each_task_results_in_exactly_one_execute_agent_call_when_successful(self, mock_execute, mock_budget):
        mock_execute.side_effect = lambda name, task, ctx, depth=0: _ok(name)
        tasks = [AgentTaskRequest(agent_name="research", task="a"), AgentTaskRequest(agent_name="memory", task="b")]
        execute_agents_parallel(tasks, _context())
        self.assertEqual(mock_execute.call_count, 2)


class TestBatchResultType(unittest.TestCase):

    @patch("agent.agents.manager.global_budget_status", return_value=_not_over_budget())
    @patch("agent.agents.manager.execute_agent")
    def test_returns_an_agent_batch_result(self, mock_execute, mock_budget):
        mock_execute.side_effect = lambda name, task, ctx, depth=0: _ok(name)
        tasks = [AgentTaskRequest(agent_name="research", task="a"), AgentTaskRequest(agent_name="memory", task="b")]
        result = execute_agents_parallel(tasks, _context())
        self.assertIsInstance(result, AgentBatchResult)
        self.assertEqual(result.request_id, "req-1")


if __name__ == "__main__":
    unittest.main()
