"""Tests for agent/model_router.py -- confirms the formalized interface
still produces exactly the existing Claude-first/OpenAI-fallback choice,
without making any real API calls. TestBuildFallbackChain (Phase 9
Milestone 2) covers the new task-aware routing entry point; it patches
agent.provider_health's configured-checks/cooldown and
agent.provider_budget's budget filter directly, so these tests are fully
deterministic regardless of what's actually configured on the machine
running them. Settings is a frozen dataclass -- dataclasses.replace()
(a new instance with one field overridden) is used wherever a test needs
a different setting, never a direct attribute assignment.

Run with: python -m unittest tests.test_model_router -v
"""
import dataclasses
import unittest
from unittest.mock import patch

from agent import model_router, provider_health
from agent.task_classifier import TaskRequirements, TaskType, classify
from config.settings import settings


class TestModelRouter(unittest.TestCase):

    def test_primary_choice_is_anthropic_default_model(self):
        choice = model_router.primary_choice()
        self.assertEqual(choice.provider, "anthropic")
        self.assertEqual(choice.model, settings.default_model)

    def test_fallback_choice_is_openai_fallback_model(self):
        choice = model_router.fallback_choice()
        self.assertEqual(choice.provider, "openai")
        self.assertEqual(choice.model, settings.fallback_model)

    def test_select_attempt_zero_is_primary(self):
        self.assertEqual(model_router.select(attempt=0), model_router.primary_choice())

    def test_select_attempt_one_is_fallback(self):
        self.assertEqual(model_router.select(attempt=1), model_router.fallback_choice())

    def test_select_default_attempt_is_primary(self):
        self.assertEqual(model_router.select(), model_router.primary_choice())

    def test_select_ignores_context_for_now(self):
        # Documents current (intentionally simple) behavior -- context is
        # accepted but doesn't change the outcome yet.
        self.assertEqual(
            model_router.select(attempt=0, context="anything"),
            model_router.select(attempt=0, context=None),
        )

    def test_model_choice_is_immutable(self):
        choice = model_router.primary_choice()
        with self.assertRaises(Exception):
            choice.model = "something-else"


class _IsolatedRoutingTestCase(unittest.TestCase):
    """Every candidate provider is treated as configured, healthy, and
    within budget by default -- individual tests narrow that down to
    exercise one filter at a time. Clears the (real, in-memory)
    provider_health cooldown state before/after so a prior test's
    record_failure() call can never leak into another. Doesn't touch
    settings.task_aware_routing_enabled at all -- the real default is
    already True, which is exactly what these tests need; only the one
    test that specifically covers the disabled case overrides it."""

    def setUp(self):
        for provider in ("anthropic", "openai", "xai", "perplexity"):
            provider_health.clear_failure(provider)

        self._configured_patch = patch.multiple(
            "agent.provider_health",
            anthropic_configured=lambda: True,
            openai_configured=lambda: True,
            xai_configured=lambda: True,
            perplexity_configured=lambda: True,
        )
        self._configured_patch.start()

        self._budget_patch = patch("agent.model_router.is_provider_within_budget", return_value=True)
        self._budget_patch.start()

    def tearDown(self):
        self._configured_patch.stop()
        self._budget_patch.stop()
        for provider in ("anthropic", "openai", "xai", "perplexity"):
            provider_health.clear_failure(provider)


