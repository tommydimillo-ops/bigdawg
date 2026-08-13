"""Tests for agent/cancellation.py -- the formal request_cancel/
get_request_status API built on top of agent.execution_state's
process-local active-execution registry.

Run with: python -m unittest tests.test_cancellation -v
"""
import unittest
import os
import tempfile

import agent.cancellation as cancellation
import agent.jarvis_state as jarvis_state
from agent.cancellation import get_request_status, request_cancel
from agent.execution_state import ExecutionStatus
from agent.execution_state import ExecutionState, register_active, unregister_active


class TestRequestCancel(unittest.TestCase):

    def setUp(self):
        self._real_cancel_dir = cancellation.CANCEL_DIR
        self._real_state_file = jarvis_state.STATE_FILE
        self.temp_dir = tempfile.TemporaryDirectory()
        cancellation.CANCEL_DIR = os.path.join(self.temp_dir.name, "cancellations")
        jarvis_state.STATE_FILE = os.path.join(self.temp_dir.name, "state.json")

    def tearDown(self):
        unregister_active("test-cancel-id")
        cancellation.CANCEL_DIR = self._real_cancel_dir
        jarvis_state.STATE_FILE = self._real_state_file
        self.temp_dir.cleanup()

    def test_cancels_a_real_active_request(self):
        state = ExecutionState(max_iterations=8)
        register_active("test-cancel-id", state)
        result = request_cancel("test-cancel-id")
        self.assertTrue(result)
        self.assertTrue(state.cancelled)

    def test_unknown_request_id_returns_false_not_an_error(self):
        # A request_id that never existed, already finished, or belongs
        # to a different process should be a safe, quiet no-op -- there's
        # nothing here for the caller to have gotten wrong.
        self.assertFalse(request_cancel("not-a-real-request-id"))

    def test_empty_string_request_id_returns_false(self):
        self.assertFalse(request_cancel(""))

    def test_cancelling_twice_is_safe(self):
        state = ExecutionState(max_iterations=8)
        register_active("test-cancel-id", state)
        self.assertTrue(request_cancel("test-cancel-id"))
        self.assertTrue(request_cancel("test-cancel-id"))
        self.assertTrue(state.cancelled)

    def test_cancels_request_owned_by_another_process(self):
        jarvis_state.set_status(
            ExecutionStatus.EXECUTING,
            active_request_id="remote-request",
            current_task="remote work",
        )
        self.assertTrue(request_cancel("remote-request"))
        self.assertTrue(cancellation.cancellation_requested("remote-request"))

    def test_cannot_create_marker_for_unadvertised_request(self):
        jarvis_state.set_status(ExecutionStatus.EXECUTING, active_request_id="real-request")
        self.assertFalse(request_cancel("made-up-request"))
        self.assertFalse(cancellation.cancellation_requested("made-up-request"))

    def test_rejects_request_id_path_traversal(self):
        jarvis_state.set_status(ExecutionStatus.EXECUTING, active_request_id="../escape")
        self.assertFalse(request_cancel("../escape"))

    def test_clear_cancellation_removes_marker(self):
        jarvis_state.set_status(ExecutionStatus.EXECUTING, active_request_id="remote-request")
        self.assertTrue(request_cancel("remote-request"))
        cancellation.clear_cancellation("remote-request")
        self.assertFalse(cancellation.cancellation_requested("remote-request"))


class TestGetRequestStatus(unittest.TestCase):

    def tearDown(self):
        unregister_active("test-cancel-id")

    def test_returns_the_real_state_for_an_active_request(self):
        state = ExecutionState(max_iterations=8)
        register_active("test-cancel-id", state)
        self.assertIs(get_request_status("test-cancel-id"), state)

    def test_returns_none_for_unknown_id(self):
        self.assertIsNone(get_request_status("not-a-real-request-id"))


if __name__ == "__main__":
    unittest.main()
