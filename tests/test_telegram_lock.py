"""Tests for agent/telegram_lock.py -- the single-instance lifetime lock
for the Telegram daemon. Uses the redirected TELEGRAM_LOCK_FILE (see
tests/_safety.py).

Run with: python -m unittest tests.test_telegram_lock -v
"""
import unittest

import agent.telegram_lock as tl


class TestTelegramLock(unittest.TestCase):

    def tearDown(self):
        import os
        if os.path.exists(tl.TELEGRAM_LOCK_FILE):
            os.remove(tl.TELEGRAM_LOCK_FILE)

    def test_first_acquire_succeeds_and_returns_an_open_handle(self):
        handle = tl.acquire_or_exit()
        self.addCleanup(handle.close)
        self.assertFalse(handle.closed)

    def test_second_acquire_while_held_raises(self):
        first = tl.acquire_or_exit()
        self.addCleanup(first.close)
        with self.assertRaises(tl.TelegramDaemonAlreadyRunning):
            tl.acquire_or_exit()

    def test_lock_is_reusable_after_the_holder_releases(self):
        first = tl.acquire_or_exit()
        first.close()
        second = tl.acquire_or_exit()
        self.addCleanup(second.close)
        self.assertFalse(second.closed)


if __name__ == "__main__":
    unittest.main()
