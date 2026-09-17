"""Tests for agent/agents/memory.py -- MemoryAgent is a thin wrapper
around the EXISTING agent.memory_agent.remember()/recall(), so only that
call boundary is mocked here; nothing in this file writes to the real
memory store.

Run with: python -m unittest tests.test_agents_memory -v
"""
import unittest
from unittest.mock import patch

from agent.agents.memory import MemoryAgent
from agent.request_context import RequestContext


class TestMemoryAgent(unittest.TestCase):

    def setUp(self):
        self.agent = MemoryAgent()
        self.context = RequestContext.create("test", source="test")

    def test_metadata(self):
        self.assertEqual(self.agent.metadata.name, "memory")

    @patch("agent.agents.memory.remember")
    def test_remember_that_phrasing_stores_the_stripped_fact(self, mock_remember):
        mock_remember.return_value = "I'll remember that I prefer dark mode."
        result = self.agent.execute("Remember that I prefer dark mode.", self.context)

        mock_remember.assert_called_once_with("notes", "I prefer dark mode.")
        self.assertTrue(result.success)
        self.assertEqual(result.result, "I'll remember that I prefer dark mode.")

    @patch("agent.agents.memory.remember")
    def test_remember_to_phrasing_stores_the_stripped_fact(self, mock_remember):
        mock_remember.return_value = "ok"
        self.agent.execute("Remember to call mom on Friday.", self.context)
        mock_remember.assert_called_once_with("notes", "call mom on Friday.")

    @patch("agent.agents.memory.recall")
    def test_recall_phrasing_reads_instead_of_writes(self, mock_recall):
        mock_recall.return_value = "I prefer dark mode."
        result = self.agent.execute("recall my preferences", self.context)

        mock_recall.assert_called_once_with("notes")
        self.assertTrue(result.success)
        self.assertEqual(result.result, "I prefer dark mode.")

    @patch("agent.agents.memory.recall")
    def test_what_do_i_phrasing_is_a_recall(self, mock_recall):
        mock_recall.return_value = "..."
        self.agent.execute("what do i prefer for dark mode", self.context)
        mock_recall.assert_called_once()

    @patch("agent.agents.memory.remember")
    def test_execute_catches_exceptions(self, mock_remember):
        mock_remember.side_effect = RuntimeError("disk full")
        result = self.agent.execute("Remember that I prefer dark mode.", self.context)
        self.assertFalse(result.success)
        self.assertIn("RuntimeError", result.error)

    @patch("agent.agents.memory.remember")
    def test_a_safety_filter_refusal_is_reported_as_a_failure(self, mock_remember):
        # Regression: remember()'s refusal string used to be wrapped in
        # AgentResult(success=True) unconditionally -- a memory the
        # content-safety filter (agent/memory/safety.py) refused to store
        # was reported as a successful agent run.
        mock_remember.return_value = "Didn't save that: looks like it contains a credential or secret, which is never stored as a memory"
        result = self.agent.execute("Remember that api_key: sk-abcdefghijklmnopqrstuvwxyz123456", self.context)
        self.assertFalse(result.success)
        self.assertIn("Didn't save that", result.error)
        self.assertEqual(result.result, "")


class TestRememberPermissionGate(unittest.TestCase):
    """MemoryAgent bypass audit (ROADMAP.md): the remember() call now
    routes through the SAME agent.autonomy.should_request_confirmation
    function agent/executor.py's _run_tool and agent/agents/coding.py's
    _write_file already use -- mirrors tests.test_agents_coding_enabled.
    TestWriteFilePermissionGate. recall() is deliberately NOT gated (see
    agent/agents/memory.py's module comment), so it's covered here too,
    to pin that as a tested decision rather than an untested assumption."""

    def setUp(self):
        self.agent = MemoryAgent()

    @patch("agent.agents.memory.remember")
    def test_default_autonomy_level_allows_the_remember(self, mock_remember):
        mock_remember.return_value = "I'll remember that I prefer dark mode."
        context = RequestContext.create("t", source="agent_worker")  # autonomy_level defaults to settings.autonomy_level (4)
        result = self.agent.execute("Remember that I prefer dark mode.", context)
        self.assertTrue(result.success)
        mock_remember.assert_called_once()

    @patch("agent.agents.memory.remember")
    def test_low_autonomy_level_denies_the_remember(self, mock_remember):
        context = RequestContext.create("t", source="agent_worker")
        context.autonomy_level = 1  # threshold 0 -- permission_level 1 needs confirmation
        result = self.agent.execute("Remember that I prefer dark mode.", context)
        self.assertFalse(result.success)
        self.assertEqual(result.result, "")
        self.assertEqual(result.error, "not permitted at the current autonomy level (deny)")
        mock_remember.assert_not_called()

    @patch("agent.agents.memory.remember")
    def test_low_autonomy_level_never_hangs_waiting_for_a_confirmation_that_cannot_come(self, mock_remember):
        # source="agent_worker" is in agent.autonomy's non-interactive-
        # sources set: a verdict that would otherwise mean "pause and
        # ask" must resolve immediately to a denial, not block or
        # silently proceed. This test's own completion (it doesn't time
        # out) is part of what it proves.
        context = RequestContext.create("t", source="agent_worker")
        context.autonomy_level = 0
        result = self.agent.execute("Remember that I prefer dark mode.", context)
        self.assertFalse(result.success)
        mock_remember.assert_not_called()

    @patch("agent.agents.memory.recall")
    def test_recall_is_unaffected_by_autonomy_level(self, mock_recall):
        mock_recall.return_value = "..."
        context = RequestContext.create("t", source="agent_worker")
        context.autonomy_level = 0  # would deny a remember; recall stays ungated
        result = self.agent.execute("recall my preferences", context)
        self.assertTrue(result.success)
        mock_recall.assert_called_once()


if __name__ == "__main__":
    unittest.main()
