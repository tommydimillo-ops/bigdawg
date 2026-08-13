"""Tests for agent/provider_health.py -- configuration/initialization
checks only. No live API calls are made by this module, so none are
needed to test it either; get_secret is mocked so these results don't
depend on whatever keys happen to be configured on the machine running
the tests.

Run with: python -m unittest tests.test_provider_health -v
"""
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


class TestCheckProviders(unittest.TestCase):

    def test_returns_status_for_both_providers(self):
        status = provider_health.check_providers()
        self.assertIn("anthropic", status)
        self.assertIn("openai", status)
        for provider in ("anthropic", "openai"):
            self.assertIn("configured", status[provider])
            self.assertIn("initialized", status[provider])
            self.assertIsInstance(status[provider]["configured"], bool)
            self.assertIsInstance(status[provider]["initialized"], bool)

    def test_initialized_true_when_shared_clients_exist(self):
        # agent.chat's clients are constructed at import time -- if that
        # had failed, importing agent.chat would already have raised, so
        # reaching this assertion at all proves initialization succeeded.
        status = provider_health.check_providers()
        self.assertTrue(status["anthropic"]["initialized"])
        self.assertTrue(status["openai"]["initialized"])

    def test_does_not_make_a_network_call(self):
        # No mocking of httpx/requests needed for this to pass quickly --
        # if check_providers() ever grows a real API call, this test's
        # runtime would balloon from milliseconds to a real round trip.
        import time
        start = time.time()
        provider_health.check_providers()
        self.assertLess(time.time() - start, 0.5)


if __name__ == "__main__":
    unittest.main()
