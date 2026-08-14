"""Executed once, before any test module in this package is imported, by
`python -m unittest discover -s tests`. Redirects agent.usage.USAGE_FILE
to a disposable, this-run-only temp file for the whole suite -- a single,
central guard rather than relying on every individual test file that
might exercise an LLM/audio call site (agent/executor.py, agent/
research_agent.py, tools/vision.py, voice/listen.py, voice/speak.py,
etc.) to remember to isolate it itself the way execution_history.
HISTORY_FILE and jarvis_state.STATE_FILE already are isolated, per file.

Without this, a mocked LLM response's `.usage` attribute -- even a
MagicMock, degraded by agent.usage._as_number to 0 tokens/$0 cost --
still writes a real, if harmless, entry into the real
~/Library/Application Support/CampusPilot/usage_history.json every time
the automated suite runs. Confirmed live during Phase 8's development:
245 zero-cost test-artifact entries had already accumulated there before
this guard existed, the same class of test-artifact pollution Phase 7's
own report flagged for the audit log.

Individual test files (see tests/test_usage.py's IsolatedUsageTestCase)
can still further redirect/restore USAGE_FILE within their own setUp/
tearDown on top of this -- they save and restore whatever this module-
level value already is, never the true production path, so nesting is
safe.
"""
import tempfile

import agent.usage as _usage

_usage.USAGE_FILE = tempfile.mktemp(prefix="jarvis-test-usage-", suffix=".json")
