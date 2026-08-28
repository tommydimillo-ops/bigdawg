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

    def test_agent_worker_source_denies_when_would_otherwise_confirm(self):
        # Phase 10: a coworker-agent worker subprocess has the exact same
        # "no live person to answer a CONFIRM verdict" property a
        # scheduled task does -- it runs to completion inside its own OS
        # process with no mid-task round trip back to the user.
        ctx = ExecutionContext(source="agent_worker")
        self.assertEqual(should_request_confirmation("run_python", 1, ctx), Decision.DENY)

    def test_agent_worker_source_still_allows_within_threshold(self):
        ctx = ExecutionContext(source="agent_worker")
        self.assertEqual(should_request_confirmation("get_weather", 1, ctx), Decision.ALLOW)


class TestPermissionLevelOverride(unittest.TestCase):
    """Phase 10's chokepoint: a real action that is not, and must not
    become, a model-callable registered tool (agent/agents/coding.py's
    write_file) still needs a real permission-level-vs-autonomy
    decision, through the SAME function _run_tool already calls -- not a
    second, independently-written copy of this logic."""

    def test_override_skips_the_registry_lookup(self):
        # "not_a_real_tool" is unregistered -- would normally always
        # CONFIRM (test_unregistered_tool_always_confirms above) -- but
        # an explicit override bypasses that lookup entirely.
        self.assertEqual(
            should_request_confirmation("not_a_real_tool", 4, permission_level=2),
            Decision.ALLOW,
        )

    def test_override_still_respects_the_autonomy_threshold(self):
        self.assertEqual(
            should_request_confirmation("not_a_real_tool", 1, permission_level=2),
            Decision.CONFIRM,
        )

    def test_override_combined_with_a_non_interactive_source_denies(self):
        ctx = ExecutionContext(source="agent_worker")
        self.assertEqual(
            should_request_confirmation("not_a_real_tool", 1, ctx, permission_level=2),
            Decision.DENY,
        )

    def test_existing_registered_tools_are_completely_unaffected(self):
        # The override is opt-in per call -- every existing caller that
        # doesn't pass it keeps the exact original registry-lookup
        # behavior, for every level, byte for byte.
        for level in range(6):
            self.assertEqual(
                should_request_confirmation("send_email", level),
                should_request_confirmation("send_email", level, permission_level=None),
            )


class TestVoiceSourceSafety(unittest.TestCase):

    def test_voice_reminder_always_confirms_at_max_autonomy(self):
        ctx = ExecutionContext(source="voice")
        self.assertEqual(
            should_request_confirmation("add_reminder", 4, ctx),
            Decision.CONFIRM,
        )

    def test_voice_open_browser_always_confirms_at_max_autonomy(self):
        # A misheard wake word must not be able to launch a real browser
        # against a real (possibly attacker- or ad-influenced, if the
        # transcript came from a video) destination unconfirmed -- see
        # agent/autonomy.py's _VOICE_ALWAYS_CONFIRMS docstring for the
        # live incident this was added for.
        ctx = ExecutionContext(source="voice")
        self.assertEqual(
            should_request_confirmation("open_browser", 4, ctx),
            Decision.CONFIRM,
        )

    def test_voice_consult_coworker_agent_always_confirms_at_max_autonomy(self):
        # consult_coworker_agent is the entry point into ResearchAgent's
        # own internal tool loop, which dispatches open_browser directly
        # without going through this permission system at all -- gating
        # the entry point is what actually protects that path.
        ctx = ExecutionContext(source="voice")
        self.assertEqual(
            should_request_confirmation("consult_coworker_agent", 4, ctx),
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

    def test_typed_chat_open_browser_keeps_existing_behavior(self):
        ctx = ExecutionContext(source="chat")
        self.assertEqual(
            should_request_confirmation("open_browser", 4, ctx),
            Decision.ALLOW,
        )

    def test_typed_chat_consult_coworker_agent_keeps_existing_behavior(self):
        ctx = ExecutionContext(source="chat")
        self.assertEqual(
            should_request_confirmation("consult_coworker_agent", 4, ctx),
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
