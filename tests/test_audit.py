"""Tests for agent/audit.py -- the security/action log. Phase 9 Milestone
3 added a cross-process fcntl.flock around log_action's write (see that
module's own comment) because bounded-parallel coworker delegation means
several genuinely separate OS processes can now append here at close to
the same instant, not just several threads in one process. Isolates
LOG_FILE to a temp path throughout, matching this project's established
file-backed-store test isolation convention.

Run with: python -m unittest tests.test_audit -v
"""
import json
import os
import subprocess
import sys
import tempfile
import threading
import unittest

import agent.audit as audit


class IsolatedLogFileTestCase(unittest.TestCase):

    def setUp(self):
        self._real_log_file = audit.LOG_FILE
        self._real_log_dir = audit.LOG_DIR
        audit.LOG_DIR = tempfile.mkdtemp()
        audit.LOG_FILE = os.path.join(audit.LOG_DIR, "audit.log")

    def tearDown(self):
        audit.LOG_DIR = self._real_log_dir
        audit.LOG_FILE = self._real_log_file


class TestBasicLogging(IsolatedLogFileTestCase):

    def test_log_action_writes_a_readable_entry(self):
        audit.log_action("get_weather", {"city": "SF"}, "72F and sunny")
        entries = audit.recent_actions()
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["tool"], "get_weather")
        self.assertEqual(entries[0]["result"], "72F and sunny")

    def test_each_line_is_independently_valid_json(self):
        for i in range(5):
            audit.log_action("get_weather", {}, f"result {i}")
        with open(audit.LOG_FILE) as file:
            lines = file.readlines()
        self.assertEqual(len(lines), 5)
        for line in lines:
            json.loads(line)  # must not raise

    def test_long_fields_are_truncated(self):
        audit.log_action("x", {}, "y" * 1000)
        entries = audit.recent_actions()
        self.assertLessEqual(len(entries[0]["result"]), audit.MAX_FIELD_LENGTH + 1)

    def test_recent_actions_respects_limit(self):
        for i in range(10):
            audit.log_action("x", {}, f"result {i}")
        entries = audit.recent_actions(limit=3)
        self.assertEqual(len(entries), 3)

    def test_no_log_file_yet_returns_empty(self):
        self.assertEqual(audit.recent_actions(), [])


class TestConcurrentThreadWrites(IsolatedLogFileTestCase):
    """A real threading stress test (not mocked) -- many threads in this
    one process hammering log_action concurrently must never produce a
    corrupted or interleaved line."""

    def test_many_concurrent_threads_produce_exactly_as_many_valid_lines(self):
        thread_count = 20

        def _write(i):
            audit.log_action("concurrent_tool", {"i": i}, f"result-{i}" * 20)

        threads = [threading.Thread(target=_write, args=(i,)) for i in range(thread_count)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        with open(audit.LOG_FILE) as file:
            lines = [line for line in file.readlines() if line.strip()]

        self.assertEqual(len(lines), thread_count)
        parsed = [json.loads(line) for line in lines]  # any corruption raises here
        self.assertEqual(len(parsed), thread_count)
        self.assertTrue(all(entry["tool"] == "concurrent_tool" for entry in parsed))


class TestConcurrentProcessWrites(IsolatedLogFileTestCase):
    """A real subprocess stress test -- separate OS processes (not
    threads) appending to the SAME audit.log at once, the exact new
    scenario Milestone 3's bounded-parallel coworker subprocesses
    introduce. Proves the fcntl.flock fix, not just the pre-existing
    in-process threading.Lock."""

    def test_multiple_real_processes_never_corrupt_the_log(self):
        writer_script = (
            "import sys; sys.path.insert(0, %r)\n"
            "import agent.audit as audit\n"
            "audit.LOG_FILE = %r\n"
            "audit.LOG_DIR = %r\n"
            "import os\n"
            "os.makedirs(audit.LOG_DIR, exist_ok=True)\n"
            "for i in range(15):\n"
            "    audit.log_action('proc_tool', {'i': i}, 'x' * 50)\n"
        ) % (os.path.dirname(os.path.dirname(os.path.abspath(__file__))), audit.LOG_FILE, audit.LOG_DIR)

        script_path = os.path.join(audit.LOG_DIR, "writer.py")
        with open(script_path, "w") as f:
            f.write(writer_script)

        processes = [
            subprocess.Popen([sys.executable, script_path])
            for _ in range(4)
        ]
        for p in processes:
            self.assertEqual(p.wait(timeout=15), 0)

        with open(audit.LOG_FILE) as file:
            lines = [line for line in file.readlines() if line.strip()]

        self.assertEqual(len(lines), 4 * 15)
        for line in lines:
            json.loads(line)  # any interleaved/corrupted line raises here


if __name__ == "__main__":
    unittest.main()
