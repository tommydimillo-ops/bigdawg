"""The exact, load-bearing invocation of this project's own canonical
test suite -- one function, so the specific flags (`-t .` above all)
can never diverge between callers again.

Real, not hypothetical: agent/agents/qa.py's own copy of this command
was missing `-t .` until a code review caught it (agent/agents/
coding.py's separately-written copy had it correctly). Without `-t .`,
`unittest discover`'s start directory and top-level directory default to
the same thing, so it imports every test file as a bare top-level
module and never imports the `tests` package at all -- meaning
`tests/__init__.py`'s safety bootstrap (redirecting every production
store path, blocking external network) never runs. QAAgent's own "do
the tests still pass?" capability is already fully live in production
(no setting needs to be turned on for it), so every real invocation
before this fix ran the actual suite against real production paths and
the real Keychain -- exactly the incident CLAUDE.md's "How to test"
section documents having happened for real from a bare `-s tests`
command elsewhere in this project's own history.

Deliberately not a merge of qa.py's and coding.py's own `_run_test_suite`
functions: those return genuinely different shapes for genuinely
different callers (QAAgent's `ok`/`summary`/`raw_tail` vs. CodingAgent's
`suite_exit_code`/`tests_run`/`tests_failed`), and reconciling that under
time pressure risks introducing a new bug for the sake of removing a few
duplicated lines. What actually needs to never diverge is the command
itself -- this is that, and only that.
"""
import sys
from typing import List, Optional


def canonical_suite_command(pattern: Optional[str] = None) -> List[str]:
    """The exact argv for running this project's own suite, or, with
    `pattern`, one narrow slice of it via unittest's own `-p` filter --
    always through the same `-t .` `-s tests` invocation CLAUDE.md's "How
    to test" section documents as the only safety-guaranteed entry
    point. Callers add their own flags (`-v`, etc.) and supply their own
    `cwd`/`timeout` to `subprocess.run`."""
    command = [sys.executable, "-m", "unittest", "discover", "-s", "tests", "-t", "."]
    if pattern is not None:
        command += ["-p", pattern]
    return command
