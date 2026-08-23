"""Integration tests for Phase 9 Milestone 2's generalized N-provider
fallback loop in agent/executor.py's execute_task_stream -- the
replacement for the original hardcoded two-tier Claude-then-OpenAI
cascade. Exercised through the real execute_task_stream loop with only
the network calls themselves mocked (claude_client's streaming API,
and each OpenAI-compatible client's chat.completions.create), matching
this project's established policy (see tests/test_executor_phase5_
integration.py). agent.executor.build_fallback_chain is patched directly
so these tests control exactly which candidates are in play, independent
of agent/model_router.py's own classification/ranking logic (covered
separately in tests/test_model_router.py and tests/test_task_classifier.py).

Run with: python -m unittest tests.test_executor_multi_provider_fallback -v
"""
import os
import tempfile
import unittest
from unittest.mock import MagicMock, patch

import tools.schemas  # noqa: F401 -- populates the registry
import agent.execution_history as execution_history
import agent.history_store as history_store
import agent.jarvis_state as jarvis_state
import agent.usage as usage
from agent import provider_health
from agent.executor import execute_task_stream
from agent.model_router import ModelChoice


class _MockStream:
    """Stands in for the object claude_client.messages.stream(...)
    returns -- a context manager exposing .text_stream (an iterator) and
    .get_final_message()."""

    def __init__(self, chunks, final_message):
        self._chunks = chunks
        self._final_message = final_message

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        return False

    @property
    def text_stream(self):
        return iter(self._chunks)

    def get_final_message(self):
        return self._final_message


def _claude_success(text="Hi there!"):
    return _MockStream([text], MagicMock(stop_reason="end_turn"))


def _openai_style_success(text="Hi from fallback"):
    response = MagicMock()
    response.choices = [MagicMock(message=MagicMock(tool_calls=None, content=text))]
    response.usage = MagicMock(prompt_tokens=10, completion_tokens=5)
    return response


def _perplexity_agent_success(text="From Perplexity"):
    # Stands in for _call_perplexity_agent's return value -- the Agent
    # API's own output[]/usage shape (input/output, not messages/choices;
    # see agent/executor.py's _extract_agent_api_text), not the OpenAI-
    # style response _openai_style_success above mimics.
    return {
        "output": [{"type": "message", "content": [{"type": "output_text", "text": text}]}],
        "usage": {"input_tokens": 10, "output_tokens": 5},
    }


class IsolatedExecutorTestCase(unittest.TestCase):
    """Same isolation as tests/test_executor_phase5_integration.py, plus
    clearing agent.provider_health's in-memory failure-cooldown state --
    real, module-global, in-process state a prior test's simulated
    provider failure could otherwise leak into this one."""

    def setUp(self):
        self._real_history_file = execution_history.HISTORY_FILE
        self._real_state_file = jarvis_state.STATE_FILE
        self._real_usage_file = usage.USAGE_FILE
        self._real_history_db = history_store.HISTORY_DB
        execution_history.HISTORY_FILE = tempfile.mktemp(suffix=".json")
        jarvis_state.STATE_FILE = tempfile.mktemp(suffix=".json")
        usage.USAGE_FILE = tempfile.mktemp(suffix=".json")
        history_store.HISTORY_DB = tempfile.mktemp(suffix=".db")
        for provider in ("anthropic", "openai", "xai", "perplexity"):
            provider_health.clear_failure(provider)

    def tearDown(self):
        for path in (
            execution_history.HISTORY_FILE, f"{execution_history.HISTORY_FILE}.tmp",
            jarvis_state.STATE_FILE, f"{jarvis_state.STATE_FILE}.tmp",
            usage.USAGE_FILE, f"{usage.USAGE_FILE}.lock",
            history_store.HISTORY_DB, f"{history_store.HISTORY_DB}-wal", f"{history_store.HISTORY_DB}-shm",
        ):
            if os.path.exists(path):
                os.remove(path)
        execution_history.HISTORY_FILE = self._real_history_file
        jarvis_state.STATE_FILE = self._real_state_file
        usage.USAGE_FILE = self._real_usage_file
        history_store.HISTORY_DB = self._real_history_db
        for provider in ("anthropic", "openai", "xai", "perplexity"):
            provider_health.clear_failure(provider)


