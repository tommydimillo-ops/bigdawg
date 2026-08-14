"""Tests for agent/cowork_gateway.py -- a status-only stub, honestly
reporting that there is no documented, programmatic Cowork API to
integrate against, rather than faking one.

Run with: python -m unittest tests.test_cowork_gateway -v
"""
import unittest

import agent.cowork_gateway as cowork_gateway
from agent.cowork_gateway import CoworkStatus


class TestCoworkStatus(unittest.TestCase):

    def test_status_is_unavailable(self):
        self.assertEqual(cowork_gateway.status(), CoworkStatus.UNAVAILABLE)

    def test_is_available_is_false(self):
        self.assertFalse(cowork_gateway.is_available())

    def test_describe_status_is_a_non_empty_honest_message(self):
        message = cowork_gateway.describe_status()
        self.assertTrue(message)
        self.assertIn("unavailable", message.lower())

    def test_status_enum_has_exactly_the_three_documented_states(self):
        self.assertEqual(
            {s.value for s in CoworkStatus},
            {"available", "unavailable", "not_configured"},
        )


if __name__ == "__main__":
    unittest.main()
