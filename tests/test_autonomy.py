"""Tests for agent/autonomy.py -- the autonomy decision engine and its
generic pending-confirmation ledger. Pure/deterministic, no API calls, no
memory-file isolation needed (doesn't touch database.memory).

Run with: python -m unittest tests.test_autonomy -v
"""
import unittest

import tools.schemas  # noqa: F401 -- populates the registry
from agent.autonomy import (
    Decision,
    ExecutionContext,
    is_confirmed,
    request_confirmation,
    should_request_confirmation,
)


class TestAutonomyLevels(unittest.TestCase):

    def test_level_0_confirms_even_read_only(self):
        self.assertEqual(should_request_confirmation("get_weather", 0), Decision.CONFIRM)

    def test_level_1_allows_read_only_confirms_rest(self):
        self.assertEqual(should_request_confirmation("get_weather", 1), Decision.ALLOW)  # perm 0
        self.assertEqual(should_request_confirmation("add_reminder", 1), Decision.CONFIRM)  # perm 1

    def test_level_2_allows_safe_local_confirms_rest(self):
        self.assertEqual(should_request_confirmation("add_reminder", 2), Decision.ALLOW)  # perm 1
        self.assertEqual(should_request_confirmation("run_python", 2), Decision.CONFIRM)  # perm 2

    def test_level_3_allows_code_execution_confirms_rest(self):
        self.assertEqual(should_request_confirmation("run_python", 3), Decision.ALLOW)  # perm 2
        self.assertEqual(should_request_confirmation("send_email", 3), Decision.CONFIRM)  # perm 3

    def test_level_4_allows_everything_but_destructive(self):
        self.assertEqual(should_request_confirmation("send_email", 4), Decision.ALLOW)  # perm 3
        self.assertEqual(should_request_confirmation("computer_confirm_action", 4), Decision.CONFIRM)  # perm 5

    def test_unknown_level_falls_back_to_highest_defined(self):
        # An out-of-range level shouldn't crash or silently allow
        # everything -- falls back to the most permissive *defined*
        # level, not "allow all."
        self.assertEqual(
            should_request_confirmation("computer_confirm_action", 99),
            should_request_confirmation("computer_confirm_action", 4),
        )

    def test_unregistered_tool_always_confirms(self):
        self.assertEqual(should_request_confirmation("not_a_real_tool", 4), Decision.CONFIRM)


class TestScheduledSourceDeniesInsteadOfConfirms(unittest.TestCase):

    def test_scheduled_source_denies_when_would_otherwise_confirm(self):
        ctx = ExecutionContext(source="scheduled")
        self.assertEqual(should_request_confirmation("run_python", 1, ctx), Decision.DENY)

    def test_scheduled_source_still_allows_within_threshold(self):
        ctx = ExecutionContext(source="scheduled")
        self.assertEqual(should_request_confirmation("get_weather", 1, ctx), Decision.ALLOW)

    def test_chat_source_confirms_not_denies(self):
        ctx = ExecutionContext(source="chat")
        self.assertEqual(should_request_confirmation("run_python", 1, ctx), Decision.CONFIRM)


class TestVoiceSourceSafety(unittest.TestCase):

    def test_voice_reminder_always_confirms_at_max_autonomy(self):
        ctx = ExecutionContext(source="voice")
        self.assertEqual(
            should_request_confirmation("add_reminder", 4, ctx),
            Decision.CONFIRM,
        )

    def test_voice_read_only_tool_still_runs_normally(self):
        ctx = ExecutionContext(source="voice")
        self.assertEqual(
            should_request_confirmation("get_weather", 4, ctx),
            Decision.ALLOW,
        )

    def test_typed_chat_reminder_keeps_existing_behavior(self):
        ctx = ExecutionContext(source="chat")
        self.assertEqual(
            should_request_confirmation("add_reminder", 4, ctx),
            Decision.ALLOW,
        )


class TestPendingConfirmationLedger(unittest.TestCase):

    def test_not_confirmed_before_request(self):
        self.assertFalse(is_confirmed("run_python", {"code": "print('unique test marker 1')"}))

    def test_confirmed_after_request(self):
        tool_input = {"code": "print('unique test marker 2')"}
        request_confirmation("run_python", tool_input)
        self.assertTrue(is_confirmed("run_python", tool_input))

    def test_confirmation_is_one_time_use(self):
        tool_input = {"code": "print('unique test marker 3')"}
        request_confirmation("run_python", tool_input)
        self.assertTrue(is_confirmed("run_python", tool_input))
        self.assertFalse(is_confirmed("run_python", tool_input))

    def test_confirmation_is_specific_to_the_exact_input(self):
        request_confirmation("run_python", {"code": "print('a')"})
        self.assertFalse(is_confirmed("run_python", {"code": "print('b')"}))

    def test_confirmation_is_specific_to_the_tool(self):
        request_confirmation("run_python", {"x": 1})
        self.assertFalse(is_confirmed("add_reminder", {"x": 1}))


if __name__ == "__main__":
    unittest.main()
