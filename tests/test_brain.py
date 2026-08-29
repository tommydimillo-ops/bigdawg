"""Tests for agent/brain.py's build_system_prompt() -- specifically Phase
9 M4.4's wiring of agent.history_context.build_history_context() into it.
Relies on the central test-safety bootstrap (tests/_safety.py, via the
canonical `-t .` invocation) to redirect database.memory.MEMORY_FILE and
agent.history_store.HISTORY_DB -- no per-test file isolation needed here,
matching tests/test_skills_security.py's existing
TestBuildSystemPromptSkillInjectionIsSafe precedent.

Run with: python -m unittest tests.test_brain -v
"""
import tempfile
import unittest
from unittest.mock import patch

import agent.brain as brain
import agent.history_store as history_store
from agent.brain import build_system_prompt
from config.settings import settings


class TestGreetingInstructionForbidsNarrationBeforeToolCalls(unittest.TestCase):
    """Real finding (ROADMAP.md's "'Say hi' -> two provider calls" entry,
    `.relay/report-2.md`): a bare greeting produced a doubled "Hello,
    master." in the streamed reply -- the model narrated ("let me check
    the time and weather") before calling get_system_status/get_weather,
    then produced the full templated greeting again once results came
    back, so the user saw/heard two separate replies instead of one.
    Not testable end-to-end without a real model call (this is a prompt
    instruction, not code) -- this pins the instruction's presence so it
    can't silently regress, the same way the codebase already treats
    other must-not-drift prompt content."""

    def test_prompt_forbids_narration_before_the_greeting_tool_calls(self):
        prompt = brain.BASE_SYSTEM_PROMPT
        self.assertIn("ZERO text before them", prompt)
        self.assertIn("not a stylistic question", prompt)


class IsolatedHistoryContextSettingsTestCase(unittest.TestCase):

    def setUp(self):
        self._real_history_db = history_store.HISTORY_DB
        history_store.HISTORY_DB = tempfile.mktemp(suffix=".db")

        self._real_enabled = settings.proactive_history_enabled
        self._real_budget = settings.history_context_budget_tokens
        self._real_timeout = settings.history_context_timeout_ms
        self._real_max_results = settings.history_context_max_results
        object.__setattr__(settings, "history_context_budget_tokens", 500)
        object.__setattr__(settings, "history_context_timeout_ms", 150)
        object.__setattr__(settings, "history_context_max_results", 3)

    def tearDown(self):
        history_store.HISTORY_DB = self._real_history_db
        object.__setattr__(settings, "proactive_history_enabled", self._real_enabled)
        object.__setattr__(settings, "history_context_budget_tokens", self._real_budget)
        object.__setattr__(settings, "history_context_timeout_ms", self._real_timeout)
        object.__setattr__(settings, "history_context_max_results", self._real_max_results)

    def _seed(self, prefix="quarterly budget review meeting"):
        history_store.initialize_history_store(db_path=history_store.HISTORY_DB)
        history_store.create_session("chat", session_id="s1", db_path=history_store.HISTORY_DB)
        history_store.record_turn(
            "s1", "user", prefix, request_id="req-seed", db_path=history_store.HISTORY_DB,
        )


class TestDisabledPathIsByteIdenticalToNotWiredIn(IsolatedHistoryContextSettingsTestCase):
    """The important test per the M4.4 wiring plan: proves the disabled
    (default) path is not merely "doesn't visibly break" but produces the
    EXACT same prompt as if agent.history_context were never called from
    build_system_prompt() at all -- not a reconstruction of brain.py's
    other assembly logic (that would just duplicate it and risk drifting
    out of sync), but a direct comparison against the real function with
    build_history_context stubbed to a no-op standing in for "this code
    path doesn't exist"."""

    class _AlwaysEmptyHistoryContext:
        prompt_text = ""

    def test_byte_identical_with_and_without_the_call_present(self):
        object.__setattr__(settings, "proactive_history_enabled", False)
        self._seed()  # even with real matching data sitting in the store

        real_prompt = build_system_prompt("quarterly budget review", request_id="req-1", state=None)

        with patch.object(brain, "build_history_context", return_value=self._AlwaysEmptyHistoryContext()):
            prompt_without_the_call_at_all = build_system_prompt(
                "quarterly budget review", request_id="req-1", state=None,
            )

        self.assertEqual(real_prompt, prompt_without_the_call_at_all)

    def test_byte_identical_with_no_history_data_at_all(self):
        # Same proof, simpler precondition: no history.db even exists.
        object.__setattr__(settings, "proactive_history_enabled", False)

        real_prompt = build_system_prompt("anything", request_id="req-2", state=None)
        with patch.object(brain, "build_history_context", return_value=self._AlwaysEmptyHistoryContext()):
            prompt_without_the_call_at_all = build_system_prompt("anything", request_id="req-2", state=None)

        self.assertEqual(real_prompt, prompt_without_the_call_at_all)


