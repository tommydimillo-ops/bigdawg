"""Redirects agent.usage.USAGE_FILE and agent.history_store.HISTORY_DB to
disposable, this-run-only temp paths -- genuinely active protection for
one real invocation style, not dead code, and not the only protection
either. Read this before deciding whether to touch it.

TWO DIFFERENT WAYS THIS PROJECT'S TESTS GET RUN, TWO DIFFERENT OUTCOMES
(both verified empirically during Phase 9 M4.2's own development,
2026-08-22 -- not assumed):

1. `python -m unittest discover -s tests -v` (this project's documented
   full-suite command -- see CLAUDE.md, HANDOFF.md,
   .github/workflows/tests.yml). With no `-t`/top-level-directory flag,
   `start_dir == top_level_dir`, and `unittest discover` imports each
   test file as a bare top-level module (e.g. `test_history_capture`,
   not `tests.test_history_capture`) -- which does NOT require importing
   this package first. Confirmed with a stderr marker placed at this
   file's top level: it never printed during a real `discover` run, and
   the real production history.db was written to regardless of the
   reassignment below. **Under this invocation, this file provides no
   protection at all.**

2. `python -m unittest tests.test_history_capture -v` (the single-file
   invocation every test file in this project documents as its own
   "Run with:" instruction). This dotted module path genuinely requires
   Python to import the `tests` package first, which DOES execute this
   file. Confirmed empirically: with only this file's reassignment in
   place (no per-file isolation), running one test module this way
   correctly redirected HISTORY_DB and left the real production
   history.db untouched. **Under this invocation, this file is the only
   thing standing between a test and the real production file.**

Given both are true, removing this file's reassignments would remove
real protection for a real, actively-documented invocation style, even
though it provides none for the *other* real, actively-documented
invocation style (the one CI and the full-suite command actually use).
Keeping it is deliberate, not an oversight -- see the M4.2 lifecycle-
hardening pass's report for the review that reached this conclusion.

THE PROTECTION THAT MATTERS FOR (1), THE FULL-SUITE CASE: per-file
isolation. Every test file whose tests exercise a real (even if
provider-mocked) execute_task_stream()/execute_task() call explicitly
redirects agent.history_store.HISTORY_DB itself in its own setUp/
tearDown, the same established pattern already used for
execution_history.HISTORY_FILE and jarvis_state.STATE_FILE in those same
files (see tests/test_claude_gateway.py, tests/test_executor_multi_
provider_fallback.py, tests/test_executor_phase5_integration.py,
tests/test_agents_executor_integration.py, tests/test_phase6_security.py,
tests/test_usage_limits_integration.py, tests/test_voice_session.py,
tests/test_voice_skill_integration.py, tests/test_history_capture.py).
The long-standing USAGE_FILE reassignment below has the identical split
protection profile -- it went unnoticed for (1) only because every test
file that touches agent.usage.USAGE_FILE already redundantly isolates it
itself, the same way.

**If you add a new file-backed store**: do not assume adding its
reassignment here is sufficient on its own. Add per-file isolation to
every test that actually exercises a code path writing to it, and treat
this file's reassignment as a secondary layer that helps for single-file
dotted invocations, never as the primary guarantee for the full suite.
"""
import tempfile

import agent.history_store as _history_store
import agent.usage as _usage

_usage.USAGE_FILE = tempfile.mktemp(prefix="jarvis-test-usage-", suffix=".json")
_history_store.HISTORY_DB = tempfile.mktemp(prefix="jarvis-test-history-", suffix=".db")
