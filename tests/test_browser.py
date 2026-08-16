"""Regression tests for tools/browser.py's browser lifecycle/ownership
fix (Phase 9 Milestone 1) -- the cross-process Chrome-profile contention
risk described in PROFILE_DIR's own comment, and the lock-based fix in
agent/browser_lock.py wired in here.

Playwright/Chrome itself is mocked throughout (the external-call
boundary, per this project's established test policy) -- no test here
ever launches a real browser or touches a real Chrome profile. The lock
primitive underneath is exercised for real, not mocked, matching
tests/test_browser_lock.py and tests/test_scheduler_lock.py's own
"test the real concurrency primitive when practical" philosophy: tests
that need to simulate "another process already owns the profile" hold
the real BROWSER_LOCK_FILE via a genuinely separate open() call.

Run with: python -m unittest tests.test_browser -v
"""
import fcntl
import os
import tempfile
import unittest
from unittest.mock import MagicMock, patch

import agent.browser_lock as browser_lock
import tools.browser as browser
import tools.autofill as autofill


class _IsolatedBrowserTestCase(unittest.TestCase):
    """Redirects both the lock file and the (never-actually-touched-for-
    real, since Playwright is mocked) profile directory to temp paths,
    and resets tools.browser's module-level globals -- real state that
    would otherwise leak between tests exactly like any other file-backed
    store in this project's test suite."""

    def setUp(self):
        self._real_lock_file = browser_lock.BROWSER_LOCK_FILE
        self._real_profile_dir = browser.PROFILE_DIR
        browser_lock.BROWSER_LOCK_FILE = tempfile.mktemp(suffix=".lock")
        browser.PROFILE_DIR = tempfile.mkdtemp(prefix="jarvis-test-chrome-profile-")

        self._real_playwright = browser._playwright
        self._real_context = browser._context
        self._real_shutdown_registered = browser._shutdown_registered
        browser._playwright = None
        browser._context = None
        browser._shutdown_registered = False

    def tearDown(self):
        browser._playwright = self._real_playwright
        browser._context = self._real_context
        browser._shutdown_registered = self._real_shutdown_registered

        browser_lock.release()
        if os.path.exists(browser_lock.BROWSER_LOCK_FILE):
            os.remove(browser_lock.BROWSER_LOCK_FILE)
        browser_lock.BROWSER_LOCK_FILE = self._real_lock_file
        browser.PROFILE_DIR = self._real_profile_dir

    def _hold_lock_from_elsewhere(self):
        os.makedirs(os.path.dirname(browser_lock.BROWSER_LOCK_FILE), exist_ok=True)
        held = open(browser_lock.BROWSER_LOCK_FILE, "a+")
        fcntl.flock(held.fileno(), fcntl.LOCK_EX)
        return held

    def _mock_playwright_chain(self):
        """A fake sync_playwright()/chromium.launch_persistent_context()
        chain -- fake_context.pages starts empty so _get_page() creates
        one via new_page(), matching the real Playwright API shape this
        module actually calls."""
        fake_page = MagicMock()
        fake_page.is_closed.return_value = False
        fake_page.url = "https://example.com"
        fake_page.inner_text.return_value = "some page text"

        fake_context = MagicMock()
        fake_context.pages = []
        fake_context.new_page.return_value = fake_page

        fake_playwright_instance = MagicMock()
        fake_playwright_instance.chromium.launch_persistent_context.return_value = fake_context

        mock_sync_playwright = MagicMock()
        mock_sync_playwright.return_value.start.return_value = fake_playwright_instance

        return mock_sync_playwright, fake_playwright_instance, fake_context, fake_page


class TestLockAcquisitionAroundLaunch(_IsolatedBrowserTestCase):

    def test_lock_is_acquired_before_launching(self):
        mock_sync_playwright, _, fake_context, _ = self._mock_playwright_chain()
        with patch("tools.browser.sync_playwright", mock_sync_playwright):
            browser._get_context()
        self.assertTrue(browser_lock.is_held())

    def test_raises_browser_busy_when_lock_held_elsewhere(self):
        held = self._hold_lock_from_elsewhere()
        try:
            mock_sync_playwright, _, _, _ = self._mock_playwright_chain()
            with patch("tools.browser.sync_playwright", mock_sync_playwright):
                with self.assertRaises(browser.BrowserBusyError):
                    browser._get_context()
            mock_sync_playwright.assert_not_called()
        finally:
            held.close()

    def test_lock_released_if_launch_itself_fails(self):
        mock_sync_playwright = MagicMock()
        mock_sync_playwright.return_value.start.side_effect = RuntimeError("chrome failed to start")
        with patch("tools.browser.sync_playwright", mock_sync_playwright):
            with self.assertRaises(RuntimeError):
                browser._get_context()
        self.assertFalse(browser_lock.is_held())

    def test_shutdown_releases_the_lock(self):
        mock_sync_playwright, _, _, _ = self._mock_playwright_chain()
        with patch("tools.browser.sync_playwright", mock_sync_playwright):
            browser._get_context()
        self.assertTrue(browser_lock.is_held())
        browser._shutdown()
        self.assertFalse(browser_lock.is_held())

    def test_discard_dead_context_releases_the_lock(self):
        mock_sync_playwright, _, _, _ = self._mock_playwright_chain()
        with patch("tools.browser.sync_playwright", mock_sync_playwright):
            browser._get_context()
        self.assertTrue(browser_lock.is_held())
        browser._discard_dead_context()
        self.assertFalse(browser_lock.is_held())
        self.assertIsNone(browser._context)


