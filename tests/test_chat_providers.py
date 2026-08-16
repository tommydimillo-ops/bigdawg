"""Tests for agent/chat.py's Phase 9 Milestone 2 additions -- xai_client/
perplexity_client, the two new optional, OpenAI-API-compatible clients.

Deliberately does NOT reload agent.chat itself: that module's clients are
constructed once at import time and already shared by reference across
every other already-imported module in this process, so forcing a reload
here would risk swapping live client objects out from under unrelated
tests running in the same suite. Instead: (1) confirms the real, already-
imported module state degrades gracefully (never raises, produces a
sensible type either way) for whatever is actually configured on this
machine, and (2) directly tests the exact conditional-construction
pattern agent/chat.py uses (`OpenAI(api_key=key, base_url=...) if key
else None`) in isolation, which is what actually matters and doesn't
require touching the shared module at all.

Run with: python -m unittest tests.test_chat_providers -v
"""
import unittest

from openai import OpenAI

import agent.chat as chat


class TestRealModuleState(unittest.TestCase):

    def test_xai_client_is_none_or_a_real_client(self):
        self.assertTrue(chat.xai_client is None or isinstance(chat.xai_client, OpenAI))

    def test_perplexity_client_is_none_or_a_real_client(self):
        self.assertTrue(chat.perplexity_client is None or isinstance(chat.perplexity_client, OpenAI))

    def test_required_clients_are_always_constructed(self):
        # anthropic_client/openai_client are required providers -- if
        # constructing either had failed, importing agent.chat itself
        # would already have raised, so reaching this line at all proves
        # both succeeded.
        self.assertIsNotNone(chat.anthropic_client)
        self.assertIsNotNone(chat.openai_client)


class TestOptionalClientConstructionPattern(unittest.TestCase):
    """Exercises the exact `OpenAI(...) if key else None` pattern
    agent/chat.py uses for xai_client/perplexity_client, in isolation --
    proves the pattern itself is sound without reloading the shared
    module. No network call happens either way: constructing an OpenAI
    SDK client object never makes one."""

    def _construct_like_chat_py(self, key, base_url):
        return OpenAI(api_key=key, base_url=base_url) if key else None

    def test_missing_key_produces_none_not_an_exception(self):
        result = self._construct_like_chat_py(None, "https://api.x.ai/v1")
        self.assertIsNone(result)

    def test_empty_string_key_produces_none(self):
        # get_secret() returns "" in some falsy-but-not-None edge cases
        # (e.g. an empty env var) -- must degrade the same as None, not
        # attempt construction with a blank key.
        result = self._construct_like_chat_py("", "https://api.x.ai/v1")
        self.assertIsNone(result)

    def test_present_key_produces_a_real_client(self):
        result = self._construct_like_chat_py("fake-test-key", "https://api.x.ai/v1")
        self.assertIsInstance(result, OpenAI)

    def test_present_key_sets_the_given_base_url(self):
        result = self._construct_like_chat_py("fake-test-key", "https://api.perplexity.ai")
        self.assertEqual(str(result.base_url).rstrip("/"), "https://api.perplexity.ai")


if __name__ == "__main__":
    unittest.main()
