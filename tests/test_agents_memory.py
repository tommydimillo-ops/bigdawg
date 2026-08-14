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


if __name__ == "__main__":
    unittest.main()
