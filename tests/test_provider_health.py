"""Tests for agent/provider_health.py -- configuration/initialization
checks only. No live API calls are made by this module, so none are
needed to test it either; get_secret is mocked so these results don't
depend on whatever keys happen to be configured on the machine running
the tests.

Run with: python -m unittest tests.test_provider_health -v
"""
import time
import unittest
from unittest.mock import patch

from agent import provider_health


class TestConfiguredChecks(unittest.TestCase):

    @patch("agent.provider_health.get_secret")
    def test_anthropic_configured_when_key_present(self, mock_get_secret):
        mock_get_secret.return_value = "fake-key-value"
        self.assertTrue(provider_health.anthropic_configured())

    @patch("agent.provider_health.get_secret")
    def test_anthropic_not_configured_when_key_missing(self, mock_get_secret):
        mock_get_secret.return_value = None
        self.assertFalse(provider_health.anthropic_configured())

    @patch("agent.provider_health.get_secret")
    def test_openai_configured_when_key_present(self, mock_get_secret):
        mock_get_secret.return_value = "fake-key-value"
        self.assertTrue(provider_health.openai_configured())

    @patch("agent.provider_health.get_secret")
    def test_openai_not_configured_when_key_missing(self, mock_get_secret):
        mock_get_secret.return_value = None
        self.assertFalse(provider_health.openai_configured())

    @patch("agent.provider_health.get_secret")
    def test_xai_configured_when_key_present(self, mock_get_secret):
        mock_get_secret.return_value = "fake-key-value"
        self.assertTrue(provider_health.xai_configured())

    @patch("agent.provider_health.get_secret")
    def test_xai_not_configured_when_key_missing(self, mock_get_secret):
        mock_get_secret.return_value = None
        self.assertFalse(provider_health.xai_configured())

    @patch("agent.provider_health.get_secret")
    def test_perplexity_configured_when_key_present(self, mock_get_secret):
        mock_get_secret.return_value = "fake-key-value"
        self.assertTrue(provider_health.perplexity_configured())

    @patch("agent.provider_health.get_secret")
    def test_perplexity_not_configured_when_key_missing(self, mock_get_secret):
        mock_get_secret.return_value = None
        self.assertFalse(provider_health.perplexity_configured())


class TestCheckProviders(unittest.TestCase):

    def test_returns_status_for_every_provider(self):
        status = provider_health.check_providers()
        for provider in ("anthropic", "openai", "xai", "perplexity"):
            self.assertIn(provider, status)
            self.assertIn("configured", status[provider])
            self.assertIn("initialized", status[provider])
            self.assertIsInstance(status[provider]["configured"], bool)
            self.assertIsInstance(status[provider]["initialized"], bool)

    def test_initialized_true_when_shared_clients_exist(self):
        # agent.chat's required clients are constructed at import time --
        # if that had failed, importing agent.chat would already have
        # raised, so reaching this assertion at all proves initialization
        # succeeded.
        status = provider_health.check_providers()
        self.assertTrue(status["anthropic"]["initialized"])
        self.assertTrue(status["openai"]["initialized"])

    def test_optional_providers_degrade_gracefully_when_unconfigured(self):
        # xai/perplexity are optional (Phase 9 Milestone 2) -- their
        # "initialized" status must reflect reality (None client = not
        # initialized) without ever raising, regardless of whether a key
        # happens to be configured on the machine running this test.
        status = provider_health.check_providers()
        self.assertEqual(status["xai"]["initialized"], status["xai"]["configured"])
        self.assertEqual(status["perplexity"]["initialized"], status["perplexity"]["configured"])

    def test_does_not_make_a_network_call(self):
        # No mocking of httpx/requests needed for this to pass quickly --
        # if check_providers() ever grows a real API call, this test's
        # runtime would balloon from milliseconds to a real round trip.
        # (agent.chat may need a real first-time import here, which is
        # itself slow -- see CHANGELOG.md's Phase 9 Milestone 2 entry --
        # so this imports it up front rather than timing that cost.)
        import agent.chat  # noqa: F401 -- warm the import before timing

        start = time.time()
        provider_health.check_providers()
        self.assertLess(time.time() - start, 0.5)


class TestFailureCooldown(unittest.TestCase):
    """agent.provider_health's in-memory, failure-derived cooldown --
    the router (agent/model_router.py) consults is_in_cooldown() to skip
    a provider that just failed, instead of a live health-check ping
    before every request."""

    def setUp(self):
        provider_health.clear_failure("anthropic")
        provider_health.clear_failure("openai")

    def tearDown(self):
        provider_health.clear_failure("anthropic")
        provider_health.clear_failure("openai")

    def test_not_in_cooldown_by_default(self):
        self.assertFalse(provider_health.is_in_cooldown("anthropic"))

    def test_in_cooldown_immediately_after_a_recorded_failure(self):
        provider_health.record_failure("anthropic")
        self.assertTrue(provider_health.is_in_cooldown("anthropic"))

    def test_clear_failure_ends_the_cooldown(self):
        provider_health.record_failure("anthropic")
        provider_health.clear_failure("anthropic")
        self.assertFalse(provider_health.is_in_cooldown("anthropic"))

    def test_cooldown_expires_after_the_configured_window(self):
        with patch("agent.provider_health.settings") as mock_settings:
            mock_settings.provider_failure_cooldown_seconds = 0.05
            provider_health.record_failure("openai")
            self.assertTrue(provider_health.is_in_cooldown("openai"))
            time.sleep(0.1)
            self.assertFalse(provider_health.is_in_cooldown("openai"))

    def test_cooldown_is_per_provider(self):
        provider_health.record_failure("anthropic")
        self.assertTrue(provider_health.is_in_cooldown("anthropic"))
        self.assertFalse(provider_health.is_in_cooldown("openai"))


if __name__ == "__main__":
    unittest.main()
