"""Tests for agent/task_classifier.py -- deterministic (no model call)
task classification behind Phase 9 Milestone 2's task-aware model router.

Run with: python -m unittest tests.test_task_classifier -v
"""
import unittest

from agent.task_classifier import TaskRequirements, TaskType, classify


class TestTaskTypeDetection(unittest.TestCase):

    def test_debugging_request(self):
        self.assertEqual(classify("why does my script keep crashing").task_type, TaskType.DEBUGGING)

    def test_coding_request(self):
        self.assertEqual(classify("write a python function to reverse a list").task_type, TaskType.CODING)

    def test_current_research_request(self):
        self.assertEqual(classify("what's the latest news on the election today").task_type, TaskType.RESEARCH_CURRENT)

    def test_general_research_request(self):
        self.assertEqual(classify("research the history of the transistor").task_type, TaskType.RESEARCH_GENERAL)

    def test_vision_request(self):
        self.assertEqual(classify("what's on my screen right now").task_type, TaskType.VISION)

    def test_writing_request(self):
        self.assertEqual(classify("write a blog post about hiking").task_type, TaskType.WRITING)

    def test_planning_request(self):
        self.assertEqual(classify("help me plan my trip to Japan").task_type, TaskType.PLANNING)

    def test_summarization_request(self):
        self.assertEqual(classify("summarize this article for me").task_type, TaskType.SUMMARIZATION)

    def test_document_analysis_request(self):
        self.assertEqual(classify("read this pdf and tell me what it says").task_type, TaskType.DOCUMENT_ANALYSIS)

    def test_complex_reasoning_from_signal_words(self):
        self.assertEqual(
            classify("compare these two options and then tell me which is better").task_type,
            TaskType.REASONING_COMPLEX,
        )

    def test_complex_reasoning_from_length(self):
        long_request = " ".join(["word"] * 70)
        self.assertEqual(classify(long_request).task_type, TaskType.REASONING_COMPLEX)

    def test_simple_reasoning_from_short_request(self):
        self.assertEqual(classify("hi").task_type, TaskType.REASONING_SIMPLE)

    def test_general_chat_fallback(self):
        # Long enough to clear the short-request threshold, no coding/
        # research/vision/writing/planning/summarize/complexity signal --
        # ordinary conversation with no special routing need.
        self.assertEqual(
            classify("tell me a little bit about how your day has been going so far").task_type,
            TaskType.GENERAL_CHAT,
        )

    def test_empty_text_does_not_raise(self):
        result = classify("")
        self.assertIsInstance(result, TaskRequirements)

    def test_none_text_does_not_raise(self):
        result = classify(None)
        self.assertIsInstance(result, TaskRequirements)


class TestRequirementFlags(unittest.TestCase):

    def test_current_web_flag_set_for_current_events(self):
        self.assertTrue(classify("what's happening in the news right now").needs_current_web)

    def test_current_web_flag_not_set_for_general_question(self):
        self.assertFalse(classify("what is the capital of France").needs_current_web)

    def test_vision_flag_set_for_screen_request(self):
        self.assertTrue(classify("take a look at this screenshot").needs_vision)

    def test_privacy_flag_set_for_explicit_privacy_wording(self):
        self.assertTrue(classify("this is confidential, keep it local only").privacy_sensitive)

    def test_privacy_flag_not_set_by_default(self):
        self.assertFalse(classify("what's the weather like").privacy_sensitive)

    def test_needs_tools_always_true(self):
        # Every real Jarvis request runs through the same tool-capable
        # loop regardless of task type.
        self.assertTrue(classify("hi").needs_tools)
        self.assertTrue(classify("write me some code").needs_tools)

    def test_large_context_flag_for_long_requests(self):
        long_request = " ".join(["word"] * 70)
        self.assertTrue(classify(long_request).needs_large_context)

    def test_large_context_flag_for_document_analysis(self):
        self.assertTrue(classify("read this pdf").needs_large_context)

    def test_quality_priority_for_coding(self):
        self.assertTrue(classify("fix this bug in my code").quality_priority)

    def test_cost_priority_for_simple_requests(self):
        self.assertTrue(classify("hi").cost_priority)

    def test_cost_priority_not_set_for_coding(self):
        self.assertFalse(classify("write a python function").cost_priority)

    def test_latency_priority_for_simple_requests(self):
        self.assertTrue(classify("hi").latency_priority)


class TestSourceAwareClassification(unittest.TestCase):

    def test_voice_source_sets_latency_priority_even_for_a_long_request(self):
        long_request = " ".join(["word"] * 70)
        result = classify(long_request, source="voice")
        self.assertTrue(result.latency_priority)

    def test_chat_source_does_not_force_latency_priority(self):
        long_request = " ".join(["word"] * 70)
        result = classify(long_request, source="chat")
        self.assertFalse(result.latency_priority)


class TestTaskRequirementsIsImmutable(unittest.TestCase):

    def test_cannot_mutate_a_result(self):
        result = classify("hi")
        with self.assertRaises(Exception):
            result.task_type = TaskType.CODING


if __name__ == "__main__":
    unittest.main()
