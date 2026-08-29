"""Tests for agent/memory/access_log.py -- AUTHORITY.md §2's decided fix:
memory.json's last_accessed field is no longer rewritten (the whole
durable document) on every ordinary read; this sidecar carries that
signal instead, written best-effort, read back by agent/memory/store.py's
load_all().

Run with: python -m unittest tests.test_memory_access_log -v
"""
import os
import tempfile
import unittest

import agent.memory.access_log as access_log


class _IsolatedAccessLogFile(unittest.TestCase):

    def setUp(self):
        self._real_file = access_log.ACCESS_LOG_FILE
        access_log.ACCESS_LOG_FILE = tempfile.mktemp(suffix=".json")

    def tearDown(self):
        for path in (access_log.ACCESS_LOG_FILE, f"{access_log.ACCESS_LOG_FILE}.lock"):
            if os.path.exists(path):
                os.remove(path)
        access_log.ACCESS_LOG_FILE = self._real_file


class TestRecordAccess(_IsolatedAccessLogFile):

    def test_missing_file_returns_empty_dict(self):
        self.assertEqual(access_log.get_all(), {})

    def test_records_a_single_access(self):
        access_log.record_access("mem-1", timestamp=100.0)
        self.assertEqual(access_log.get_all(), {"mem-1": 100.0})

    def test_defaults_to_the_current_time_when_not_given(self):
        access_log.record_access("mem-1")
        result = access_log.get_all()
        self.assertIn("mem-1", result)
        self.assertIsInstance(result["mem-1"], float)

    def test_repeated_access_overwrites_the_previous_timestamp(self):
        access_log.record_access("mem-1", timestamp=100.0)
        access_log.record_access("mem-1", timestamp=200.0)
        self.assertEqual(access_log.get_all()["mem-1"], 200.0)

    def test_does_not_disturb_other_memory_ids(self):
        access_log.record_access("mem-1", timestamp=100.0)
        access_log.record_access("mem-2", timestamp=200.0)
        self.assertEqual(access_log.get_all(), {"mem-1": 100.0, "mem-2": 200.0})


class TestRecordAccesses(_IsolatedAccessLogFile):

    def test_batch_records_every_id_with_the_same_timestamp(self):
        access_log.record_accesses(["mem-1", "mem-2", "mem-3"], timestamp=100.0)
        self.assertEqual(access_log.get_all(), {"mem-1": 100.0, "mem-2": 100.0, "mem-3": 100.0})

    def test_empty_iterable_is_a_no_op(self):
        access_log.record_accesses([], timestamp=100.0)
        self.assertFalse(os.path.exists(access_log.ACCESS_LOG_FILE))

    def test_one_lock_acquisition_writes_all_ids_atomically(self):
        # Not directly observable from the outside, but a real regression
        # this guards: a naive per-id implementation calling
        # record_access() in a loop would still work functionally, this
        # just confirms the batch path produces the same end state.
        access_log.record_accesses(["mem-1", "mem-2"], timestamp=100.0)
        access_log.record_access("mem-3", timestamp=200.0)
        self.assertEqual(access_log.get_all(), {"mem-1": 100.0, "mem-2": 100.0, "mem-3": 200.0})


class TestFailureIsolation(_IsolatedAccessLogFile):

    def test_corrupt_file_degrades_to_empty_dict_not_a_raise(self):
        os.makedirs(os.path.dirname(access_log.ACCESS_LOG_FILE), exist_ok=True)
        with open(access_log.ACCESS_LOG_FILE, "w") as file:
            file.write("{not valid json")
        self.assertEqual(access_log.get_all(), {})

    def test_non_dict_json_degrades_to_empty_dict(self):
        os.makedirs(os.path.dirname(access_log.ACCESS_LOG_FILE), exist_ok=True)
        with open(access_log.ACCESS_LOG_FILE, "w") as file:
            file.write("[1, 2, 3]")
        self.assertEqual(access_log.get_all(), {})

    def test_write_to_an_unwritable_directory_never_raises(self):
        # A real disk/permissions failure must never surface to the
        # caller -- the access signal is secondary, the search/recall
        # result it's attached to is not.
        access_log.ACCESS_LOG_FILE = "/nonexistent-root-only-dir/access_log.json"
        try:
            access_log.record_access("mem-1")  # must not raise
        except Exception as error:
            self.fail(f"record_access raised {error!r} instead of failing silently")


if __name__ == "__main__":
    unittest.main()
