"""Tests for agent/planner.py. create_plan() makes a real Anthropic API
call, which is mocked here (matching this project's established policy of
not requiring paid API calls in the automated suite) -- is_complex() and
the Plan/PlanStep data model are pure and tested directly with no mocking
needed.

Run with: python -m unittest tests.test_planner -v
"""
import json
import unittest
from unittest.mock import MagicMock, patch

from agent.planner import Plan, PlanStep, StepStatus, create_plan, is_complex


class TestIsComplex(unittest.TestCase):

    def test_simple_question_is_not_complex(self):
        self.assertFalse(is_complex("what is the weather"))

    def test_empty_request_is_not_complex(self):
        self.assertFalse(is_complex(""))

    def test_sequencing_words_are_complex(self):
        self.assertTrue(is_complex("open my email and then draft a reply"))

    def test_compare_signal_is_complex(self):
        self.assertTrue(is_complex("research three laptops and compare them"))

    def test_multiple_ands_are_complex(self):
        self.assertTrue(is_complex("check my email and my calendar and my reminders"))

    def test_long_request_is_complex(self):
        long_request = " ".join(["word"] * 35)
        self.assertTrue(is_complex(long_request))

    def test_short_single_ask_is_not_complex(self):
        self.assertFalse(is_complex("remind me to call mom tomorrow"))


def _mock_response(json_text):
    block = MagicMock()
    block.type = "text"
    block.text = json_text
    response = MagicMock()
    response.content = [block]
    return response


class TestCreatePlan(unittest.TestCase):

    @patch("agent.planner.anthropic_client")
    def test_parses_structured_steps(self, mock_client):
        mock_client.messages.create.return_value = _mock_response(json.dumps([
            {"description": "Research laptops", "required_tools": ["research_agent"]},
            {"description": "Compare specs", "required_tools": []},
            {"description": "Write report", "required_tools": ["create_note"]},
        ]))

        plan = create_plan("research laptops, compare them, write a report")

        self.assertEqual(plan.total, 3)
        self.assertEqual(plan.steps[0].description, "Research laptops")
        self.assertEqual(plan.steps[0].required_tools, ["research_agent"])
        self.assertEqual(plan.steps[2].description, "Write report")
        call = mock_client.messages.create.call_args.kwargs
        from config.settings import settings
        self.assertEqual(call["model"], settings.planner_model)
        self.assertEqual(call["max_tokens"], 512)

    @patch("agent.planner.anthropic_client")
    def test_first_step_starts_in_progress(self, mock_client):
        mock_client.messages.create.return_value = _mock_response(json.dumps([
            {"description": "Step one"},
            {"description": "Step two"},
        ]))

        plan = create_plan("do two things")

        self.assertEqual(plan.steps[0].status, StepStatus.IN_PROGRESS)
        self.assertEqual(plan.steps[1].status, StepStatus.PENDING)

    @patch("agent.planner.anthropic_client")
    def test_strips_markdown_code_fences(self, mock_client):
        mock_client.messages.create.return_value = _mock_response(
            '```json\n[{"description": "Only step"}]\n```'
        )
        plan = create_plan("do one thing")
        self.assertEqual(plan.total, 1)

    @patch("agent.planner.anthropic_client")
    def test_caps_at_max_steps(self, mock_client):
        from agent.planner import MAX_STEPS
        many_steps = [{"description": f"Step {i}"} for i in range(MAX_STEPS + 5)]
        mock_client.messages.create.return_value = _mock_response(json.dumps(many_steps))

        plan = create_plan("do many things")
        self.assertEqual(plan.total, MAX_STEPS)

    @patch("agent.planner.anthropic_client")
    def test_malformed_json_falls_back_to_single_step(self, mock_client):
        mock_client.messages.create.return_value = _mock_response("not valid json at all")

        plan = create_plan("do the thing")

        self.assertEqual(plan.total, 1)
        self.assertEqual(plan.steps[0].description, "do the thing")

    @patch("agent.planner.anthropic_client")
    def test_api_exception_falls_back_to_single_step(self, mock_client):
        mock_client.messages.create.side_effect = RuntimeError("API down")

        plan = create_plan("do the thing")

        self.assertEqual(plan.total, 1)
        self.assertEqual(plan.steps[0].description, "do the thing")


class TestPlan(unittest.TestCase):

    def _plan(self, n=3):
        steps = [PlanStep(step_id=i + 1, description=f"Step {i + 1}") for i in range(n)]
        steps[0].status = StepStatus.IN_PROGRESS
        return Plan(request="test", steps=steps)

    def test_current_step_starts_at_first(self):
        plan = self._plan()
        self.assertEqual(plan.current_step.step_id, 1)

    def test_advance_completes_current_and_starts_next(self):
        plan = self._plan()
        plan.advance()
        self.assertEqual(plan.steps[0].status, StepStatus.COMPLETED)
        self.assertEqual(plan.steps[1].status, StepStatus.IN_PROGRESS)
        self.assertEqual(plan.current_step.step_id, 2)

    def test_advance_with_failed_marks_failed_not_completed(self):
        plan = self._plan()
        plan.advance(failed=True)
        self.assertEqual(plan.steps[0].status, StepStatus.FAILED)

    def test_is_finished_after_all_steps_advanced(self):
        plan = self._plan(n=2)
        self.assertFalse(plan.is_finished)
        plan.advance()
        plan.advance()
        self.assertTrue(plan.is_finished)
        self.assertIsNone(plan.current_step)

    def test_advance_past_the_end_is_a_safe_no_op(self):
        plan = self._plan(n=1)
        plan.advance()
        plan.advance()  # already finished -- should not raise
        self.assertTrue(plan.is_finished)

    def test_completed_and_failed_counts(self):
        plan = self._plan(n=3)
        plan.advance()  # step 1 completed
        plan.advance(failed=True)  # step 2 failed
        self.assertEqual(plan.completed_count, 1)
        self.assertEqual(plan.failed_count, 1)

    def test_progress_text_uses_symbols(self):
        plan = self._plan(n=2)
        plan.advance()
        text = plan.progress_text()
        self.assertIn("✓", text)
        self.assertIn("→", text)


if __name__ == "__main__":
    unittest.main()
