"""Tests for agent/agents/research.py -- ResearchAgent is a thin wrapper
around the EXISTING agent.research_agent.research(), so only that call
boundary is mocked here; nothing in this file makes a real network call.

Run with: python -m unittest tests.test_agents_research -v
"""
import unittest
from unittest.mock import patch

from agent.agents.research import ResearchAgent
from agent.request_context import RequestContext


class TestResearchAgent(unittest.TestCase):

    def setUp(self):
        self.agent = ResearchAgent()
        self.context = RequestContext.create("Research the best laptops.", source="test")

    def test_metadata(self):
        self.assertEqual(self.agent.metadata.name, "research")
        self.assertTrue(self.agent.metadata.enabled)

    @patch("agent.agents.research.research")
    def test_execute_wraps_the_existing_function(self, mock_research):
        mock_research.return_value = "The best laptops under $1000 are..."
        result = self.agent.execute("Research the best laptops.", self.context)

        mock_research.assert_called_once_with("Research the best laptops.")
        self.assertTrue(result.success)
        self.assertEqual(result.result, "The best laptops under $1000 are...")
        self.assertEqual(result.agent_name, "research")
        self.assertEqual(result.request_id, self.context.request_id)

    @patch("agent.agents.research.research")
    def test_execute_catches_exceptions(self, mock_research):
        mock_research.side_effect = RuntimeError("network down")
        result = self.agent.execute("Research the best laptops.", self.context)

        self.assertFalse(result.success)
        self.assertIn("RuntimeError", result.error)
        self.assertEqual(result.result, "")

    def test_can_handle_trusts_the_router(self):
        self.assertTrue(self.agent.can_handle("anything at all"))
        self.assertFalse(self.agent.can_handle(""))


if __name__ == "__main__":
    unittest.main()