class TestPrimarySucceeds(IsolatedExecutorTestCase):

    @patch("agent.executor.build_fallback_chain")
    @patch("agent.executor.claude_client")
    def test_fallback_is_never_called_when_primary_succeeds(self, mock_claude, mock_chain):
        mock_chain.return_value = [
            ModelChoice(provider="anthropic", model="claude-sonnet-5"),
            ModelChoice(provider="openai", model="gpt-5.6-terra"),
        ]
        mock_claude.messages.stream.return_value = _claude_success()

        with patch("agent.executor.openai_client") as mock_openai:
            result = "".join(execute_task_stream("say hi"))
            mock_openai.chat.completions.create.assert_not_called()

        self.assertEqual(result, "Hi there!")


class TestFallsToNextCandidate(IsolatedExecutorTestCase):

    @patch("agent.executor.build_fallback_chain")
    @patch("agent.executor.openai_client")
    @patch("agent.executor.claude_client")
    def test_primary_failure_falls_to_the_next_candidate(self, mock_claude, mock_openai, mock_chain):
        mock_chain.return_value = [
            ModelChoice(provider="anthropic", model="claude-sonnet-5"),
            ModelChoice(provider="openai", model="gpt-5.6-terra"),
        ]
        mock_claude.messages.stream.side_effect = RuntimeError("anthropic is down")
        mock_openai.chat.completions.create.return_value = _openai_style_success()

        result = "".join(execute_task_stream("say hi"))

        self.assertEqual(result, "Hi from fallback")
        mock_openai.chat.completions.create.assert_called_once()

    @patch("agent.executor.build_fallback_chain")
    @patch("agent.executor._call_perplexity_agent")
    @patch("agent.executor.openai_client")
    @patch("agent.executor.claude_client")
    def test_two_failures_fall_through_to_the_third_candidate(self, mock_claude, mock_openai, mock_call_perplexity, mock_chain):
        mock_chain.return_value = [
            ModelChoice(provider="anthropic", model="claude-sonnet-5"),
            ModelChoice(provider="openai", model="gpt-5.6-terra"),
            ModelChoice(provider="perplexity", model="low"),
        ]
        mock_claude.messages.stream.side_effect = RuntimeError("anthropic is down")
        mock_openai.chat.completions.create.side_effect = RuntimeError("openai is down too")
        mock_call_perplexity.return_value = _perplexity_agent_success("From Perplexity")

        result = "".join(execute_task_stream("what's the latest news"))

        self.assertEqual(result, "From Perplexity")
        mock_call_perplexity.assert_called_once()

    @patch("agent.executor.build_fallback_chain")
    @patch("agent.executor.xai_client")
    @patch("agent.executor.claude_client")
    def test_falls_to_xai_when_it_is_the_next_ranked_candidate(self, mock_claude, mock_xai, mock_chain):
        mock_chain.return_value = [
            ModelChoice(provider="anthropic", model="claude-sonnet-5"),
            ModelChoice(provider="xai", model="grok-4.3"),
        ]
        mock_claude.messages.stream.side_effect = RuntimeError("anthropic is down")
        mock_xai.chat.completions.create.return_value = _openai_style_success("From Grok")

        result = "".join(execute_task_stream("say hi"))

        self.assertEqual(result, "From Grok")


class TestAllProvidersFail(IsolatedExecutorTestCase):

    @patch("agent.executor.build_fallback_chain")
    @patch("agent.executor.openai_client")
    @patch("agent.executor.claude_client")
    def test_clean_failure_message_when_every_candidate_fails(self, mock_claude, mock_openai, mock_chain):
        mock_chain.return_value = [
            ModelChoice(provider="anthropic", model="claude-sonnet-5"),
            ModelChoice(provider="openai", model="gpt-5.6-terra"),
        ]
        mock_claude.messages.stream.side_effect = RuntimeError("anthropic is down")
        mock_openai.chat.completions.create.side_effect = RuntimeError("openai is down too")

        result = "".join(execute_task_stream("say hi"))

        self.assertIn("Agent error", result)

    @patch("agent.executor.build_fallback_chain")
    @patch("agent.executor.claude_client")
    def test_single_candidate_chain_fails_cleanly_too(self, mock_claude, mock_chain):
        mock_chain.return_value = [ModelChoice(provider="anthropic", model="claude-sonnet-5")]
        mock_claude.messages.stream.side_effect = RuntimeError("anthropic is down")

        result = "".join(execute_task_stream("say hi"))

        self.assertIn("Agent error", result)

    @patch("agent.executor.build_fallback_chain")
    @patch("agent.executor._call_perplexity_agent")
    def test_single_perplexity_candidate_fails_cleanly(self, mock_call_perplexity, mock_chain):
        mock_chain.return_value = [ModelChoice(provider="perplexity", model="low")]
        mock_call_perplexity.side_effect = RuntimeError("perplexity is down")

        result = "".join(execute_task_stream("what's the latest news"))

        self.assertIn("Agent error", result)


