"""Tests for agent/execution_history.py -- the bounded, persistent record
of past requests (distinct from agent/memory/'s indefinite personal facts
and agent/audit.py's unbounded raw action log).

HISTORY_FILE is redirected to a temp file for every test here (module-
wide, matching this project's established pattern for every other file-
backed store's tests -- see tests/test_memory.py), so none of this ever
touches the real ~/Library/.../execution_history.json the dashboard and
view_task_history tool read from.

Run with: python -m unittest tests.test_execution_history -v
"""
import os
import tempfile
import unittest

import agent.execution_history as execution_history
from agent.execution_state import ExecutionState, register_active, unregister_active
from config.settings import settings


class IsolatedHistoryTestCase(unittest.TestCase):

    def setUp(self):
        self._real_history_file = execution_history.HISTORY_FILE
        execution_history.HISTORY_FILE = tempfile.mktemp(suffix=".json")

    def tearDown(self):
        for path in (execution_history.HISTORY_FILE, f"{execution_history.HISTORY_FILE}.tmp"):
            if os.path.exists(path):
                os.remove(path)
        execution_history.HISTORY_FILE = self._real_history_file


class TestExecutionRecordRoundTrip(unittest.TestCase):

    def test_to_dict_from_dict_round_trip(self):
        record = execution_history.ExecutionRecord(
            request_id="r1", request_summary="check the weather", model="claude-sonnet-5",
            duration_seconds=1.5, status="completed", tools_used=["get_weather"], tool_count=1,
        )
        restored = execution_history.ExecutionRecord.from_dict(record.to_dict())
        self.assertEqual(restored, record)

    def test_from_dict_ignores_unknown_fields(self):
        restored = execution_history.ExecutionRecord.from_dict({
            "request_id": "r1", "a_future_field": "whatever",
        })
        self.assertEqual(restored.request_id, "r1")


class TestSanitizeSummary(unittest.TestCase):

    def test_redacts_secret_shaped_content(self):
        sanitized = execution_history._sanitize_summary("my api_key: sk-abcdefghijklmnopqrstuvwxyz")
        self.assertNotIn("sk-abcdefghijklmnopqrstuvwxyz", sanitized)
        self.assertIn("[redacted]", sanitized)

    def test_truncates_long_summaries(self):
        sanitized = execution_history._sanitize_summary("x" * 500)
        self.assertLessEqual(len(sanitized), execution_history.MAX_SUMMARY_LENGTH + 1)

    def test_empty_summary_stays_empty(self):
        self.assertEqual(execution_history._sanitize_summary(""), "")


class TestPersistAndRetention(IsolatedHistoryTestCase):

    def _state(self, **overrides):
        state = ExecutionState(max_iterations=8)
        state.finish(result="ok")
        for key, value in overrides.items():
            setattr(state, key, value)
        return state

    def test_record_completed_persists_a_completed_entry(self):
        execution_history.record_completed("r1", "check weather", self._state(), autonomy_level=4)
        records = execution_history.get_recent()
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0].status, "completed")
        self.assertEqual(records[0].request_id, "r1")
        self.assertEqual(records[0].autonomy_level, 4)

    def test_record_failed_persists_a_failed_entry_with_sanitized_error(self):
        state = self._state()
        state.error = "auth failed, api_key: sk-abcdefghijklmnopqrstuvwxyz"
        execution_history.record_failed("r2", "do a thing", state, autonomy_level=2)
        record = execution_history.get_by_id("r2")
        self.assertEqual(record.status, "failed")
        self.assertEqual(len(record.errors), 1)
        self.assertNotIn("sk-abcdefghijklmnopqrstuvwxyz", record.errors[0])

    def test_record_cancelled_persists_a_cancelled_entry(self):
        execution_history.record_cancelled("r3", "stop this", self._state(), autonomy_level=4)
        record = execution_history.get_by_id("r3")
        self.assertEqual(record.status, "cancelled")

    def test_bounded_retention_keeps_only_the_most_recent_entries(self):
        # Directly exercises the real bound via config, not a monkeypatch --
        # persists more than the configured limit and confirms the oldest
        # ones are the ones dropped.
        limit = settings.execution_history_limit
        for i in range(limit + 5):
            execution_history.record_completed(f"r{i}", f"request {i}", self._state())
        records = execution_history.get_recent()
        self.assertEqual(len(records), limit)
        kept_ids = {r.request_id for r in records}
        # The earliest ones (r0, r1, ...) should have been dropped.
        self.assertNotIn("r0", kept_ids)
        self.assertIn(f"r{limit + 4}", kept_ids)

    def test_get_recent_orders_most_recent_first(self):
        execution_history.record_completed("first", "req a", self._state())
        execution_history.record_completed("second", "req b", self._state())
        records = execution_history.get_recent()
        self.assertEqual(records[0].request_id, "second")
        self.assertEqual(records[1].request_id, "first")

    def test_get_recent_respects_limit_argument(self):
        for i in range(5):
            execution_history.record_completed(f"r{i}", f"request {i}", self._state())
        records = execution_history.get_recent(limit=2)
        self.assertEqual(len(records), 2)

    def test_get_by_id_returns_none_for_unknown_id(self):
        self.assertIsNone(execution_history.get_by_id("no-such-request"))

    def test_no_history_file_means_empty_recent_list(self):
        self.assertEqual(execution_history.get_recent(), [])


class TestGetActiveDelegatesToExecutionState(unittest.TestCase):

    def tearDown(self):
        unregister_active("history-active-test")

    def test_get_active_reflects_the_real_active_registry(self):
        state = ExecutionState(max_iterations=8)
        register_active("history-active-test", state)
        active = execution_history.get_active()
        self.assertIn(state, active)


if __name__ == "__main__":
    unittest.main()
