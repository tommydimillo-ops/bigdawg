"""Tests for agent/canonical_suite.py -- the one place the actual
canonical-suite command is built, so agent/agents/qa.py's and
agent/agents/coding.py's own copies can no longer diverge on the
load-bearing `-t .` flag the way qa.py's real, pre-existing copy once
did (found by code review, fixed alongside this module's creation).

Run with: python -m unittest tests.test_canonical_suite -v
"""
import sys
import unittest

from agent.canonical_suite import canonical_suite_command


class TestCanonicalSuiteCommand(unittest.TestCase):
    def test_includes_the_load_bearing_t_flag(self):
        command = canonical_suite_command()
        self.assertIn("-t", command)
        self.assertEqual(command[command.index("-t") + 1], ".")

    def test_uses_the_real_interpreter_and_discover(self):
        command = canonical_suite_command()
        self.assertEqual(command[0], sys.executable)
        self.assertIn("discover", command)
        self.assertIn("-s", command)
        self.assertEqual(command[command.index("-s") + 1], "tests")

    def test_no_pattern_means_no_dash_p(self):
        command = canonical_suite_command()
        self.assertNotIn("-p", command)

    def test_pattern_is_appended_via_dash_p(self):
        command = canonical_suite_command(pattern="test_foo.py")
        self.assertIn("-p", command)
        self.assertEqual(command[command.index("-p") + 1], "test_foo.py")


if __name__ == "__main__":
    unittest.main()
