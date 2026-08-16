"""Tests for agent/research_agent.py's Phase 9 Milestone 3 routing --
research() now goes through classify_task()/build_fallback_chain() (the
same M2 primitives the outer request uses) instead of always calling
Anthropic directly. Mocks build_fallback_chain directly (already
independently tested in tests/test_model_router.py) to control exactly
which provider/model is offered where the specific candidate matters, and
mocks the underlying provider client at the same boundary
tests/test_usage_limits_integration.py already established for this
module. Isolates usage.USAGE_FILE throughout -- every success path here
calls record_llm_usage for real, and this project's own testing policy
requires isolating any test that exercises a write to a real file-backed
store (see CLAUDE.md's "How to test" section). No real network call
anywhere in this file.

Run with: python -m unittest tests.test_research_agent -v
"""
import os
import tempfile
import unittest
from unittest.mock import MagicMock, patch

import agent.usage as usage
from agent.model_router import ModelChoice
from agent.research_agent import research


def _text_block(text):
    block = MagicMock()
    block.type = "text"
    block.text = text
    return block


def _claude_response(text):
    response = MagicMock(stop_reason="end_turn")
    response.content = [_text_block(text)]
    response.usage = MagicMock(input_tokens=10, output_tokens=5)
    return response


def _openai_response(text):
    response = MagicMock()
    message = MagicMock(content=text, tool_calls=None)
    response.choices = [MagicMock(message=message)]
    response.usage = MagicMock(prompt_tokens=10, completion_tokens=5)
    return response


class IsolatedUsageFileTestCase(unittest.TestCase):

    def setUp(self):
        self._real_usage_file = usage.USAGE_FILE
        usage.USAGE_FILE = tempfile.mktemp(suffix=".json")

    def tearDown(self):
        for path in (usage.USAGE_FILE, f"{usage.USAGE_FILE}.lock"):
            if os.path.exists(path):
                os.remove(path)
        usage.USAGE_FILE = self._real_usage_file


class TestProviderSelection(IsolatedUsageFileTestCase):

    @patch("agent.research_agent.build_fallback_chain")
    @patch("agent.research_agent.anthropic_client")
    def test_uses_the_router_chosen_anthropic_model(self, mock_client, mock_chain):
        mock_chain.return_value = [ModelChoice(provider="anthropic", model="claude-haiku-4-5-20251001")]
        mock_client.messages.create.return_value = _claude_response("Found it.")

        result = research("what is the best laptop")

        self.assertEqual(result, "Found it.")
        _, kwargs = mock_client.messages.create.call_args
        self.assertEqual(kwargs["model"], "claude-haiku-4-5-20251001")

    @patch("agent.research_agent.build_fallback_chain")
    @patch("agent.research_agent.openai_client")
    def test_uses_the_router_chosen_openai_model(self, mock_client, mock_chain):
        mock_chain.return_value = [ModelChoice(provider="openai", model="gpt-5.6-luna")]
        mock_client.chat.completions.create.return_value = _openai_response("Found it via OpenAI.")

        result = research("what is the best laptop")

        self.assertEqual(result, "Found it via OpenAI.")
        _, kwargs = mock_client.chat.completions.create.call_args
        self.assertEqual(kwargs["model"], "gpt-5.6-luna")

    @patch("agent.research_agent.build_fallback_chain")
    @patch("agent.research_agent.xai_client")
    def test_uses_the_router_chosen_xai_model(self, mock_client, mock_chain):
        mock_chain.return_value = [ModelChoice(provider="xai", model="grok-4.3")]
        mock_client.chat.completions.create.return_value = _openai_response("Found it via Grok.")

        result = research("what is the best laptop")

        self.assertEqual(result, "Found it via Grok.")
        _, kwargs = mock_client.chat.completions.create.call_args
        self.assertEqual(kwargs["model"], "grok-4.3")

    @patch("agent.research_agent.build_fallback_chain")
    @patch("agent.research_agent._call_perplexity_agent")
    def test_uses_perplexity_via_a_single_grounded_call_not_a_tool_loop(self, mock_call, mock_chain):
        mock_chain.return_value = [ModelChoice(provider="perplexity", model="low")]
        mock_call.return_value = {
            "output": [{"type": "message", "content": [{"type": "output_text", "text": "Grounded answer."}]}],
        }

        result = research("what's happening in the news today")

        self.assertEqual(result, "Grounded answer.")
        mock_call.assert_called_once()
        self.assertEqual(mock_call.call_args[0][0], "low")

    @patch("agent.research_agent.build_fallback_chain")
    @patch("agent.research_agent.xai_client")
    @patch("agent.research_agent.openai_client")
    def test_a_premium_provider_is_not_auto_selected_just_because_a_cheaper_one_is_available(
        self, mock_openai, mock_xai, mock_chain,
    ):
        # The router itself (agent/model_router.py) already decides
        # ordering -- this test only confirms research() calls whichever
        # candidate the chain actually puts first, never reaching for a
        # later/pricier one on its own initiative.
        mock_chain.return_value = [
            ModelChoice(provider="openai", model="gpt-5.6-luna"),
            ModelChoice(provider="xai", model="grok-4.6"),
        ]
        mock_openai.chat.completions.create.return_value = _openai_response("cheap answer")

        research("what is the best laptop")

        mock_openai.chat.completions.create.assert_called_once()
        mock_xai.chat.completions.create.assert_not_called()


