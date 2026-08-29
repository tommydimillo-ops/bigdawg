"""Tests for agent/verification.py -- proportional post-action
verification. Isolates database.memory.MEMORY_FILE for the tools whose
verifier re-queries a real store (schedule_task).

Run with: python -m unittest tests.test_verification -v
"""
import json
import os
import tempfile
import unittest

import agent.scheduled_tasks as scheduled_tasks
import database.memory as dbmem
from agent.agents.models import AgentResult
from agent.verification import verify, verify_agent_result


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

        # add_task() below writes through to the real production
        # scheduled_tasks.json unless this is redirected too -- previously
        # unisolated, so every test run silently left a real "test task"
        # entry behind (confirmed live: 10 accumulated duplicates from
        # repeated suite runs).
        self._real_tasks_file = scheduled_tasks.TASKS_FILE
        self._temp_tasks_file = tempfile.mktemp(suffix=".json")
        scheduled_tasks.TASKS_FILE = self._temp_tasks_file

    def tearDown(self):
        dbmem.MEMORY_FILE = self._real_memory_file
        if os.path.exists(self._temp_file):
            os.remove(self._temp_file)
        tmp = f"{self._temp_file}.tmp"
        if os.path.exists(tmp):
            os.remove(tmp)

        scheduled_tasks.TASKS_FILE = self._real_tasks_file
        for path in (self._temp_tasks_file, f"{self._temp_tasks_file}.tmp"):
            if os.path.exists(path):
                os.remove(path)

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


class TestVerifyAgentResult(unittest.TestCase):
    """Phase 9 Milestone 3 -- verifying a coworker AgentResult, not just a
    single tool call's result string."""

    def _result(self, **overrides):
        defaults = dict(
            success=True, agent_name="memory", request_id="req-1", result="I'll remember that.",
        )
        defaults.update(overrides)
        return AgentResult(**defaults)

    def test_explicit_failure_is_not_ok(self):
        result = verify_agent_result(self._result(success=False, error="RuntimeError: network down"))
        self.assertFalse(result.ok)
        self.assertIn("network down", result.note)

    def test_cancelled_is_not_ok(self):
        result = verify_agent_result(self._result(success=False, cancelled=True))
        self.assertFalse(result.ok)
        self.assertIn("cancelled", result.note)

    def test_explicit_verification_status_failed_overrides_success_true(self):
        result = verify_agent_result(self._result(
            agent_name="qa", success=True, verification_status="failed", result="One or more tests failed.",
        ))
        self.assertFalse(result.ok)

    def test_qa_passing_test_suite_is_ok(self):
        result = verify_agent_result(self._result(
            agent_name="qa", success=True, verification_status="passed", result="All tests passed.",
        ))
        self.assertTrue(result.ok)

    def test_coding_agent_nonzero_suite_exit_code_overrides_success_true(self):
        result = verify_agent_result(self._result(
            agent_name="coding", success=True, result="done",
            metadata={"suite_exit_code": 1, "tests_run": 1514, "tests_failed": 3},
        ))
        self.assertFalse(result.ok)
        self.assertIn("exit code 1", result.note)

    def test_coding_agent_zero_suite_exit_code_is_ok(self):
        result = verify_agent_result(self._result(
            agent_name="coding", success=True, result="done",
            metadata={"suite_exit_code": 0, "tests_run": 1514, "tests_failed": 0},
        ))
        self.assertTrue(result.ok)

    def test_missing_suite_exit_code_does_not_affect_unrelated_agents(self):
        result = verify_agent_result(self._result(agent_name="memory", result="I'll remember that."))
        self.assertTrue(result.ok)

    def test_deferred_to_executor_is_ok_with_nothing_to_verify_yet(self):
        result = verify_agent_result(self._result(
            agent_name="coding", success=True, result="", metadata={"deferred_to_executor": True},
        ))
        self.assertTrue(result.ok)
        self.assertIn("nothing agent-specific to verify", result.note)

    def test_research_result_with_a_source_is_ok(self):
        result = verify_agent_result(self._result(
            agent_name="research", result="Prices range widely, according to reviews on cnet.com.",
        ))
        self.assertTrue(result.ok)

    def test_research_result_with_a_url_is_ok(self):
        result = verify_agent_result(self._result(
            agent_name="research", result="See https://example.com/reviews for details.",
        ))
        self.assertTrue(result.ok)

    def test_research_result_with_no_source_evidence_is_not_ok(self):
        result = verify_agent_result(self._result(
            agent_name="research", result="The best laptop is whatever one you like most.",
        ))
        self.assertFalse(result.ok)
        self.assertIn("doesn't mention any source", result.note)

    def test_memory_agent_generic_success_is_ok(self):
        result = verify_agent_result(self._result(agent_name="memory", result="I'll remember that."))
        self.assertTrue(result.ok)

    def test_memory_agent_failure_marker_in_result_text_is_not_ok(self):
        result = verify_agent_result(self._result(
            agent_name="memory", result="Could not save that fact.",
        ))
        self.assertFalse(result.ok)


