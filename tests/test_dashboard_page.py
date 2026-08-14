"""A smoke test for pages/1_Dashboard.py -- for a Streamlit page, "runs to
completion without raising" is most of the test. This caught a real,
pre-existing bug the first time it was actually run after Phase 7 added
the Coworker Agents section: `active_executions` was referenced but never
defined (should have been `list_active()`), which crashed the entire page
load. Read-only: nothing on this page writes to any persisted store, so
the real ~/Library/Application Support/CampusPilot files are read as-is
here, matching the page's own real behavior, rather than isolated to a
temp file the way write-capable modules' tests are.

Run with: python -m unittest tests.test_dashboard_page -v
"""
import os
import runpy
import unittest

_DASHBOARD_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "pages", "1_Dashboard.py")


class TestDashboardPageRuns(unittest.TestCase):

    def test_page_executes_without_raising(self):
        try:
            runpy.run_path(_DASHBOARD_PATH, run_name="__main__")
        except Exception as error:
            self.fail(f"pages/1_Dashboard.py raised {type(error).__name__}: {error}")


if __name__ == "__main__":
    unittest.main()