class TestPerplexityAgentAPI(IsolatedExecutorTestCase):
    """Unit-level coverage for agent/executor.py's own narrow Perplexity
    Agent API call path (_call_perplexity_agent/_extract_agent_api_text)
    -- the one provider that can't share _run_openai_compatible_loop
    because its request/response shape genuinely differs (input/output,
    not messages/choices). Mocked at httpx.post itself, the actual
    external-call boundary for this path."""

    @patch("agent.executor.httpx.post")
    def test_posts_to_the_documented_agent_api_endpoint_with_preset_and_input(self, mock_post):
        from agent import executor

        mock_post.return_value = MagicMock(json=lambda: _perplexity_agent_success())
        executor._call_perplexity_agent("low", "what's new today", "be concise")

        args, kwargs = mock_post.call_args
        self.assertEqual(args[0], "https://api.perplexity.ai/v1/agent")
        self.assertEqual(kwargs["json"]["preset"], "low")
        self.assertEqual(kwargs["json"]["input"], "what's new today")
        self.assertEqual(kwargs["json"]["instructions"], "be concise")
        self.assertIn("Bearer", kwargs["headers"]["Authorization"])

    @patch("agent.executor.httpx.post")
    def test_raises_when_the_response_is_an_error_status(self, mock_post):
        from agent import executor
        import httpx

        mock_response = MagicMock()
        mock_response.raise_for_status.side_effect = httpx.HTTPStatusError(
            "bad request", request=MagicMock(), response=MagicMock(status_code=400),
        )
        mock_post.return_value = mock_response

        with self.assertRaises(httpx.HTTPStatusError):
            executor._call_perplexity_agent("low", "test", None)

    def test_extract_text_finds_the_message_item(self):
        from agent import executor

        data = _perplexity_agent_success("the grounded answer")
        self.assertEqual(executor._extract_agent_api_text(data), "the grounded answer")

    def test_extract_text_returns_empty_string_when_no_message_item(self):
        from agent import executor

        self.assertEqual(executor._extract_agent_api_text({"output": [{"type": "search_results"}]}), "")
        self.assertEqual(executor._extract_agent_api_text({}), "")


class TestProviderHealthIntegration(IsolatedExecutorTestCase):

    @patch("agent.executor.build_fallback_chain")
    @patch("agent.executor.openai_client")
    @patch("agent.executor.claude_client")
    def test_a_failed_provider_is_recorded_for_cooldown(self, mock_claude, mock_openai, mock_chain):
        mock_chain.return_value = [
            ModelChoice(provider="anthropic", model="claude-sonnet-5"),
            ModelChoice(provider="openai", model="gpt-5.6-terra"),
        ]
        mock_claude.messages.stream.side_effect = RuntimeError("anthropic is down")
        mock_openai.chat.completions.create.return_value = _openai_style_success()

        "".join(execute_task_stream("say hi"))

        self.assertTrue(provider_health.is_in_cooldown("anthropic"))
        self.assertFalse(provider_health.is_in_cooldown("openai"))

    @patch("agent.executor.build_fallback_chain")
    @patch("agent.executor.claude_client")
    def test_a_successful_call_clears_any_prior_cooldown(self, mock_claude, mock_chain):
        provider_health.record_failure("anthropic")
        mock_chain.return_value = [ModelChoice(provider="anthropic", model="claude-sonnet-5")]
        mock_claude.messages.stream.return_value = _claude_success()

        "".join(execute_task_stream("say hi"))

        self.assertFalse(provider_health.is_in_cooldown("anthropic"))

    @patch("agent.executor.build_fallback_chain")
    @patch("agent.executor._call_perplexity_agent")
    def test_a_perplexity_failure_is_recorded_for_cooldown(self, mock_call_perplexity, mock_chain):
        mock_chain.return_value = [ModelChoice(provider="perplexity", model="low")]
        mock_call_perplexity.side_effect = RuntimeError("perplexity is down")

        "".join(execute_task_stream("what's the latest news"))

        self.assertTrue(provider_health.is_in_cooldown("perplexity"))

    @patch("agent.executor.build_fallback_chain")
    @patch("agent.executor._call_perplexity_agent")
    def test_a_successful_perplexity_call_clears_any_prior_cooldown(self, mock_call_perplexity, mock_chain):
        provider_health.record_failure("perplexity")
        mock_chain.return_value = [ModelChoice(provider="perplexity", model="low")]
        mock_call_perplexity.return_value = _perplexity_agent_success()

        "".join(execute_task_stream("what's the latest news"))

        self.assertFalse(provider_health.is_in_cooldown("perplexity"))


