"""Security-focused tests for Phase 5 (persistent execution, cancellation,
cross-interface state) -- exercising the real mechanisms, matching this
project's established policy (see tests/test_phase4_security.py) of
testing the actual code path rather than just the pure decision logic in
isolation.

Three guarantees this covers:
1. cancel_request/request_cancel can only ever affect a request running
   in the SAME process -- there is no code path from a request_id string
   to an OS-level process/thread kill.
2. Cancellation is purely additive on top of the existing autonomy/
   permission gates -- it can skip an already-cleared tool call, but
   can't itself grant, skip, or weaken a confirmation requirement.
3. Execution history never contains a raw secret, even when the
   triggering request or its error text contained one.

Run with: python -m unittest tests.test_phase5_security -v
"""
import inspect
import os
import tempfile
import unittest

import tools.schemas  # noqa: F401 -- populates the registry
import agent.execution_history as execution_history
import agent.jarvis_state as jarvis_state
from agent.cancellation import request_cancel
from agent.execution_state import ExecutionState
from agent.executor import _run_tool
from agent.request_context import RequestContext

# _run_tool writes cross-interface status via agent.jarvis_state on every
# real dispatch -- redirected module-wide (matching every other file-
# backed store's tests in this project) so exercising it here doesn't
# clobber the real ~/Library/.../jarvis_state.json.
_real_state_file = jarvis_state.STATE_FILE


def setUpModule():
    jarvis_state.STATE_FILE = tempfile.mktemp(suffix=".json")


def tearDownModule():
    jarvis_state.STATE_FILE = _real_state_file


class TestCancellationIsProcessScopedOnly(unittest.TestCase):
    """agent.cancellation has no way to reach anything outside this
    process's own agent.execution_state._active dict -- structural checks,
    not just "it returned False for a made-up id"."""

    def test_cancellation_module_never_imports_process_or_os_kill_primitives(self):
        import agent.cancellation as cancellation_module
        source = inspect.getsource(cancellation_module)
        for forbidden in ("os.kill", "subprocess", "signal.", "psutil"):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, source)

    def test_request_cancel_only_consults_the_local_active_registry(self):
        import agent.cancellation as cancellation_module
        tree_source = inspect.getsource(cancellation_module.request_cancel)
        self.assertIn("cancel_active", tree_source)

    def test_a_request_id_that_was_never_registered_here_cannot_be_cancelled(self):
        # Simulates "a request_id belonging to another process/session" --
        # from this process's point of view that's indistinguishable from
        # any other unknown id, and must resolve to a safe no-op.
        self.assertFalse(request_cancel("some-other-processes-request-id-guess"))


class TestCancellationCannotBypassPermissionGates(unittest.TestCase):
    """A cancelled ExecutionState must make _run_tool skip running the
    tool at all -- it must never be usable as a side-channel to force a
    tool through that would otherwise have been blocked or held for
    confirmation."""

    def test_cancelled_state_skips_dispatch_entirely_even_for_an_otherwise_allowed_tool(self):
        state = ExecutionState(max_iterations=8)
        state.cancel()
        result = _run_tool("get_system_status", {}, source="chat", state=state)
        self.assertIn("Skipped", result)
        self.assertIn("cancelled", result)
        self.assertEqual(state.tools_executed, [])

    def test_cancelling_a_request_awaiting_confirmation_does_not_auto_execute_it(self):
        ctx = RequestContext.create("delete something", source="chat")
        ctx.autonomy_level = 0  # lowest level -- confirmation required for nearly everything
        state = ExecutionState(max_iterations=8)
        # First call parks it on WAITING_FOR_CONFIRMATION (real gate, not
        # a hard-coded assumption).
        first = _run_tool("computer_confirm_action", {"description": "test"}, source="chat", context=ctx, state=state)
        self.assertIn("OK first", first)
        # Now cancel instead of confirming.
        state.cancel()
        second = _run_tool("computer_confirm_action", {"description": "test"}, source="chat", context=ctx, state=state)
        self.assertNotIn("Confirmed action executed", second)
        self.assertIn("cancelled", second)

    def test_hard_gates_still_apply_when_not_cancelled(self):
        # Regression check for the new cancellation check added ahead of
        # the pre-existing hard gates in _run_tool -- confirms it didn't
        # accidentally swallow or reorder the scheduled +
        # requires_live_confirmation gate for the ordinary, not-cancelled
        # case.
        ctx = RequestContext.create("x", source="scheduled")
        state = ExecutionState(max_iterations=8)
        result = _run_tool("confirm_login", {"site": "test"}, source="scheduled", context=ctx, state=state)
        self.assertIn("Skipped", result)
        self.assertIn("live conversation", result)


class TestHistoryNeverContainsSecrets(unittest.TestCase):

    def setUp(self):
        self._real_history_file = execution_history.HISTORY_FILE
        execution_history.HISTORY_FILE = tempfile.mktemp(suffix=".json")

    def tearDown(self):
        for path in (execution_history.HISTORY_FILE, f"{execution_history.HISTORY_FILE}.tmp"):
            if os.path.exists(path):
                os.remove(path)
        execution_history.HISTORY_FILE = self._real_history_file

    SECRET = "sk-abcdefghijklmnopqrstuvwxyz0123456789"

    def test_secret_in_the_request_text_never_reaches_disk(self):
        state = ExecutionState(max_iterations=8)
        state.finish(result="ok")
        execution_history.record_completed("r1", f"remember my api_key: {self.SECRET}", state)
        with open(execution_history.HISTORY_FILE) as f:
            raw = f.read()
        self.assertNotIn(self.SECRET, raw)

    def test_secret_in_a_recorded_error_never_reaches_disk(self):
        state = ExecutionState(max_iterations=8)
        state.error = f"login failed, token: {self.SECRET}"
        state.finish(failed=True, error=state.error)
        execution_history.record_failed("r2", "log me in", state)
        with open(execution_history.HISTORY_FILE) as f:
            raw = f.read()
        self.assertNotIn(self.SECRET, raw)

    def test_raw_tool_inputs_are_never_persisted_at_all(self):
        # Only tool NAMES are recorded (state.tools_executed), never the
        # arguments they were called with -- structurally true by
        # ExecutionRecord having no such field, confirmed here with a
        # real record that did execute a tool.
        state = ExecutionState(max_iterations=8)
        state.record_tool("send_email", result_preview="sent", ok=True)
        state.finish(result="ok")
        execution_history.record_completed("r3", "email my boss my password: hunter2", state)
        with open(execution_history.HISTORY_FILE) as f:
            raw = f.read()
        self.assertNotIn("hunter2", raw)


if __name__ == "__main__":
    unittest.main()
