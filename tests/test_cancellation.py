"""Tests for agent/cancellation.py -- the formal request_cancel/
get_request_status API built on top of agent.execution_state's
process-local active-execution registry.

Run with: python -m unittest tests.test_cancellation -v
"""
import unittest

from agent.cancellation import get_request_status, request_cancel
from agent.execution_state import ExecutionState, register_active, unregister_active


class TestRequestCancel(unittest.TestCase):

    def tearDown(self):
        unregister_active("test-cancel-id")

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
