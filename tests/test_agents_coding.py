"""Tests for agent/agents/coding.py's DEFAULT (disabled) behavior --
config.settings.coding_agent_enabled is False unless explicitly turned
on, and while it is, CodingAgent always defers to the ordinary executor
exactly as it did before Phase 10. These tests pin exactly that:
execute() must never touch a tool, a shell, or a model client in the
default configuration. See tests/test_agents_coding_enabled.py for the
real, opted-in execution path (Phase 10 increment 1).

Run with: python -m unittest tests.test_agents_coding -v
"""
import unittest

from agent.agents.coding import CodingAgent
from agent.request_context import RequestContext
from config.settings import settings


class TestCodingAgent(unittest.TestCase):

    def setUp(self):
        # Explicitly forced, not just relying on the class default --
        # found for real by dogfooding CodingAgent's real execute(): its
        # own final test-suite subprocess (agent.agents.coding.
        # _run_test_suite) inherits the CALLING process's environment via
        # plain subprocess.run(), so if CODING_AGENT_ENABLED=true is set
        # as a real environment variable (exactly how someone would
        # actually turn this feature on in production, not just in a
        # test), that subprocess's own fresh Settings.load() picks it up
        # too -- and these tests, which assert the DISABLED stub
        # behavior, broke for real under that real scenario. Same lesson
        # Phase 9 Reliability S1 already established project-wide: never
        # trust ambient/inherited state, isolate explicitly.
        self._real_enabled = settings.coding_agent_enabled
        object.__setattr__(settings, "coding_agent_enabled", False)
        self.agent = CodingAgent()
        self.context = RequestContext.create("Fix this Python error.", source="test")

    def tearDown(self):
        object.__setattr__(settings, "coding_agent_enabled", self._real_enabled)

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
