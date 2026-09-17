"""Tests for agent/alexa_bridge.py -- auth, the last-result store, and the
task runner behind the Alexa entry point. The external boundaries this
module reaches out to lazily (agent.executor.execute_task,
agent.telegram_bridge) are mocked; everything else here is real.

Run with: python -m unittest tests.test_alexa_bridge -v
"""
import json
import os
import threading
import time
import unittest
from unittest.mock import patch

import agent.alexa_bridge as ab


def _wait_for_alexa_task(timeout=10.0):
    """Deterministically wait for start_task()'s background worker to
    finish -- joins the real thread by name (a real CI runner can be
    materially slower than a quiet local machine, a class of flake this
    project has hit before; see .github/workflows/tests.yml's own
    timeout-raise comment), falling back to a bounded is_busy() poll only
    for the race where the thread already finished and was reaped before
    this could enumerate it."""
    deadline = time.time() + timeout
    for thread in threading.enumerate():
        if thread.name == "alexa-task":
            thread.join(timeout=max(0.0, deadline - time.time()))
            break
    while ab.is_busy() and time.time() < deadline:
        time.sleep(0.02)


class TestTokenConfig(unittest.TestCase):

    def test_not_configured_without_a_token(self):
        with patch("agent.alexa_bridge.get_secret", return_value=None):
            self.assertFalse(ab.is_configured())

    def test_configured_with_a_token(self):
        with patch("agent.alexa_bridge.get_secret", return_value="s3cr3t"):
            self.assertTrue(ab.is_configured())

    def test_verify_token_accepts_the_exact_configured_token(self):
        with patch("agent.alexa_bridge.get_secret", return_value="s3cr3t"):
            self.assertTrue(ab.verify_token("s3cr3t"))

    def test_verify_token_accepts_a_bearer_prefix_case_insensitively(self):
        with patch("agent.alexa_bridge.get_secret", return_value="s3cr3t"):
            self.assertTrue(ab.verify_token("Bearer s3cr3t"))
            self.assertTrue(ab.verify_token("bearer s3cr3t"))
            self.assertTrue(ab.verify_token("BEARER s3cr3t"))

    def test_verify_token_rejects_the_wrong_token(self):
        with patch("agent.alexa_bridge.get_secret", return_value="s3cr3t"):
            self.assertFalse(ab.verify_token("wrong"))
            self.assertFalse(ab.verify_token("s3cr3"))
            self.assertFalse(ab.verify_token("s3cr3tt"))

    def test_verify_token_rejects_when_unconfigured(self):
        # An absent configured token can never be "satisfied" by an
        # absent or empty presented value.
        with patch("agent.alexa_bridge.get_secret", return_value=None):
            self.assertFalse(ab.verify_token(None))
            self.assertFalse(ab.verify_token(""))
            self.assertFalse(ab.verify_token("anything"))

    def test_verify_token_rejects_none_or_empty_presented(self):
        with patch("agent.alexa_bridge.get_secret", return_value="s3cr3t"):
            self.assertFalse(ab.verify_token(None))
            self.assertFalse(ab.verify_token(""))
            self.assertFalse(ab.verify_token("   "))


class TestBusy(unittest.TestCase):

    def tearDown(self):
        if ab._run_lock.locked():
            ab._run_lock.release()

    def test_is_busy_reflects_the_run_lock(self):
        self.assertFalse(ab.is_busy())
        ab._run_lock.acquire()
        self.assertTrue(ab.is_busy())
        ab._run_lock.release()
        self.assertFalse(ab.is_busy())


class TestLastResultPersistence(unittest.TestCase):

    def tearDown(self):
        for path in (ab.LAST_RESULT_FILE, f"{ab.LAST_RESULT_FILE}.tmp"):
            if os.path.exists(path):
                os.remove(path)

    def test_round_trips_through_the_redirected_file(self):
        record = {"task": "check my calendar", "result": "nothing today", "ok": True, "finished_at": 123.0}
        ab.save_last_result(record)
        self.assertEqual(ab.load_last_result(), record)

    def test_missing_file_reads_as_a_neutral_placeholder(self):
        if os.path.exists(ab.LAST_RESULT_FILE):
            os.remove(ab.LAST_RESULT_FILE)
        self.assertEqual(
            ab.load_last_result(),
            {"task": None, "result": None, "ok": None, "finished_at": None},
        )

    def test_corrupt_file_reads_as_the_placeholder_not_a_crash(self):
        os.makedirs(os.path.dirname(ab.LAST_RESULT_FILE), exist_ok=True)
        with open(ab.LAST_RESULT_FILE, "w") as handle:
            handle.write("{not valid json")
        self.assertEqual(ab.load_last_result()["task"], None)

    def test_non_dict_json_reads_as_the_placeholder(self):
        os.makedirs(os.path.dirname(ab.LAST_RESULT_FILE), exist_ok=True)
        with open(ab.LAST_RESULT_FILE, "w") as handle:
            json.dump([1, 2, 3], handle)
        self.assertEqual(ab.load_last_result()["task"], None)


