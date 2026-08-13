"""Tests for agent/request_context.py.

Run with: python -m unittest tests.test_request_context -v
"""
import unittest

from agent.request_context import RequestContext


class TestRequestContext(unittest.TestCase):

    def test_create_sets_user_input_and_source(self):
        ctx = RequestContext.create("what's the weather", source="chat")
        self.assertEqual(ctx.user_input, "what's the weather")
        self.assertEqual(ctx.source, "chat")

    def test_default_source_is_chat(self):
        ctx = RequestContext.create("hi")
        self.assertEqual(ctx.source, "chat")

    def test_each_context_gets_a_unique_request_id(self):
        ids = {RequestContext.create("x").request_id for _ in range(50)}
        self.assertEqual(len(ids), 50)

    def test_request_id_is_a_nonempty_string(self):
        ctx = RequestContext.create("x")
        self.assertIsInstance(ctx.request_id, str)
        self.assertTrue(ctx.request_id)

    def test_timestamp_is_set(self):
        ctx = RequestContext.create("x")
        self.assertIsInstance(ctx.timestamp, float)
        self.assertGreater(ctx.timestamp, 0)

    def test_autonomy_level_defaults_from_settings(self):
        from config.settings import settings
        ctx = RequestContext.create("x")
        self.assertEqual(ctx.autonomy_level, settings.autonomy_level)


if __name__ == "__main__":
    unittest.main()
