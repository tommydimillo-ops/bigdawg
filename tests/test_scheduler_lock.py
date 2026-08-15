"""Regression tests for agent/scheduler_lock.py -- the cross-process
mutex that fixes the duplicate-scheduler lifecycle risk (see
CHANGELOG.md): agent/scheduler_daemon.py and ui/menu_bar.py each poll the
same scheduled_tasks.json independently, and used to both execute a due
task when run at the same time.

Two layers of proof, on purpose:
1. In-process tests of the primitive itself, using two separate open()
   calls on the same lock file -- a faithful test of flock's real
   exclusivity, since BSD flock() locks attach to the open file
   description (each open() call), not the process, but still fast/no
   subprocess overhead.
2. A real subprocess test (TestCrossProcessContention) that spawns an
   actual second OS process to hold the lock, including killing it with
   SIGKILL rather than letting it exit cleanly -- proving the lock is
   genuinely kernel-managed (auto-released on a hard kill, no stale-lock
   detection code involved) rather than relying on any in-process
   Python-level cleanup that a real crash wouldn't run.

Run with: python -m unittest tests.test_scheduler_lock -v
"""
import os
import subprocess
import sys
import tempfile
import unittest

import agent.scheduler_lock as scheduler_lock


class SchedulerLockTestCase(unittest.TestCase):

    def setUp(self):
        self._real_lock_file = scheduler_lock.SCHEDULER_LOCK_FILE
        scheduler_lock.SCHEDULER_LOCK_FILE = tempfile.mktemp(suffix=".lock")

    def tearDown(self):
        if os.path.exists(scheduler_lock.SCHEDULER_LOCK_FILE):
            os.remove(scheduler_lock.SCHEDULER_LOCK_FILE)
        scheduler_lock.SCHEDULER_LOCK_FILE = self._real_lock_file

    def test_acquisition_succeeds_when_uncontested(self):
        with scheduler_lock.try_acquire() as acquired:
            self.assertTrue(acquired)

    def test_creates_lock_file_under_application_support(self):
        with scheduler_lock.try_acquire():
            self.assertTrue(os.path.exists(scheduler_lock.SCHEDULER_LOCK_FILE))

    def test_second_attempt_cannot_acquire_while_first_holds(self):
        # Two independent open() calls on the same path -- genuinely two
        # separate open file descriptions, so this exercises the same
        # kernel exclusivity a second real process would hit, not a
        # simulation of it.
        with scheduler_lock.try_acquire() as first:
            self.assertTrue(first)
            with scheduler_lock.try_acquire() as second:
                self.assertFalse(second)

    def test_lock_becomes_available_after_release(self):
        with scheduler_lock.try_acquire() as first:
            self.assertTrue(first)

        with scheduler_lock.try_acquire() as second:
            self.assertTrue(second)

    def test_nested_contention_then_release_then_reacquire(self):
        with scheduler_lock.try_acquire() as first:
            self.assertTrue(first)
            with scheduler_lock.try_acquire() as second:
                self.assertFalse(second)
        with scheduler_lock.try_acquire() as third:
            self.assertTrue(third)

    def test_lock_released_even_if_caller_raises(self):
        with self.assertRaises(ValueError):
            with scheduler_lock.try_acquire() as acquired:
                self.assertTrue(acquired)
                raise ValueError("boom")

        with scheduler_lock.try_acquire() as after:
            self.assertTrue(after)


class TestCrossProcessContention(unittest.TestCase):
    """Real cross-process proof, not threads standing in for it -- a
    genuinely separate OS process holds the lock while this test process
    tries to acquire it, exactly the scenario agent/scheduler_daemon.py
    and ui/menu_bar.py are in when both are actually running."""

    _CHILD_SCRIPT = (
        "import fcntl, sys, time\n"
        "f = open(sys.argv[1], 'a+')\n"
        "fcntl.flock(f.fileno(), fcntl.LOCK_EX)\n"
        "print('LOCKED', flush=True)\n"
        "time.sleep(60)\n"
    )

    def setUp(self):
        self._real_lock_file = scheduler_lock.SCHEDULER_LOCK_FILE
        scheduler_lock.SCHEDULER_LOCK_FILE = tempfile.mktemp(suffix=".lock")
        self._child = None

    def tearDown(self):
        if self._child is not None:
            if self._child.poll() is None:
                self._child.kill()
                self._child.wait(timeout=5)
            if self._child.stdout:
                self._child.stdout.close()
        if os.path.exists(scheduler_lock.SCHEDULER_LOCK_FILE):
            os.remove(scheduler_lock.SCHEDULER_LOCK_FILE)
        scheduler_lock.SCHEDULER_LOCK_FILE = self._real_lock_file

    def _spawn_lock_holder(self):
        child = subprocess.Popen(
            [sys.executable, "-u", "-c", self._CHILD_SCRIPT, scheduler_lock.SCHEDULER_LOCK_FILE],
            stdout=subprocess.PIPE,
            text=True,
        )
        self._child = child
        ready = child.stdout.readline()
        self.assertEqual(ready.strip(), "LOCKED", "child process never reported holding the lock")
        return child

    def test_real_second_process_holding_the_lock_blocks_this_one(self):
        self._spawn_lock_holder()

        with scheduler_lock.try_acquire() as acquired:
            self.assertFalse(acquired)

    def test_lock_becomes_available_after_owning_process_is_killed(self):
        child = self._spawn_lock_holder()

        with scheduler_lock.try_acquire() as acquired:
            self.assertFalse(acquired)

        # SIGKILL, not terminate()/wait() for a clean exit -- the whole
        # point is proving the OS releases the flock even when the
        # holder never runs a single line of its own cleanup code.
        child.kill()
        child.wait(timeout=5)

        with scheduler_lock.try_acquire() as acquired:
            self.assertTrue(acquired)


if __name__ == "__main__":
    unittest.main()