class TestSpokenSummary(unittest.TestCase):

    def test_nothing_finished_yet(self):
        self.assertIn("haven't finished", ab.spoken_summary({"finished_at": None}))
        self.assertIn("haven't finished", ab.spoken_summary({}))

    def test_finished_with_no_text(self):
        record = {"finished_at": 1.0, "result": "   "}
        self.assertIn("didn't say anything", ab.spoken_summary(record))

    def test_short_result_returned_as_is(self):
        record = {"finished_at": 1.0, "result": "Done — turned off the lights."}
        self.assertEqual(ab.spoken_summary(record), "Done — turned off the lights.")

    def test_long_result_is_trimmed_with_a_telegram_pointer(self):
        record = {"finished_at": 1.0, "result": "word " * 300}
        summary = ab.spoken_summary(record)
        self.assertLessEqual(len(summary), ab.SPOKEN_RESULT_LIMIT + len("... the rest is in Telegram."))
        self.assertTrue(summary.endswith("... the rest is in Telegram."))


class TestRunTask(unittest.TestCase):

    def tearDown(self):
        for path in (ab.LAST_RESULT_FILE, f"{ab.LAST_RESULT_FILE}.tmp"):
            if os.path.exists(path):
                os.remove(path)

    def test_runs_with_empty_history_and_alexa_source(self):
        with patch("agent.executor.execute_task", return_value="all set") as exec_mock, \
             patch("agent.telegram_bridge.is_configured", return_value=False):
            ab.run_task("turn off the lights")
        exec_mock.assert_called_once_with("turn off the lights", [], source="alexa")

    def test_success_is_persisted_and_delivered_over_telegram(self):
        with patch("agent.executor.execute_task", return_value="all set"), \
             patch("agent.telegram_bridge.is_configured", return_value=True), \
             patch("agent.telegram_bridge.send_message") as send_mock:
            record = ab.run_task("turn off the lights")

        self.assertTrue(record["ok"])
        self.assertEqual(record["result"], "all set")
        self.assertEqual(ab.load_last_result()["result"], "all set")
        send_mock.assert_called_once()
        self.assertIn("all set", send_mock.call_args.args[0])

    def test_no_delivery_attempt_when_telegram_is_not_configured(self):
        with patch("agent.executor.execute_task", return_value="all set"), \
             patch("agent.telegram_bridge.is_configured", return_value=False), \
             patch("agent.telegram_bridge.send_message") as send_mock:
            ab.run_task("turn off the lights")
        send_mock.assert_not_called()

    def test_agent_exception_is_contained_and_reported_as_a_failed_result(self):
        with patch("agent.executor.execute_task", side_effect=RuntimeError("boom")), \
             patch("agent.telegram_bridge.is_configured", return_value=True), \
             patch("agent.telegram_bridge.send_message") as send_mock:
            record = ab.run_task("do something that breaks")

        self.assertFalse(record["ok"])
        self.assertIn("RuntimeError", record["result"])
        # Delivery still happens for a failed run -- the point is to know
        # it failed, not just successes.
        send_mock.assert_called_once()

    def test_telegram_delivery_failure_does_not_propagate(self):
        with patch("agent.executor.execute_task", return_value="all set"), \
             patch("agent.telegram_bridge.is_configured", return_value=True), \
             patch("agent.telegram_bridge.send_message", side_effect=RuntimeError("telegram down")):
            record = ab.run_task("turn off the lights")  # must not raise
        self.assertTrue(record["ok"])
        self.assertEqual(ab.load_last_result()["result"], "all set")


class TestStartTask(unittest.TestCase):

    def tearDown(self):
        # Join first (bounded): forcibly releasing a lock a still-running
        # worker thread believes it owns would let the NEXT test's
        # start_task() succeed while that stray thread is still using
        # THIS test's now-closed mock patches.
        _wait_for_alexa_task(timeout=10.0)
        if ab._run_lock.locked():
            ab._run_lock.release()
        for path in (ab.LAST_RESULT_FILE, f"{ab.LAST_RESULT_FILE}.tmp"):
            if os.path.exists(path):
                os.remove(path)

    def test_starts_a_background_task_and_releases_the_lock_when_done(self):
        with patch("agent.executor.execute_task", return_value="done"), \
             patch("agent.telegram_bridge.is_configured", return_value=False):
            accepted = ab.start_task("do the thing")
            self.assertTrue(accepted)
            _wait_for_alexa_task(timeout=10.0)
            self.assertFalse(ab.is_busy(), "background task did not finish within the wait budget")
        self.assertEqual(ab.load_last_result()["result"], "done")

    def test_refuses_a_second_task_while_one_is_running(self):
        ab._run_lock.acquire()
        try:
            self.assertFalse(ab.start_task("a second task"))
        finally:
            ab._run_lock.release()


if __name__ == "__main__":
    unittest.main()
