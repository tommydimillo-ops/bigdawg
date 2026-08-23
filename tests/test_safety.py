"""Regression tests for the safety-critical paths built up over this
project's development: permission coverage, sandbox isolation, the login
confirmation gate, audit logging, and the scheduler's unattended-run
restrictions. Sandbox isolation exercises the real mechanism (real
sandbox-exec) on the theory that a security boundary you haven't
actually tested isn't verified -- that one is a genuine OS-level kernel
policy no Python-level mock could stand in for anyway.

TestConfirmLoginGate is the one exception to "exercise the real
mechanism": it mocks the `keyring` boundary (see its own docstring for
why) rather than touching any Keychain, real or test-service-named --
confirmed live during the Phase 9 reliability-audit implementation that
even a distinctly-named test service can raise a real macOS Keychain API
error in a non-interactive session (no GUI to answer the access-control
prompt), which is exactly the class of CI/automation hang the audit
flagged. The gate LOGIC under test (confirm-requires-preview,
scheduled-source blocks confirm) has nothing to do with Keychain
internals, so mocking that one boundary doesn't weaken what this test
actually verifies.

Run with: python -m unittest tests.test_safety -v
"""

import os
import tempfile
import unittest
from datetime import datetime
from unittest.mock import MagicMock, patch

import tools.autofill as autofill
import agent.jarvis_state as jarvis_state
import agent.scheduled_tasks as scheduled_tasks
from agent.audit import recent_actions
from agent.brain import TOOLS
from agent.executor import _run_tool
from agent.permissions import check_full_coverage
from agent.scheduled_tasks import add_task, list_tasks, mark_run, remove_task
from tools.credential_store import delete_login, save_login
from tools.sandbox_python import run_python

# _run_tool now writes cross-interface status via agent.jarvis_state on
# every real dispatch -- redirected here (module-wide, like every other
# file-backed store's tests in this project) so exercising it doesn't
# clobber the real ~/Library/.../jarvis_state.json the live menu-bar/
# dashboard apps read from. TestScheduler below writes real tasks (albeit
# briefly, cleaned up again in its own tearDown) -- redirected the same
# way so even that transient write never touches the real
# scheduled_tasks.json.
_real_state_file = jarvis_state.STATE_FILE
_real_tasks_file = scheduled_tasks.TASKS_FILE


def setUpModule():
    jarvis_state.STATE_FILE = tempfile.mktemp(suffix=".json")
    scheduled_tasks.TASKS_FILE = tempfile.mktemp(suffix=".json")


def tearDownModule():
    jarvis_state.STATE_FILE = _real_state_file
    scheduled_tasks.TASKS_FILE = _real_tasks_file


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
    """Mocks tools.credential_store.keyring (the module-level `import
    keyring` reference every save_login/get_login/delete_login call reads
    dynamically) so this test never reaches any Keychain backend -- real
    service or the test-only one tests/_safety.py redirects
    KEYCHAIN_SERVICE to. LOGINS_FILE itself is exercised for real (it's
    already redirected to the shared test run-root by tests/_safety.py,
    or to a per-test temp file if this module runs in isolation without
    that bootstrap), proving save_login/delete_login's actual metadata
    read-modify-write logic without needing a live Keychain underneath
    it."""

    TEST_SITE = "unittest_gate_check"

    def setUp(self):
        self._keyring_patch = patch("tools.credential_store.keyring", MagicMock())
        self._keyring_patch.start()
        autofill._pending_confirmation = None
        save_login(self.TEST_SITE, "example.com", "tester", "not-a-real-secret")

    def tearDown(self):
        delete_login(self.TEST_SITE)
        autofill._pending_confirmation = None
        self._keyring_patch.stop()

    def test_confirm_without_preview_is_blocked(self):
        result = autofill.confirm_login(self.TEST_SITE)
        self.assertIn("call fill_login first", result)

    def test_scheduled_source_blocks_confirm_login(self):
        result = _run_tool("confirm_login", {"site": self.TEST_SITE}, source="scheduled")
        self.assertIn("Skipped", result)
        self.assertIn("scheduled", result)


class TestAuditLog(unittest.TestCase):

    def test_real_tool_call_is_logged(self):
        # Checks the last entry itself, not a before/after length delta --
        # the real audit log this test deliberately exercises (see the
        # module docstring) has no cap, so once it's grown past whatever
        # window recent_actions() is asked for, adding one more entry
        # doesn't change that window's *length*, only its content.
        _run_tool("get_system_status", {})
        after = recent_actions(1000)

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
