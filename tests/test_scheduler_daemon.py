"""Regression tests for agent/scheduler_daemon.py's poll tick -- both its
pre-existing due-task execution behavior and its new integration with
agent/scheduler_lock.py's cross-process lock (the duplicate-scheduler
fix -- see CHANGELOG.md).

Contention here is proven against the real lock file, held by a second
open() call from this same test process. tests/test_scheduler_lock.py
already proves the underlying primitive against a genuine second OS
process (including a hard SIGKILL); what these tests verify is specific
to this file: that _poll_once() reacts correctly to losing the lock --
skips execution, never marks a task run, logs a diagnostic -- and that
everything about the pre-existing single-scheduler behavior (including
its already-unusual "mark_run even on execution error" semantic, unlike
ui/menu_bar.py's poller) is unchanged.

Run with: python -m unittest tests.test_scheduler_daemon -v
"""
import fcntl
import os
import tempfile
import unittest
from datetime import datetime
from unittest.mock import patch

import agent.scheduled_tasks as scheduled_tasks
import agent.scheduler_daemon as scheduler_daemon
import agent.scheduler_lock as scheduler_lock
from agent.scheduled_tasks import add_task


class SchedulerDaemonTestCase(unittest.TestCase):

    def setUp(self):
        self._real_tasks_file = scheduled_tasks.TASKS_FILE
        self._real_lock_file = scheduler_lock.SCHEDULER_LOCK_FILE
        scheduled_tasks.TASKS_FILE = tempfile.mktemp(suffix=".json")
        scheduler_lock.SCHEDULER_LOCK_FILE = tempfile.mktemp(suffix=".lock")
        self._now = datetime(2026, 1, 1, 8, 0)

    def tearDown(self):
        for path in (
            scheduled_tasks.TASKS_FILE, f"{scheduled_tasks.TASKS_FILE}.lock",
            scheduler_lock.SCHEDULER_LOCK_FILE,
        ):
            if os.path.exists(path):
                os.remove(path)
        scheduled_tasks.TASKS_FILE = self._real_tasks_file
        scheduler_lock.SCHEDULER_LOCK_FILE = self._real_lock_file

    def _add_due_task(self, prompt="unittest: say hi"):
        task, error = add_task(prompt, self._now.strftime("%H:%M"))
        self.assertIsNone(error)
        return task

    def _hold_lock_from_elsewhere(self):
        os.makedirs(os.path.dirname(scheduler_lock.SCHEDULER_LOCK_FILE), exist_ok=True)
        held = open(scheduler_lock.SCHEDULER_LOCK_FILE, "a+")
        fcntl.flock(held.fileno(), fcntl.LOCK_EX)
        return held

    @patch("agent.scheduler_daemon._notify")
    @patch("agent.scheduler_daemon.execute_task")
    @patch("agent.scheduler_daemon.datetime")
    def test_due_task_executes_when_uncontested(self, mock_datetime, mock_execute, mock_notify):
        mock_datetime.now.return_value = self._now
        mock_execute.return_value = "done"
        task = self._add_due_task()

        scheduler_daemon._poll_once()

        mock_execute.assert_called_once_with(task["prompt"], source="scheduled")
        marked = next(t for t in scheduled_tasks.list_tasks() if t["id"] == task["id"])
        self.assertEqual(marked["last_run_date"], self._now.strftime("%Y-%m-%d"))

    @patch("agent.scheduler_daemon._notify")
    @patch("agent.scheduler_daemon.execute_task")
    @patch("agent.scheduler_daemon.datetime")
    def test_multiple_due_tasks_all_execute(self, mock_datetime, mock_execute, mock_notify):
        mock_datetime.now.return_value = self._now
        mock_execute.return_value = "done"
        first = self._add_due_task("unittest: task one")
        second = self._add_due_task("unittest: task two")

        scheduler_daemon._poll_once()

        self.assertEqual(mock_execute.call_count, 2)
        called_prompts = {call.args[0] for call in mock_execute.call_args_list}
        self.assertEqual(called_prompts, {first["prompt"], second["prompt"]})
        for task in scheduled_tasks.list_tasks():
            self.assertEqual(task["last_run_date"], self._now.strftime("%Y-%m-%d"))

    @patch("agent.scheduler_daemon.execute_task")
    @patch("agent.scheduler_daemon.datetime")
    def test_skips_execution_when_lock_is_held_elsewhere(self, mock_datetime, mock_execute):
        mock_datetime.now.return_value = self._now
        task = self._add_due_task()

        held = self._hold_lock_from_elsewhere()
        try:
            scheduler_daemon._poll_once()
        finally:
            held.close()

        mock_execute.assert_not_called()
        marked = next(t for t in scheduled_tasks.list_tasks() if t["id"] == task["id"])
        self.assertIsNone(marked["last_run_date"])

    @patch("agent.scheduler_daemon.log_event")
    @patch("agent.scheduler_daemon.execute_task")
    @patch("agent.scheduler_daemon.datetime")
    def test_deferral_is_logged(self, mock_datetime, mock_execute, mock_log_event):
        mock_datetime.now.return_value = self._now
        self._add_due_task()

        held = self._hold_lock_from_elsewhere()
        try:
            scheduler_daemon._poll_once()
        finally:
            held.close()

        mock_log_event.assert_called_once_with("scheduler_lock_deferred", component="scheduler_daemon")

    @patch("agent.scheduler_daemon._notify")
    @patch("agent.scheduler_daemon.execute_task")
    @patch("agent.scheduler_daemon.datetime")
    def test_lock_released_after_tick_so_next_tick_can_run(self, mock_datetime, mock_execute, mock_notify):
        mock_datetime.now.return_value = self._now
        mock_execute.return_value = "done"
        self._add_due_task()

        scheduler_daemon._poll_once()

        with scheduler_lock.try_acquire() as acquired:
            self.assertTrue(acquired)

    @patch("agent.scheduler_daemon._notify")
    @patch("agent.scheduler_daemon.execute_task")
    @patch("agent.scheduler_daemon.datetime")
    def test_preserves_existing_mark_run_on_execution_error(self, mock_datetime, mock_execute, mock_notify):
        # Pre-existing scheduler_daemon.py behavior, unrelated to the new
        # lock and must not change: a task that raises during execution
        # is still marked run for today, unlike ui/menu_bar.py's poller.
        mock_datetime.now.return_value = self._now
        mock_execute.side_effect = RuntimeError("boom")
        task = self._add_due_task()

        scheduler_daemon._poll_once()

        marked = next(t for t in scheduled_tasks.list_tasks() if t["id"] == task["id"])
        self.assertEqual(marked["last_run_date"], self._now.strftime("%Y-%m-%d"))

if __name__ == "__main__":
    unittest.main()