class TestSingleProcessReuse(_IsolatedBrowserTestCase):

    def test_repeated_calls_reuse_the_same_context_without_relaunching(self):
        mock_sync_playwright, _, fake_context, _ = self._mock_playwright_chain()
        with patch("tools.browser.sync_playwright", mock_sync_playwright):
            first = browser._get_context()
            second = browser._get_context()

        self.assertIs(first, second)
        mock_sync_playwright.return_value.start.assert_called_once()

    def test_repeated_calls_reuse_the_same_open_tab(self):
        mock_sync_playwright, _, fake_context, fake_page = self._mock_playwright_chain()
        with patch("tools.browser.sync_playwright", mock_sync_playwright):
            fake_context.pages = [fake_page]
            page_one = browser._get_live_page()
            page_two = browser._get_live_page()

        self.assertIs(page_one, page_two)
        fake_context.new_page.assert_not_called()


class TestBrowserBusyResultsAreClean(_IsolatedBrowserTestCase):
    """Contention must produce a controlled, user-legible result string --
    never a raw exception, never a hang."""

    def test_open_and_read_returns_a_clean_message_not_an_exception(self):
        held = self._hold_lock_from_elsewhere()
        try:
            result = browser.open_and_read("example.com")
        finally:
            held.close()
        self.assertIn("already using the browser", result)

    def test_search_on_page_returns_a_clean_message(self):
        held = self._hold_lock_from_elsewhere()
        try:
            result = browser.search_on_page("query")
        finally:
            held.close()
        self.assertIn("already using the browser", result)

    def test_click_on_page_returns_a_clean_message(self):
        held = self._hold_lock_from_elsewhere()
        try:
            result = browser.click_on_page("Sign in")
        finally:
            held.close()
        self.assertIn("already using the browser", result)

    @patch("tools.autofill.get_login")
    def test_fill_login_returns_a_clean_message(self, mock_get_login):
        mock_get_login.return_value = {"domain": "example.com", "username": "tester"}
        held = self._hold_lock_from_elsewhere()
        try:
            result = autofill.fill_login("example")
        finally:
            held.close()
        self.assertIn("already using the browser", result)

    @patch("tools.autofill.get_login")
    def test_confirm_login_returns_a_clean_message(self, mock_get_login):
        mock_get_login.return_value = {"domain": "example.com", "username": "tester"}
        autofill._pending_confirmation = {"site": "example", "expires": float("inf")}
        held = self._hold_lock_from_elsewhere()
        try:
            result = autofill.confirm_login("example")
        finally:
            held.close()
            autofill._pending_confirmation = None
        self.assertIn("already using the browser", result)


class TestFailureCleanupAndRetry(_IsolatedBrowserTestCase):

    def test_a_dead_context_is_discarded_and_a_fresh_one_launched(self):
        # First launch produces a context that raises as soon as its
        # .pages is touched (simulating one that died underneath us,
        # e.g. the user closed the window); the retry path must discard
        # it, release the lock, and successfully re-acquire + relaunch a
        # genuinely fresh context rather than reusing the broken one.
        broken_context = MagicMock()
        broken_context.pages = MagicMock()
        broken_context.pages.__iter__ = MagicMock(side_effect=RuntimeError("context has been closed"))

        working_page = MagicMock()
        working_page.is_closed.return_value = False
        working_context = MagicMock()
        working_context.pages = []
        working_context.new_page.return_value = working_page

        fake_playwright_instance = MagicMock()
        fake_playwright_instance.chromium.launch_persistent_context.side_effect = [
            broken_context, working_context,
        ]
        mock_sync_playwright = MagicMock()
        mock_sync_playwright.return_value.start.return_value = fake_playwright_instance

        with patch("tools.browser.sync_playwright", mock_sync_playwright):
            page = browser._get_live_page()

        self.assertIs(page, working_page)
        self.assertEqual(fake_playwright_instance.chromium.launch_persistent_context.call_count, 2)
        self.assertTrue(browser_lock.is_held())  # the successful retry re-acquired it


class TestExistingBehaviorIntact(_IsolatedBrowserTestCase):

    def test_open_and_read_still_returns_page_content_when_uncontested(self):
        mock_sync_playwright, _, fake_context, fake_page = self._mock_playwright_chain()
        fake_page.url = "https://example.com/"
        fake_page.inner_text.return_value = "Example Domain content"

        with patch("tools.browser.sync_playwright", mock_sync_playwright):
            with patch("tools.browser._settle"):
                result = browser.open_and_read("example.com")

        self.assertIn("Example Domain content", result)
        self.assertIn("https://example.com/", result)


class TestProfileIsolation(unittest.TestCase):
    """Confirms tools/browser.py never targets a real, everyday browser
    profile -- a dedicated CampusPilot-only directory, matching
    PROFILE_DIR's own comment."""

    def test_profile_dir_is_not_a_real_chrome_profile_path(self):
        real_chrome_defaults = (
            os.path.expanduser("~/Library/Application Support/Google/Chrome"),
            os.path.expanduser("~/Library/Application Support/Google/Chrome/Default"),
        )
        self.assertNotIn(browser.PROFILE_DIR, real_chrome_defaults)
        self.assertIn("CampusPilot", browser.PROFILE_DIR)

    def test_profile_dir_is_dedicated_and_separate(self):
        self.assertTrue(browser.PROFILE_DIR.endswith("chrome-profile"))


if __name__ == "__main__":
    unittest.main()
