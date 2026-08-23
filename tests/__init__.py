"""Package pre-discovery safety bootstrap. Read this before running or
writing any test in this project.

THE SUPPORTED, SAFETY-GUARANTEED WAYS TO RUN THIS SUITE:

  Full suite:        python -m unittest discover -s tests -t . -v
  One test module:   python -m unittest tests.test_name -v

The `-t .` flag on the full-suite command is load-bearing, not
cosmetic. `unittest discover`'s start directory (`-s tests`) and its
top-level directory default to the same thing unless `-t` says
otherwise; when they're the same, `discover` imports every test file as
a bare top-level module (`test_history_capture`, not
`tests.test_history_capture`) and never imports the `tests` package at
all -- so this file's own code, below, never runs. Verified empirically
during the Phase 9 reliability audit (2026-08-23) with a marker that
never printed under the old bare `-s tests` command, and confirmed the
fix directly: `unittest.TestLoader().discover(start_dir="tests",
top_level_dir=".")` -- exactly what `-t .` produces -- imports every
test as a real `tests.*` submodule and runs this file first, every time.

A controlled full run under the OLD, unsafe command was proven to write
into six real files under the live
`~/Library/Application Support/CampusPilot`, including a silent content
change to the real memory.json and a real macOS Keychain entry. None of
that was this file's fault -- it never got the chance to run.

NOT A SAFETY-GUARANTEED INVOCATION: running a test file directly as a
script (`python tests/test_x.py`) never imports the `tests` package
either, for the same reason. Don't do this for anything that touches a
real store or the network; use one of the two commands above.

WHAT RUNS HERE: exactly one call, `tests._safety.install_test_safety()`
-- see that module's own docstring for the full architecture (temp
run-root creation, all 19 production-store redirects, the external-
network firewall, the provider/browser tripwires). This file is
deliberately thin; the actual bootstrap logic lives in a dedicated
module rather than growing indefinitely here, per this project's own
"one file per concern" convention.

Individual test files' own `setUp`/`tearDown` redirects (e.g.
`tests/test_claude_gateway.py`'s `IsolatedExecutorTestCase`) are NOT
made redundant by this and are left exactly as they were -- real,
independent defense-in-depth, not duplicated dead code. Nesting a second
redirect on top of one already in effect has always been safe in this
project (the original USAGE_FILE handling established the pattern this
file now extends to everything).
"""
from tests import _safety

_safety.install_test_safety()

TEST_SAFETY_INSTALLED = _safety.TEST_SAFETY_INSTALLED