class TestBuildFallbackChain(_IsolatedRoutingTestCase):

    def test_disabled_routing_returns_the_original_static_chain(self):
        # settings is a frozen dataclass -- a new instance with just this
        # one field flipped, not a direct attribute assignment.
        disabled_settings = dataclasses.replace(settings, task_aware_routing_enabled=False)
        with patch("agent.model_router.settings", disabled_settings):
            chain = model_router.build_fallback_chain(classify("write me some code"))
        self.assertEqual(chain, [model_router.primary_choice(), model_router.fallback_choice()])

    def test_coding_prefers_anthropic_then_xai_then_openai(self):
        # perplexity isn't in CODING's preference list, but every
        # configured, capability-eligible provider is still reachable as
        # a last resort -- appended, never prepended ahead of the task's
        # actual preferences.
        chain = model_router.build_fallback_chain(classify("fix this bug in my python function"))
        self.assertEqual([c.provider for c in chain], ["anthropic", "xai", "openai", "perplexity"])

    def test_current_research_prefers_perplexity_first(self):
        chain = model_router.build_fallback_chain(classify("what's the latest news today"))
        self.assertEqual(chain[0].provider, "perplexity")

    def test_simple_task_selects_the_cheap_anthropic_model(self):
        chain = model_router.build_fallback_chain(classify("hi"))
        self.assertEqual(chain[0].provider, "anthropic")
        self.assertEqual(chain[0].model, settings.planner_model)

    def test_non_simple_task_selects_the_default_anthropic_model(self):
        chain = model_router.build_fallback_chain(classify("write me some code"))
        self.assertEqual(chain[0].model, settings.default_model)

    def test_quality_priority_task_selects_the_quality_openai_and_xai_models(self):
        # "write me some code" -> CODING -> quality_priority=True, and both
        # xai/openai are in CODING's preference list -- neither should pay
        # for the flagship OpenAI/xAI tier unless quality was actually
        # requested, and here it is.
        chain = model_router.build_fallback_chain(classify("write me some code"))
        by_provider = {c.provider: c.model for c in chain}
        self.assertEqual(by_provider["openai"], settings.openai_quality_model)
        self.assertEqual(by_provider["xai"], settings.xai_quality_model)

    def test_cost_priority_task_selects_the_economy_openai_and_xai_models(self):
        # "hi" -> REASONING_SIMPLE -> cost_priority=True, not quality_priority.
        chain = model_router.build_fallback_chain(classify("hi"))
        by_provider = {c.provider: c.model for c in chain}
        self.assertEqual(by_provider["openai"], settings.openai_economy_model)
        self.assertEqual(by_provider["xai"], settings.xai_economy_model)

    def test_neither_priority_task_selects_the_balanced_openai_model(self):
        # An ordinary GENERAL_CHAT request is neither cost- nor quality-
        # priority -- openai's candidate should be the same "balanced"
        # fallback_model the original static fallback_choice() always used.
        chain = model_router.build_fallback_chain(classify("tell me a little bit about how your day has been going"))
        by_provider = {c.provider: c.model for c in chain}
        self.assertEqual(by_provider["openai"], settings.fallback_model)

    def test_unconfigured_provider_is_removed_from_the_chain(self):
        with patch("agent.provider_health.perplexity_configured", return_value=False):
            chain = model_router.build_fallback_chain(classify("what's the latest news today"))
        self.assertNotIn("perplexity", [c.provider for c in chain])

    def test_provider_in_cooldown_is_removed_from_the_chain(self):
        provider_health.record_failure("anthropic")
        chain = model_router.build_fallback_chain(classify("write me some code"))
        self.assertNotIn("anthropic", [c.provider for c in chain])

    def test_over_budget_provider_is_removed_from_the_chain(self):
        with patch(
            "agent.model_router.is_provider_within_budget",
            side_effect=lambda provider: provider != "anthropic",
        ):
            chain = model_router.build_fallback_chain(classify("write me some code"))
        self.assertNotIn("anthropic", [c.provider for c in chain])

    def test_vision_task_filters_out_providers_without_vision_support(self):
        requirements = TaskRequirements(task_type=TaskType.VISION, needs_vision=True)
        chain = model_router.build_fallback_chain(requirements)
        providers = [c.provider for c in chain]
        self.assertNotIn("xai", providers)  # no vision support in the capability table
        self.assertNotIn("perplexity", providers)
        self.assertIn("openai", providers)

    def test_current_web_task_filters_out_providers_without_web_grounding(self):
        requirements = TaskRequirements(task_type=TaskType.RESEARCH_CURRENT, needs_current_web=True)
        chain = model_router.build_fallback_chain(requirements)
        # Only perplexity actually supports web grounding in the
        # capability table -- everything else must be filtered out even
        # though the task-preference table lists them for this task type.
        self.assertEqual([c.provider for c in chain], ["perplexity"])

    def test_never_returns_an_empty_chain(self):
        with patch.multiple(
            "agent.provider_health",
            anthropic_configured=lambda: False,
            openai_configured=lambda: False,
            xai_configured=lambda: False,
            perplexity_configured=lambda: False,
        ):
            chain = model_router.build_fallback_chain(classify("write me some code"))
        self.assertTrue(chain)
        self.assertEqual(chain, [model_router.primary_choice(), model_router.fallback_choice()])

    def test_general_chat_with_only_anthropic_and_openai_configured_matches_original_order(self):
        # The exact backward-compatibility guarantee: with only the two
        # originally-required providers configured, the new router must
        # reproduce the old static chain's order for an ordinary request.
        with patch.multiple(
            "agent.provider_health",
            anthropic_configured=lambda: True,
            openai_configured=lambda: True,
            xai_configured=lambda: False,
            perplexity_configured=lambda: False,
        ):
            chain = model_router.build_fallback_chain(classify("tell me a little bit about how your day has been going"))
        self.assertEqual([c.provider for c in chain], ["anthropic", "openai"])


if __name__ == "__main__":
    unittest.main()
