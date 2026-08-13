"""Tests for agent/model_router.py -- confirms the formalized interface
still produces exactly the existing Claude-first/OpenAI-fallback choice,
without making any real API calls.

Run with: python -m unittest tests.test_model_router -v
"""
import unittest

from agent import model_router
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


if __name__ == "__main__":
    unittest.main()