class TestObservability(IsolatedExecutorTestCase):

    @patch("agent.executor.log_event")
    @patch("agent.executor.build_fallback_chain")
    @patch("agent.executor.claude_client")
    def test_route_selection_is_logged(self, mock_claude, mock_chain, mock_log_event):
        mock_chain.return_value = [ModelChoice(provider="anthropic", model="claude-sonnet-5")]
        mock_claude.messages.stream.return_value = _claude_success()

        "".join(execute_task_stream("say hi"))

        events = [call.args[0] for call in mock_log_event.call_args_list]
        self.assertIn("model_route_selected", events)

    @patch("agent.executor.log_event")
    @patch("agent.executor.build_fallback_chain")
    @patch("agent.executor.openai_client")
    @patch("agent.executor.claude_client")
    def test_fallback_start_and_success_are_both_logged(self, mock_claude, mock_openai, mock_chain, mock_log_event):
        mock_chain.return_value = [
            ModelChoice(provider="anthropic", model="claude-sonnet-5"),
            ModelChoice(provider="openai", model="gpt-5.6-terra"),
        ]
        mock_claude.messages.stream.side_effect = RuntimeError("anthropic is down")
        mock_openai.chat.completions.create.return_value = _openai_style_success()

        "".join(execute_task_stream("say hi"))

        events = [call.args[0] for call in mock_log_event.call_args_list]
        self.assertIn("model_fallback_started", events)
        self.assertIn("model_fallback_succeeded", events)

    @patch("agent.executor.log_event")
    @patch("agent.executor.build_fallback_chain")
    @patch("agent.executor.claude_client")
    def test_no_secret_value_ever_appears_in_a_logged_event(self, mock_claude, mock_chain, mock_log_event):
        mock_chain.return_value = [ModelChoice(provider="anthropic", model="claude-sonnet-5")]
        mock_claude.messages.stream.return_value = _claude_success()

        "".join(execute_task_stream("say hi"))

        for call in mock_log_event.call_args_list:
            serialized = repr(call)
            self.assertNotIn("sk-ant-", serialized)
            self.assertNotIn("sk-proj-", serialized)


class TestExistingBehaviorUnaffectedWhenNoRouterMock(IsolatedExecutorTestCase):
    """No build_fallback_chain mock here -- the REAL router runs, exactly
    as it would in production with only Anthropic/OpenAI configured
    (this test environment's actual state). Proves end to end that the
    generalized loop reproduces the original Claude-then-OpenAI behavior
    without needing to know anything about the router's internals."""

    @patch("agent.executor.claude_client")
    def test_ordinary_request_still_succeeds_via_claude(self, mock_claude):
        mock_claude.messages.stream.return_value = _claude_success("Still works")
        result = "".join(execute_task_stream("say hi"))
        self.assertEqual(result, "Still works")

    @patch("agent.executor.openai_client")
    @patch("agent.executor.claude_client")
    def test_ordinary_request_falls_back_to_openai_on_claude_failure(self, mock_claude, mock_openai):
        mock_claude.messages.stream.side_effect = RuntimeError("down")
        mock_openai.chat.completions.create.return_value = _openai_style_success("Fallback still works")
        result = "".join(execute_task_stream("say hi"))
        self.assertEqual(result, "Fallback still works")


if __name__ == "__main__":
    unittest.main()
