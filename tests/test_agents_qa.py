"""Tests for agent/agents/qa.py -- subprocess.run is mocked throughout,
so no real test-suite invocation happens as a side effect of testing
QAAgent itself.

Run with: python -m unittest tests.test_agents_qa -v
"""
import subprocess
import unittest
from unittest.mock import MagicMock, patch

from agent.agents.qa import QAAgent
from agent.request_context import RequestContext


class TestQAAgent(unittest.TestCase):

    def setUp(self):
        self.agent = QAAgent()
        self.context = RequestContext.create("test", source="test")

    def test_metadata(self):
        self.assertEqual(self.agent.metadata.name, "qa")

    @patch("agent.agents.qa.subprocess.run")
    def test_test_suite_request_runs_it_and_reports_success(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0, stdout="Ran 500 tests in 4s\n\nOK\n", stderr="")
        result = self.agent.execute("do the tests still pass?", self.context)

        mock_run.assert_called_once()
        self.assertTrue(result.success)
        self.assertEqual(result.verification_status, "passed")
        self.assertIn("All tests passed", result.result)

    @patch("agent.agents.qa.subprocess.run")
    def test_test_suite_request_reports_failure(self, mock_run):
        mock_run.return_value = MagicMock(
            returncode=1, stdout="Ran 500 tests in 4s\n\nFAILED (failures=2)\n", stderr="",
        )
        result = self.agent.execute("run the tests", self.context)

        self.assertFalse(result.success)
        self.assertEqual(result.verification_status, "failed")

    @patch("agent.agents.qa.subprocess.run")
    def test_test_suite_timeout_reports_failure_not_raise(self, mock_run):
        mock_run.side_effect = subprocess.TimeoutExpired(cmd=["unittest"], timeout=45)
        result = self.agent.execute("run the test suite", self.context)
        self.assertFalse(result.success)

    def test_non_test_request_defers_to_executor(self):
        result = self.agent.execute("verify this email was actually sent", self.context)
        self.assertTrue(result.success)
        self.assertEqual(result.result, "")
        self.assertTrue(result.metadata.get("deferred_to_executor"))

    @patch("agent.agents.qa.subprocess.run")
    def test_includes_the_load_bearing_t_flag(self, mock_run):
        # Real, pre-existing bug found by code review, not a live
        # incident: this call was missing -t ., which means
        # tests/__init__.py's safety bootstrap (redirecting every
        # production store path, blocking external network) never ran --
        # and this path is already fully live in production (no setting
        # needs to be turned on), so every real "do the tests still
        # pass?" request before this fix ran against real production
        # paths and the real Keychain. See CLAUDE.md's "How to test"
        # section and tests/__init__.py's own docstring for why this
        # flag is load-bearing, not cosmetic.
        mock_run.return_value = MagicMock(returncode=0, stdout="OK\n", stderr="")
        self.agent.execute("run the tests", self.context)
        args = mock_run.call_args.args[0]
        self.assertIn("-t", args)
        self.assertEqual(args[args.index("-t") + 1], ".")

    @patch("agent.agents.qa.subprocess.run")
    def test_never_calls_a_destructive_command(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0, stdout="OK\n", stderr="")
        self.agent.execute("run the tests", self.context)
        args = mock_run.call_args.args[0]
        # Read-only test discovery only -- never anything resembling a
        # write/delete/deploy/commit/push invocation.
        joined = " ".join(args).lower()
        for forbidden in ("rm ", "git push", "git commit", "delete", "deploy"):
            self.assertNotIn(forbidden, joined)


if __name__ == "__main__":
    unittest.main()
