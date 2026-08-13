"""Tests for agent/verification.py -- proportional post-action
verification. Isolates database.memory.MEMORY_FILE for the tools whose
verifier re-queries a real store (schedule_task).

Run with: python -m unittest tests.test_verification -v
"""
import os
import tempfile
import unittest

import database.memory as dbmem
from agent.verification import verify


class TestGenericStringCheck(unittest.TestCase):

    def test_success_result_is_ok(self):
        result = verify("add_reminder", {}, "Added reminder 'Buy milk' due tomorrow")
        self.assertTrue(result.ok)

    def test_could_not_prefix_is_not_ok(self):
        result = verify("add_reminder", {}, "Could not add reminder: AppleScript error")
        self.assertFalse(result.ok)

    def test_error_prefix_is_not_ok(self):
        result = verify("open_file", {}, "Error: file not found")
        self.assertFalse(result.ok)

    def test_unrelated_tool_with_no_specific_verifier_still_checked(self):
        result = verify("create_note", {}, "Created note 'Shopping list'")
        self.assertTrue(result.ok)
        self.assertIn("no specific verification", result.note)


class TestMemoryWriteVerification(unittest.TestCase):

    def test_successful_memory_write(self):
        result = verify("remember_fact", {}, "I'll remember that the sky is blue")
        self.assertTrue(result.ok)

    def test_refused_memory_write(self):
        result = verify("remember_fact", {}, "Didn't save that: looks like it contains a credential")
        self.assertFalse(result.ok)

    def test_covers_learn_rule_and_note_pattern_too(self):
        self.assertTrue(verify("learn_rule", {}, "Got it — from now on: be brief").ok)
        self.assertTrue(verify("note_pattern", {}, "Noted: likes short replies").ok)


class TestScheduleTaskVerification(unittest.TestCase):

    def setUp(self):
        self._real_memory_file = dbmem.MEMORY_FILE
        self._temp_file = tempfile.mktemp(suffix=".json")
        dbmem.MEMORY_FILE = self._temp_file

    def tearDown(self):
        dbmem.MEMORY_FILE = self._real_memory_file
        if os.path.exists(self._temp_file):
            os.remove(self._temp_file)
        tmp = f"{self._temp_file}.tmp"
        if os.path.exists(tmp):
            os.remove(tmp)

    def test_verifies_a_task_that_really_exists(self):
        from agent.scheduled_tasks import add_task

        task, _ = add_task("test task", "09:00")
        result = verify("schedule_task", {}, f"Scheduled (id {task['id']}): \"test task\" daily at 09:00.")
        self.assertTrue(result.ok)
        self.assertIn("confirmed", result.note)

    def test_flags_a_task_id_that_does_not_actually_exist(self):
        result = verify("schedule_task", {}, "Scheduled (id deadbeef): \"fake\" daily at 09:00.")
        self.assertFalse(result.ok)

    def test_falls_back_to_string_check_if_no_id_in_result(self):
        result = verify("schedule_task", {}, "Could not schedule: bad time format")
        self.assertFalse(result.ok)


if __name__ == "__main__":
    unittest.main()
