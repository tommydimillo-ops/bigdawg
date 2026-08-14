"""Tests for agent/agents/router.py -- pure, deterministic routing, no
model calls, no execution. Every example from the Phase 7 spec's
"ROUTING EXAMPLES" section is pinned here exactly.

Run with: python -m unittest tests.test_agents_router -v
"""
import unittest

from agent.agents.router import AgentDestination, route


class TestSpecRoutingExamples(unittest.TestCase):
    """Section 12's exact routing examples."""

    def test_arithmetic_question_is_direct(self):
        self.assertEqual(route("What's 2+2?").destination, AgentDestination.DIRECT)

    def test_fix_python_error_is_coding(self):
        self.assertEqual(route("Fix this Python error.").destination, AgentDestination.CODING)

    def test_explain_failing_test_is_coding(self):
        self.assertEqual(route("Explain why this test is failing.").destination, AgentDestination.CODING)

    def test_add_a_new_feature_is_coding(self):
        self.assertEqual(route("Add a new feature.").destination, AgentDestination.CODING)

    def test_refactor_module_is_coding(self):
        self.assertEqual(route("Refactor this module.").destination, AgentDestination.CODING)

    def test_research_laptops_is_research(self):
        self.assertEqual(
            route("Research the best laptops under $1,000.").destination, AgentDestination.RESEARCH,
        )

    def test_check_tests_pass_is_qa(self):
        self.assertEqual(
            route("Check whether the changes pass all tests.").destination, AgentDestination.QA,
        )

    def test_remember_preference_is_memory(self):
        self.assertEqual(
            route("Remember that I prefer dark mode.").destination, AgentDestination.MEMORY,
        )

    def test_open_safari_is_direct(self):
        self.assertEqual(route("Open Safari.").destination, AgentDestination.DIRECT)


class TestRouteEdgeCases(unittest.TestCase):

    def test_empty_string_is_direct(self):
        decision = route("")
        self.assertEqual(decision.destination, AgentDestination.DIRECT)
        self.assertEqual(decision.reason, "empty request")

    def test_whitespace_only_is_direct(self):
        self.assertEqual(route("   ").destination, AgentDestination.DIRECT)

    def test_none_like_falsy_is_direct(self):
        self.assertEqual(route(None).destination, AgentDestination.DIRECT)

    def test_single_weak_keyword_is_not_enough(self):
        # "error" alone is a 1-point CODING keyword -- below the
        # confidence floor on its own.
        self.assertEqual(route("There was an error somewhere.").destination, AgentDestination.DIRECT)

    def test_case_insensitive(self):
        self.assertEqual(route("REMEMBER THAT I PREFER DARK MODE").destination, AgentDestination.MEMORY)

    def test_decision_never_executes_anything(self):
        # route() is a pure function -- calling it repeatedly must be
        # side-effect-free and idempotent.
        first = route("Research the best laptops.")
        second = route("Research the best laptops.")
        self.assertEqual(first, second)

    def test_agent_name_set_for_non_direct(self):
        decision = route("Refactor this module.")
        self.assertEqual(decision.agent_name, "coding")

    def test_agent_name_none_for_direct(self):
        decision = route("What's 2+2?")
        self.assertIsNone(decision.agent_name)

    def test_task_type_set_for_non_direct(self):
        decision = route("Remember that I prefer dark mode.")
        self.assertEqual(decision.task_type, "memory")

    def test_confidence_bounded_to_one(self):
        decision = route("research the best laptops")
        self.assertLessEqual(decision.confidence, 1.0)
        self.assertGreaterEqual(decision.confidence, 0.0)


if __name__ == "__main__":
    unittest.main()
