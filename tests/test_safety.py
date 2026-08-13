"""Regression tests for the safety-critical paths built up over this
project's development: permission coverage, sandbox isolation, the login
confirmation gate, audit logging, and the scheduler's unattended-run
restrictions. These exercise the real mechanisms (real sandbox-exec, real
Keychain entries cleaned up afterward, real tool dispatch) rather than
mocks, on the theory that a security boundary you haven't actually tested
isn't verified.

Run with: python -m unittest tests.test_safety -v
"""

import os
import unittest
from datetime import datetime

import tools.autofill as autofill
from agent.audit import recent_actions
from agent.brain import TOOLS
from agent.executor import _run_tool
from agent.permissions import check_full_coverage
from agent.scheduled_tasks import add_task, list_tasks, mark_run, remove_task
from tools.credential_store import delete_login, save_login
from tools.sandbox_python import run_python


class TestPermissionCoverage(unittest.TestCase):

    def test_every_tool_is_classified(self):
        # check_full_coverage raises if anything is missing -- that raise
        # not happening IS the assertion. This is the same guard that
        # already caught a real gap (a missing tool) during development.
        check_full_coverage([tool["name"] for tool in TOOLS])


class TestSandboxIsolation(unittest.TestCase):

    def test_blocks_writes_outside_sandbox(self):
        target = os.path.expanduser("~/Desktop/campuspilot_test_should_not_exist.txt")
        if os.path.exists(target):
            os.remove(target)

        run_python(f"open('{target}', 'w').write('should not exist')")

        self.assertFalse(os.path.exists(target))
        if os.path.exists(target):
            os.remove(target)

    def test_blocks_network(self):
        result = run_python(
            "import urllib.request\n"
            "try:\n"
            "    urllib.request.urlopen('https://example.com', timeout=3)\n"
            "    print('NETWORK_SUCCEEDED')\n"
            "except Exception as e:\n"
            "    print('blocked:', type(e).__name__)\n"
        )
        self.assertNotIn("NETWORK_SUCCEEDED", result)

    def test_real_computation_still_works(self):
        result = run_python("print(2 + 2)")
        self.assertIn("4", result)
        self.assertIn("succeeded", result)


class TestConfirmLoginGate(unittest.TestCase):

    TEST_SITE = "unittest_gate_check"

    def setUp(self):
        autofill._pending_confirmation = None
        save_login(self.TEST_SITE, "example.com", "tester", "not-a-real-secret")

    def tearDown(self):
        delete_login(self.TEST_SITE)
        autofill._pending_confirmation = None

    def test_confirm_without_preview_is_blocked(self):
        result = autofill.confirm_login(self.TEST_SITE)
        self.assertIn("call fill_login first", result)

    def test_scheduled_source_blocks_confirm_login(self):
        result = _run_tool("confirm_login", {"site": self.TEST_SITE}, source="scheduled")
        self.assertIn("Skipped", result)
        self.assertIn("scheduled", result)


class TestAuditLog(unittest.TestCase):

    def test_real_tool_call_is_logged(self):
        before = len(recent_actions(1000))
        _run_tool("get_system_status", {})
        after = recent_actions(1000)

        self.assertEqual(len(after), before + 1)
        self.assertEqual(after[-1]["tool"], "get_system_status")

    def test_tool_error_is_logged_with_error_prefix(self):
        with self.assertRaises(KeyError):
            _run_tool("read_document", {})

        entries = recent_actions(1)
        self.assertTrue(entries[-1]["result"].startswith("ERROR:"))


class TestScheduler(unittest.TestCase):

    def tearDown(self):
        for task in list_tasks():
            if task["prompt"].startswith("unittest:"):
                remove_task(task["id"])

    def test_invalid_time_format_rejected(self):
        task, error = add_task("unittest: bad time", "25:99")
        self.assertIsNone(task)
        self.assertIsNotNone(error)

    def test_marked_task_shows_as_already_run_today(self):
        task, error = add_task("unittest: same-day skip check", "00:00")
        self.assertIsNone(error)

        today = datetime.now().strftime("%Y-%m-%d")
        mark_run(task["id"], today)

        marked = next(t for t in list_tasks() if t["id"] == task["id"])
        self.assertEqual(marked["last_run_date"], today)


if __name__ == "__main__":
    unittest.main()
