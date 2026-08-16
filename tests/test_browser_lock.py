"""Regression tests for agent/browser_lock.py -- the cross-process mutex
fixing the Playwright/Chrome-profile contention risk described in
tools/browser.py's PROFILE_DIR comment: Streamlit, the menu-bar app, and
a scheduled task can each independently try to launch Chrome against the
same on-disk, dedicated CampusPilot profile directory.

Same two layers of proof as tests/test_scheduler_lock.py (that lock's
underlying primitive is the direct precedent for this one):
1. In-process tests of try_acquire()/release() using a second, separately
   opened file handle to simulate a genuine second owner -- a faithful
   proxy for real cross-process contention, since BSD flock() locks
   attach to the open file description (each open() call), not the
   process.
2. A real subprocess test that spawns an actual second OS process to
   hold the lock and kills it with SIGKILL rather than letting it exit
   cleanly -- proving the lock is genuinely kernel-managed, not reliant
   on any in-process Python cleanup a real crash wouldn't run.

Run with: python -m unittest tests.test_browser_lock -v
"""
import fcntl
import os
import subprocess
import sys
import tempfile
import unittest

import agent.browser_lock as browser_lock


class BrowserLockTestCase(unittest.TestCase):

    def setUp(self):
        self._real_lock_file = browser_lock.BROWSER_LOCK_FILE
        browser_lock.BROWSER_LOCK_FILE = tempfile.mktemp(suffix=".lock")

    def tearDown(self):
        browser_lock.release()
        if os.path.exists(browser_lock.BROWSER_LOCK_FILE):
            os.remove(browser_lock.BROWSER_LOCK_FILE)
        browser_lock.BROWSER_LOCK_FILE = self._real_lock_file

    def test_acquisition_succeeds_when_uncontested(self):
        self.assertTrue(browser_lock.try_acquire())
        self.assertTrue(browser_lock.is_held())

    def test_creates_lock_file_under_application_support(self):
        browser_lock.try_acquire()
        self.assertTrue(os.path.exists(browser_lock.BROWSER_LOCK_FILE))

    def test_reacquisition_by_same_process_is_idempotent(self):
        self.assertTrue(browser_lock.try_acquire())
        # A second call while already held just confirms ownership --
        # doesn't open a second file handle or fail.
        self.assertTrue(browser_lock.try_acquire())
        self.assertTrue(browser_lock.is_held())

    def test_second_owner_cannot_acquire_while_first_holds(self):
        self.assertTrue(browser_lock.try_acquire())

        # A genuinely separate open() (own open file description), the
        # same technique tests/test_scheduler_lock.py uses to prove real
        # flock exclusivity rather than just this module's own bookkeeping.
        os.makedirs(os.path.dirname(browser_lock.BROWSER_LOCK_FILE), exist_ok=True)
        other_fd = open(browser_lock.BROWSER_LOCK_FILE, "a+")
        try:
            with self.assertRaises(OSError):
                fcntl.flock(other_fd.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        finally:
            other_fd.close()

    def test_lock_becomes_available_after_release(self):
        self.assertTrue(browser_lock.try_acquire())
        browser_lock.release()
        self.assertFalse(browser_lock.is_held())

        os.makedirs(os.path.dirname(browser_lock.BROWSER_LOCK_FILE), exist_ok=True)
        other_fd = open(browser_lock.BROWSER_LOCK_FILE, "a+")
        try:
            fcntl.flock(other_fd.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)  # must not raise
        finally:
            fcntl.flock(other_fd.fileno(), fcntl.LOCK_UN)
            other_fd.close()

    def test_release_when_never_acquired_is_a_safe_no_op(self):
        browser_lock.release()  # must not raise
        self.assertFalse(browser_lock.is_held())

    def test_is_held_reflects_current_state(self):
        self.assertFalse(browser_lock.is_held())
        browser_lock.try_acquire()
        self.assertTrue(browser_lock.is_held())
        browser_lock.release()
        self.assertFalse(browser_lock.is_held())


class TestCrossProcessContention(unittest.TestCase):
    """Real cross-process proof: a genuinely separate OS process holds the
    lock while this test process tries to acquire it -- exactly the
    scenario Streamlit/menu-bar/scheduler are in when more than one of
    them actually tries to drive a browser at once."""

    _CHILD_SCRIPT = (
        "import fcntl, sys, time\n"
        "f = open(sys.argv[1], 'a+')\n"
        "fcntl.flock(f.fileno(), fcntl.LOCK_EX)\n"
        "print('LOCKED', flush=True)\n"
        "time.sleep(60)\n"
    )

    def setUp(self):
        self._real_lock_file = browser_lock.BROWSER_LOCK_FILE
        browser_lock.BROWSER_LOCK_FILE = tempfile.mktemp(suffix=".lock")
        self._child = None

    def tearDown(self):
        browser_lock.release()
        if self._child is not None:
            if self._child.poll() is None:
                self._child.kill()
                self._child.wait(timeout=5)
            if self._child.stdout:
                self._child.stdout.close()
        if os.path.exists(browser_lock.BROWSER_LOCK_FILE):
            os.remove(browser_lock.BROWSER_LOCK_FILE)
        browser_lock.BROWSER_LOCK_FILE = self._real_lock_file

    def _spawn_lock_holder(self):
        child = subprocess.Popen(
            [sys.executable, "-u", "-c", self._CHILD_SCRIPT, browser_lock.BROWSER_LOCK_FILE],
            stdout=subprocess.PIPE,
            text=True,
        )
        self._child = child
        ready = child.stdout.readline()
        self.assertEqual(ready.strip(), "LOCKED", "child process never reported holding the lock")
        return child

    def test_real_second_process_holding_the_lock_blocks_this_one(self):
        self._spawn_lock_holder()
        self.assertFalse(browser_lock.try_acquire())

    def test_lock_becomes_available_after_owning_process_is_killed(self):
        child = self._spawn_lock_holder()
        self.assertFalse(browser_lock.try_acquire())

        # SIGKILL, not a clean exit -- proves the OS releases the flock
        # even when the holder never runs a single line of its own
        # cleanup code (the exact scenario ui/menu_bar.py's own
        # termination-signal comment documents atexit can't be trusted
        # for in this codebase).
        child.kill()
        child.wait(timeout=5)

        self.assertTrue(browser_lock.try_acquire())


if __name__ == "__main__":
    unittest.main()