class TestSendMessageViaOpenClawVerification(unittest.TestCase):
    """The generic string check (FAILURE_MARKERS) is not safe for this
    tool's JSON result -- e.g. {"sent": false, "delivery_status":
    "uncertain", ...} contains none of those marker words. This verifier
    must parse the JSON directly and fail closed."""

    def _dump(self, **fields):
        return json.dumps(fields)

    def test_confirmed_delivery_is_ok(self):
        result = verify("send_message_via_openclaw", {}, self._dump(
            sent=True, delivery_status="confirmed", channel="telegram",
            target="allowed-target-1", message_id="m1",
        ))
        self.assertTrue(result.ok)

    def test_failed_delivery_is_not_ok(self):
        result = verify("send_message_via_openclaw", {}, self._dump(
            sent=False, delivery_status="failed", error="target is not allowlisted",
        ))
        self.assertFalse(result.ok)
        self.assertIn("failed", result.note.lower())
        self.assertIn("target is not allowlisted", result.note)

    def test_uncertain_delivery_is_not_ok(self):
        result = verify("send_message_via_openclaw", {}, self._dump(
            sent=False, delivery_status="uncertain", channel="telegram", target="allowed-target-1",
        ))
        self.assertFalse(result.ok)
        self.assertIn("uncertain", result.note.lower())

    def test_uncertain_delivery_note_says_not_successful(self):
        result = verify("send_message_via_openclaw", {}, self._dump(
            sent=False, delivery_status="uncertain",
        ))
        self.assertIn("NOT", result.note)
        self.assertIn("successful", result.note.lower())

    def test_uncertain_delivery_note_says_must_not_retry(self):
        result = verify("send_message_via_openclaw", {}, self._dump(
            sent=False, delivery_status="uncertain",
        ))
        self.assertIn("retr", result.note.lower())

    def test_sent_true_but_wrong_status_is_not_ok(self):
        # A malformed/inconsistent combination -- must not be treated as
        # success just because "sent" is truthy.
        result = verify("send_message_via_openclaw", {}, self._dump(
            sent=True, delivery_status="uncertain",
        ))
        self.assertFalse(result.ok)

    def test_unknown_delivery_status_fails_closed(self):
        result = verify("send_message_via_openclaw", {}, self._dump(
            sent=True, delivery_status="something_new",
        ))
        self.assertFalse(result.ok)

    def test_missing_delivery_status_fails_closed(self):
        result = verify("send_message_via_openclaw", {}, self._dump(sent=True))
        self.assertFalse(result.ok)

    def test_malformed_json_fails_closed(self):
        result = verify("send_message_via_openclaw", {}, "not json at all")
        self.assertFalse(result.ok)

    def test_json_array_instead_of_object_fails_closed(self):
        result = verify("send_message_via_openclaw", {}, json.dumps(["sent", "confirmed"]))
        self.assertFalse(result.ok)

    def test_empty_string_fails_closed(self):
        result = verify("send_message_via_openclaw", {}, "")
        self.assertFalse(result.ok)


if __name__ == "__main__":
    unittest.main()
