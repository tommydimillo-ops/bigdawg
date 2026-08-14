"""Tests for agent/context.py -- relevance-filtered, budget-limited
pattern retrieval for the system prompt. Isolates database.memory.MEMORY_FILE
to a temp file, same as tests/test_memory.py.

Run with: python -m unittest tests.test_context -v
"""
import os
import tempfile
import unittest

import database.memory as dbmem


class _IsolatedMemoryFile(unittest.TestCase):

    def setUp(self):
        self._real_memory_file = dbmem.MEMORY_FILE
        self._temp_file = tempfile.mktemp(suffix=".json")
        dbmem.MEMORY_FILE = self._temp_file

    def tearDown(self):
        dbmem.MEMORY_FILE = self._real_memory_file
        if os.path.exists(self._temp_file):
            os.remove(self._temp_file)
        tmp = f"{self._temp_file}.tmp"
        if os.path.exists(tmp):
            os.remove(tmp)


class TestBuildContext(_IsolatedMemoryFile):

    def test_empty_input_returns_empty_context(self):
        from agent.context import build_context
        context = build_context("")
        self.assertEqual(context.prompt_text, "")
        self.assertEqual(context.retrieved, [])

    def test_no_matching_patterns_returns_empty(self):
        from agent.context import build_context
        from agent.memory.manager import remember
        from agent.memory.models import MemoryType

        remember("User often asks about music recommendations", type=MemoryType.PATTERN)
        context = build_context("what's the weather like today")

        self.assertEqual(context.prompt_text, "")

    def test_relevant_pattern_is_included(self):
        from agent.context import build_context
        from agent.memory.manager import remember
        from agent.memory.models import MemoryType

        remember("User often asks to play chill music but no library match exists", type=MemoryType.PATTERN)
        context = build_context("play some chill music please")

        self.assertIn("chill music", context.prompt_text)
        self.assertEqual(len(context.retrieved), 1)

    def test_irrelevant_pattern_excluded_relevant_one_included(self):
        from agent.context import build_context
        from agent.memory.manager import remember
        from agent.memory.models import MemoryType

        remember("User often asks about music recommendations", type=MemoryType.PATTERN)
        remember("User frequently checks the weather before leaving", type=MemoryType.PATTERN)

        context = build_context("what's the weather going to be like")

        self.assertIn("weather", context.prompt_text)
        self.assertNotIn("music", context.prompt_text)

    def test_facts_and_lessons_are_not_included_by_context(self):
        # build_context is scoped to PATTERN only -- facts were never
        # auto-injected (only on-demand via recall_facts), and lessons
        # stay always-all-included via lessons_as_prompt_text() directly,
        # not subject to a relevance cutoff at all.
        from agent.context import build_context
        from agent.memory.manager import remember
        from agent.memory.models import MemoryType

        remember("likes jazz music", type=MemoryType.FACT)
        remember("always greet with good morning", type=MemoryType.LESSON)

        context = build_context("play some jazz music, good morning")

        self.assertEqual(context.prompt_text, "")

    def test_reason_is_attached_to_each_retrieved_memory(self):
        from agent.context import build_context
        from agent.memory.manager import remember
        from agent.memory.models import MemoryType

        remember("User often plays chill music at night", type=MemoryType.PATTERN)
        context = build_context("play some chill music")

        self.assertEqual(len(context.retrieved), 1)
        self.assertTrue(context.retrieved[0].reason)
        self.assertEqual(context.retrieved[0].memory.type.value, "pattern")


class TestContextBudget(_IsolatedMemoryFile):

    def test_budget_limits_number_of_patterns_included(self):
        from agent.context import build_context
        from agent.memory.manager import remember
        from agent.memory.models import MemoryType

        for i in range(10):
            remember(f"User pattern number {i} about coffee habits", type=MemoryType.PATTERN)

        context = build_context("tell me about coffee habits", max_memories=3)
        self.assertEqual(len(context.retrieved), 3)

    def test_zero_budget_returns_nothing(self):
        from agent.context import build_context
        from agent.memory.manager import remember
        from agent.memory.models import MemoryType

        remember("User likes coffee in the morning", type=MemoryType.PATTERN)
        context = build_context("tell me about coffee", max_memories=0)

        self.assertEqual(context.prompt_text, "")

    def test_default_budget_comes_from_settings(self):
        from agent.context import build_context
        from agent.memory.manager import remember
        from agent.memory.models import MemoryType
        from config.settings import settings

        for i in range(settings.context_memory_budget + 5):
            remember(f"User coffee pattern {i}", type=MemoryType.PATTERN)

        context = build_context("coffee")
        self.assertLessEqual(len(context.retrieved), settings.context_memory_budget)


class TestProfileContext(_IsolatedMemoryFile):

    def test_profile_memories_are_bounded_and_separate_from_patterns(self):
        from agent.context import build_profile_context
        from agent.memory.manager import remember
        from agent.memory.models import MemoryType

        for index in range(5):
            remember(
                f"Profile detail {index}", type=MemoryType.PROFILE,
                tags=[f"profile-{index}"],
            )
        remember("User asks about coffee", type=MemoryType.PATTERN)

        context = build_profile_context(max_memories=2)

        self.assertEqual(len(context.retrieved), 2)
        self.assertTrue(all(r.memory.type == MemoryType.PROFILE for r in context.retrieved))


if __name__ == "__main__":
    unittest.main()
