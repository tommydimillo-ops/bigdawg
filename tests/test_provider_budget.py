"""Tests for agent/provider_budget.py -- Phase 9 Milestone 2's minimal
budget-control foundation. Isolates agent.usage.USAGE_FILE to a temp path
per test (the same file-backed-store isolation every other test touching
usage.py already uses), and patches config.settings.settings for the
budget thresholds themselves -- neither ever touches the real
usage_history.json or real configuration.

Run with: python -m unittest tests.test_provider_budget -v
"""
import os
import tempfile
import unittest
from unittest.mock import patch

import agent.usage as usage
from agent import provider_budget
from agent.usage import record_llm_usage


class _IsolatedUsageFile(unittest.TestCase):

    def setUp(self):
        self._real_usage_file = usage.USAGE_FILE
        usage.USAGE_FILE = tempfile.mktemp(suffix=".json")

    def tearDown(self):
        for path in (usage.USAGE_FILE, f"{usage.USAGE_FILE}.lock"):
            if os.path.exists(path):
                os.remove(path)
        usage.USAGE_FILE = self._real_usage_file


class TestProviderSpendToday(_IsolatedUsageFile):

    def test_zero_when_nothing_recorded(self):
        self.assertEqual(provider_budget.provider_spend_today("anthropic"), 0.0)

    def test_sums_only_the_matching_provider(self):
        record_llm_usage(provider="anthropic", model="claude-sonnet-5", operation="chat", input_tokens=1_000_000)
        record_llm_usage(provider="openai", model="gpt-5", operation="fallback", input_tokens=1_000_000)
        anthropic_spend = provider_budget.provider_spend_today("anthropic")
        openai_spend = provider_budget.provider_spend_today("openai")
        self.assertGreater(anthropic_spend, 0)
        self.assertGreater(openai_spend, 0)
        self.assertNotEqual(anthropic_spend, openai_spend)  # different per-token rates

    def test_does_not_raise_on_corrupt_usage_file(self):
        os.makedirs(os.path.dirname(usage.USAGE_FILE), exist_ok=True)
        with open(usage.USAGE_FILE, "w") as f:
            f.write("{not valid json")
        self.assertEqual(provider_budget.provider_spend_today("anthropic"), 0.0)


class TestBudgetStatus(_IsolatedUsageFile):

    @patch("agent.provider_budget.settings")
    def test_no_limit_configured_means_never_over(self, mock_settings):
        mock_settings.provider_daily_budget_usd = None
        mock_settings.budget_warning_threshold = 0.8
        status = provider_budget.provider_budget_status("anthropic")
        self.assertFalse(status.over_limit)
        self.assertFalse(status.at_warning)

    @patch("agent.provider_budget.settings")
    def test_over_limit_when_spend_meets_the_ceiling(self, mock_settings):
        mock_settings.provider_daily_budget_usd = 0.001
        mock_settings.budget_warning_threshold = 0.8
        record_llm_usage(provider="anthropic", model="claude-sonnet-5", operation="chat", input_tokens=1_000_000)
        status = provider_budget.provider_budget_status("anthropic")
        self.assertTrue(status.over_limit)

    @patch("agent.provider_budget.settings")
    def test_at_warning_before_over_limit(self, mock_settings):
        mock_settings.provider_daily_budget_usd = 10.0
        mock_settings.budget_warning_threshold = 0.5
        # claude-sonnet-5: $2/M input -- 3M input tokens = $6, which is
        # 60% of a $10 ceiling: past the 50% warning line, not yet over.
        record_llm_usage(provider="anthropic", model="claude-sonnet-5", operation="chat", input_tokens=3_000_000)
        status = provider_budget.provider_budget_status("anthropic")
        self.assertTrue(status.at_warning)
        self.assertFalse(status.over_limit)

    @patch("agent.provider_budget.settings")
    def test_global_status_no_limit_configured(self, mock_settings):
        mock_settings.daily_budget_usd = None
        mock_settings.budget_warning_threshold = 0.8
        status = provider_budget.global_budget_status()
        self.assertFalse(status.over_limit)


class TestIsProviderWithinBudget(_IsolatedUsageFile):

    @patch("agent.provider_budget.settings")
    def test_within_budget_when_nothing_configured(self, mock_settings):
        mock_settings.daily_budget_usd = None
        mock_settings.provider_daily_budget_usd = None
        mock_settings.budget_warning_threshold = 0.8
        self.assertTrue(provider_budget.is_provider_within_budget("anthropic"))

    @patch("agent.provider_budget.settings")
    def test_excluded_when_its_own_provider_ceiling_is_exceeded(self, mock_settings):
        mock_settings.daily_budget_usd = None
        mock_settings.provider_daily_budget_usd = 0.001
        mock_settings.budget_warning_threshold = 0.8
        record_llm_usage(provider="anthropic", model="claude-sonnet-5", operation="chat", input_tokens=1_000_000)
        self.assertFalse(provider_budget.is_provider_within_budget("anthropic"))

    @patch("agent.provider_budget.settings")
    def test_excluded_when_global_ceiling_is_exceeded_even_if_this_provider_spent_nothing(self, mock_settings):
        mock_settings.daily_budget_usd = 0.001
        mock_settings.provider_daily_budget_usd = None
        mock_settings.budget_warning_threshold = 0.8
        record_llm_usage(provider="anthropic", model="claude-sonnet-5", operation="chat", input_tokens=1_000_000)
        # openai spent nothing itself, but the GLOBAL ceiling is blown --
        # budget policy must not let routing hunt for an unspent provider
        # once the whole-account ceiling is hit.
        self.assertFalse(provider_budget.is_provider_within_budget("openai"))

    @patch("agent.provider_budget.settings")
    def test_a_provider_that_never_spent_is_not_excluded_by_its_own_ceiling(self, mock_settings):
        mock_settings.daily_budget_usd = None
        mock_settings.provider_daily_budget_usd = 0.001
        mock_settings.budget_warning_threshold = 0.8
        record_llm_usage(provider="anthropic", model="claude-sonnet-5", operation="chat", input_tokens=1_000_000)
        # anthropic blew its own ceiling, but openai -- untouched -- has
        # not, and must remain a usable candidate.
        self.assertTrue(provider_budget.is_provider_within_budget("openai"))


if __name__ == "__main__":
    unittest.main()
