"""Tests for agent/retry_policy.py -- deterministic, bounded retry
classification.

Run with: python -m unittest tests.test_retry_policy -v
"""
import unittest

from agent.retry_policy import MAX_RETRIES, retry_delay_seconds, should_retry


class _NamedError(Exception):
    """Lets a test construct an exception with an arbitrary __name__,
    since should_retry classifies by type(error).__name__ and the real
    SDK exception classes (APITimeoutError etc.) aren't worth importing
    here just to get the right class name."""


def _error(name):
    err = _NamedError("x")
    err.__class__.__name__ = name
    return err


class TestTransientErrorsRetry(unittest.TestCase):

    def test_timeout_retries_on_first_attempt(self):
        self.assertTrue(should_retry("get_weather", _error("TimeoutError"), 1))

    def test_connection_error_retries(self):
        self.assertTrue(should_retry("get_weather", _error("ConnectionError"), 1))

    def test_rate_limit_retries(self):
        self.assertTrue(should_retry("get_weather", _error("RateLimitError"), 1))

    def test_stops_at_max_retries(self):
        self.assertFalse(should_retry("get_weather", _error("TimeoutError"), MAX_RETRIES))


class TestNonTransientErrorsNeverRetry(unittest.TestCase):

    def test_permission_error_never_retries(self):
        self.assertFalse(should_retry("get_weather", _error("PermissionError"), 1))

    def test_key_error_never_retries(self):
        self.assertFalse(should_retry("run_python", _error("KeyError"), 1))

    def test_unknown_error_type_defaults_to_no_retry(self):
        self.assertFalse(should_retry("get_weather", _error("SomeWeirdError"), 1))


class TestDestructiveToolsNeverAutoRetry(unittest.TestCase):

    def test_computer_click_never_retries_even_on_timeout(self):
        self.assertFalse(should_retry("computer_click", _error("TimeoutError"), 1))

    def test_send_email_never_retries_even_on_timeout(self):
        self.assertFalse(should_retry("send_email", _error("TimeoutError"), 1))

    def test_confirm_login_never_retries(self):
        self.assertFalse(should_retry("confirm_login", _error("ConnectionError"), 1))

    def test_ordinary_tool_with_same_error_does_retry(self):
        # Sanity check that the destructive-tool block is what's actually
        # suppressing retry above, not something about the error itself.
        self.assertTrue(should_retry("get_weather", _error("TimeoutError"), 1))


class TestRetryDelay(unittest.TestCase):

    def test_delay_increases_with_attempt(self):
        self.assertLess(retry_delay_seconds(1), retry_delay_seconds(2))

    def test_delay_is_nonnegative(self):
        self.assertGreaterEqual(retry_delay_seconds(1), 0)


if __name__ == "__main__":
    unittest.main()