class TestFallbackOnFailure(IsolatedUsageFileTestCase):

    @patch("agent.research_agent.build_fallback_chain")
    @patch("agent.research_agent.openai_client")
    @patch("agent.research_agent.anthropic_client")
    def test_provider_unavailable_falls_back_to_the_next_candidate(self, mock_anthropic, mock_openai, mock_chain):
        mock_chain.return_value = [
            ModelChoice(provider="anthropic", model="claude-sonnet-5"),
            ModelChoice(provider="openai", model="gpt-5.6-terra"),
        ]
        mock_anthropic.messages.create.side_effect = ConnectionError("down")
        mock_openai.chat.completions.create.return_value = _openai_response("Found it via fallback.")

        result = research("what is the best laptop")

        self.assertEqual(result, "Found it via fallback.")
        mock_anthropic.messages.create.assert_called_once()
        mock_openai.chat.completions.create.assert_called_once()

    @patch("agent.research_agent.build_fallback_chain")
    @patch("agent.research_agent.anthropic_client")
    def test_every_candidate_failing_returns_an_honest_message_not_an_exception(self, mock_client, mock_chain):
        mock_chain.return_value = [ModelChoice(provider="anthropic", model="claude-sonnet-5")]
        mock_client.messages.create.side_effect = ConnectionError("down")

        result = research("what is the best laptop")

        self.assertIn("couldn't complete this research", result)

    @patch("agent.research_agent.build_fallback_chain")
    @patch("agent.research_agent.provider_health")
    @patch("agent.research_agent.anthropic_client")
    def test_a_failure_is_recorded_for_cooldown(self, mock_client, mock_health, mock_chain):
        mock_chain.return_value = [ModelChoice(provider="anthropic", model="claude-sonnet-5")]
        mock_client.messages.create.side_effect = ConnectionError("down")

        research("what is the best laptop")

        mock_health.record_failure.assert_called_with("anthropic")

    @patch("agent.research_agent.build_fallback_chain")
    @patch("agent.research_agent.provider_health")
    @patch("agent.research_agent.anthropic_client")
    def test_a_success_clears_any_prior_cooldown(self, mock_client, mock_health, mock_chain):
        mock_chain.return_value = [ModelChoice(provider="anthropic", model="claude-sonnet-5")]
        mock_client.messages.create.return_value = _claude_response("ok")

        research("what is the best laptop")

        mock_health.clear_failure.assert_called_with("anthropic")