class TestEnabledPathInjectsTheBlock(IsolatedHistoryContextSettingsTestCase):

    def test_block_present_with_provenance_and_between_patterns_and_lessons(self):
        object.__setattr__(settings, "proactive_history_enabled", True)
        self._seed()

        prompt = build_system_prompt("quarterly budget review", request_id="req-3", state=None)

        self.assertIn("RELEVANT PAST CONVERSATIONS", prompt)
        self.assertIn("chat", prompt)

        # Position: after PATTERNS (if present) / at least before STANDING
        # RULES, matching where build_system_prompt() calls it.
        history_pos = prompt.find("RELEVANT PAST CONVERSATIONS")
        lessons_pos = prompt.find("STANDING RULES")
        self.assertGreater(history_pos, -1)
        if lessons_pos != -1:
            self.assertLess(history_pos, lessons_pos)

    def test_disabled_produces_no_block_even_with_real_matching_data(self):
        object.__setattr__(settings, "proactive_history_enabled", False)
        self._seed()
        prompt = build_system_prompt("quarterly budget review", request_id="req-4", state=None)
        self.assertNotIn("RELEVANT PAST CONVERSATIONS", prompt)

    def test_budget_is_honoured_inside_the_real_prompt(self):
        object.__setattr__(settings, "proactive_history_enabled", True)
        object.__setattr__(settings, "history_context_budget_tokens", 0)
        self._seed()
        prompt = build_system_prompt("quarterly budget review", request_id="req-5", state=None)
        self.assertNotIn("RELEVANT PAST CONVERSATIONS", prompt)


class TestRetrievalFailureCannotBreakAPrompt(IsolatedHistoryContextSettingsTestCase):
    """Mirrors M4.2's capture philosophy on the retrieval side: a history
    failure must never prevent build_system_prompt() from returning a
    valid prompt for the rest of a real conversation turn."""

    def _assert_prompt_still_built(self, exc):
        object.__setattr__(settings, "proactive_history_enabled", True)
        with patch("agent.history_context.history_store.search_history", side_effect=exc):
            prompt = build_system_prompt("anything", request_id="req-err", state=None)
        self.assertIsInstance(prompt, str)
        self.assertGreater(len(prompt), 0)
        self.assertNotIn("RELEVANT PAST CONVERSATIONS", prompt)

    def test_history_unavailable_does_not_break_the_prompt(self):
        self._assert_prompt_still_built(history_store.HistoryUnavailable("x"))

    def test_history_schema_error_does_not_break_the_prompt(self):
        self._assert_prompt_still_built(history_store.HistorySchemaError("x"))

    def test_history_corruption_does_not_break_the_prompt(self):
        self._assert_prompt_still_built(history_store.HistoryCorruption("x"))

    def test_history_busy_does_not_break_the_prompt(self):
        self._assert_prompt_still_built(history_store.HistoryBusy("x"))

    def test_history_validation_error_does_not_break_the_prompt(self):
        self._assert_prompt_still_built(history_store.HistoryValidationError("x"))

    def test_history_unsupported_runtime_does_not_break_the_prompt(self):
        self._assert_prompt_still_built(history_store.HistoryUnsupportedRuntime("x"))


if __name__ == "__main__":
    unittest.main()
