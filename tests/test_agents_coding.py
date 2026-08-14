"""Tests for agent/agents/coding.py -- CodingAgent executes nothing this
phase (no unrestricted shell/file access); it always defers to the
ordinary executor. These tests pin exactly that: execute() must never
touch a tool, a shell, or a model client.

Run with: python -m unittest tests.test_agents_coding -v
"""
import unittest

from agent.agents.coding import CodingAgent
from agent.request_context import RequestContext


class TestCodingAgent(unittest.TestCase):

    def setUp(self):
        self.agent = CodingAgent()
        self.context = RequestContext.create("Fix this Python error.", source="test")

    def test_metadata(self):
        self.assertEqual(self.agent.metadata.name, "coding")
        self.assertTrue(self.agent.metadata.enabled)

    def test_execute_always_defers_no_execution(self):
        result = self.agent.execute("Fix this Python error.", self.context)
        self.assertTrue(result.success)
        self.assertEqual(result.result, "")
        self.assertTrue(result.metadata.get("deferred_to_executor"))

    def test_execute_never_raises(self):
        # No matter what the task text looks like -- including something
        # that might resemble a shell/file operation -- execute() must
        # never attempt to run it.
        result = self.agent.execute("rm -rf / and then commit", self.context)
        self.assertTrue(result.success)
        self.assertEqual(result.result, "")


if __name__ == "__main__":
    unittest.main()