class TestUsageAttribution(IsolatedUsageFileTestCase):

    @patch("agent.research_agent.build_fallback_chain")
    @patch("agent.research_agent.anthropic_client")
    def test_usage_is_recorded_with_research_agent_attribution(self, mock_client, mock_chain):
        mock_chain.return_value = [ModelChoice(provider="anthropic", model="claude-sonnet-5")]
        mock_client.messages.create.return_value = _claude_response("ok")

        research("what is the best laptop")

        records = usage.get_recent()
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0].agent, "research")
        self.assertEqual(records[0].fallback_position, 0)
        self.assertIsNotNone(records[0].task_type)

    @patch("agent.research_agent.build_fallback_chain")
    @patch("agent.research_agent.openai_client")
    @patch("agent.research_agent.anthropic_client")
    def test_fallback_position_reflects_which_candidate_actually_answered(self, mock_anthropic, mock_openai, mock_chain):
        mock_chain.return_value = [
            ModelChoice(provider="anthropic", model="claude-sonnet-5"),
            ModelChoice(provider="openai", model="gpt-5.6-terra"),
        ]
        mock_anthropic.messages.create.side_effect = ConnectionError("down")
        mock_openai.chat.completions.create.return_value = _openai_response("ok via fallback")

        research("what is the best laptop")

        records = usage.get_recent()
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0].provider, "openai")
        self.assertEqual(records[0].fallback_position, 1)


class TestRealRoutingIntegration(IsolatedUsageFileTestCase):
    """Unmocked build_fallback_chain -- proves research() actually calls
    the real M2 router (classify_task + build_fallback_chain), not just
    that it WOULD dispatch correctly if fed a fake chain."""

    @patch("agent.research_agent.anthropic_client")
    def test_a_short_question_gets_the_cost_priority_cheap_model(self, mock_client):
        # Short requests classify as REASONING_SIMPLE (cost_priority=True),
        # which routes anthropic to the cheap planner_model -- proves this
        # is genuinely the real, shared M2 classifier, not a fixed model.
        mock_client.messages.create.return_value = _claude_response("ok")
        from config.settings import settings

        research("best laptop?")

        _, kwargs = mock_client.messages.create.call_args
        self.assertEqual(kwargs["model"], settings.planner_model)

    @patch("agent.research_agent.anthropic_client")
    def test_missing_xai_and_perplexity_credentials_are_harmless(self, mock_client):
        # Regardless of whether XAI_API_KEY/PERPLEXITY_API_KEY happen to be
        # configured on the machine running this test, research() must not
        # raise -- build_fallback_chain already degrades unconfigured
        # optional providers to "filtered out", never an exception.
        mock_client.messages.create.return_value = _claude_response("ok")
        result = research("tell me something")
        self.assertEqual(result, "ok")


class TestNoRecursiveDelegation(IsolatedUsageFileTestCase):

    def test_research_module_never_imports_the_agent_manager_or_executor(self):
        # Structural guarantee, not just a behavioral one: this module
        # must not be ABLE to call back into agent.agents.manager or
        # agent.executor (either would risk a recursive agent tree /
        # violate MAX_AGENT_DEPTH). Confirmed by inspecting what's
        # actually bound in the module's namespace after import.
        import agent.research_agent as research_agent_module
        module_names = dir(research_agent_module)
        self.assertNotIn("execute_agent", module_names)
        self.assertNotIn("route_and_execute", module_names)
        self.assertNotIn("execute_task_stream", module_names)

    @patch("agent.research_agent.build_fallback_chain")
    @patch("agent.research_agent.anthropic_client")
    def test_max_agent_depth_constant_is_untouched_by_this_module(self, mock_client, mock_chain):
        from agent.agents.manager import MAX_AGENT_DEPTH
        mock_chain.return_value = [ModelChoice(provider="anthropic", model="claude-sonnet-5")]
        mock_client.messages.create.return_value = _claude_response("ok")

        research("anything")

        self.assertEqual(MAX_AGENT_DEPTH, 1)


if __name__ == "__main__":
    unittest.main()
