"""M10.0 -- the structural regression guard for the finding this
milestone exists to address: agent/agents/worker.py's `coworker.
execute()` runs in a genuinely separate OS subprocess that never imports
agent.executor, so none of the four coworker agents' own real
side-effecting actions pass through agent.executor's `_run_tool` --
where tools.registry's permission levels and agent.autonomy's decision
actually live for every registered tool.

This does not claim to fix that gap in general. It does two things:
  1. Documents, in code (not in a comment someone can silently let
     drift), exactly which of the coworker agents' own functions perform
     a real side effect without going through agent.autonomy's decision
     engine, and why each one is currently accepted as-is.
  2. Actually re-derives that set from the real source on every test
     run, via ast parsing, and asserts it equals the documented set --
     so a NEW ungated call site (a function added to one of these files
     that performs a real side effect without also calling
     agent.autonomy.should_request_confirmation) fails this test
     immediately, and removing coverage for an EXISTING one (letting it
     silently drop out of the accepted set without anyone deciding that
     on purpose) fails it too.

CodingAgent's own write_file is deliberately NOT in the accepted set --
see agent/agents/coding.py's _write_file, which now calls agent.
autonomy.should_request_confirmation directly (the first coworker action
routed through the same chokepoint agent/executor.py's _run_tool
already uses for every registered tool). This test proves that routing
actually took: if _write_file's gate call were ever removed, this test
would immediately re-classify it as a new, undocumented bypass and fail.

MemoryAgent's execute() is ALSO deliberately NOT in the accepted set, as
of the "MemoryAgent bypass audit" ROADMAP.md item this file's own
docstring used to point to as unresolved -- see agent/agents/memory.py's
execute(), which now gates its remember() call the same way, through the
same should_request_confirmation chokepoint, with an explicit
permission_level=1 override matching remember_fact's own registered
level (not write_file's 2 -- a memory write is a "safe local action,"
not a file/code modification). recall() (read-only) is deliberately
UNCHANGED and stays ungated in the source, same reasoning as
CodingAgent's _read_file -- but unlike _read_file/_write_file (two
separate functions in coding.py, so the scanner can track each one
independently), remember() and recall() both live inside this same
execute() function. The scan below is function-granular, not
branch-granular: once execute() contains any call to
should_request_confirmation, the whole function -- recall() branch
included -- stops being flagged as an ungated call site. That is an
honest limit of this test's coverage, not a claim that recall() is
separately re-verified as accepted going forward; if that precision is
ever needed, split recall() into its own function the way coding.py
did.

Everything else found by the same audit that produced this file's own
accepted set stays exactly as it already was -- reads (CodingAgent's
_read_file), test-suite spawns (CodingAgent's _run_test_suite/
_collected_test_count, QAAgent's _run_test_suite), and ResearchAgent's
own pre-existing, CLAUDE.md-documented exception are all still ungated,
by explicit choice, not by oversight -- this file is that explicit
choice, in writing. ("Ungated" here means not routed through agent.
autonomy's decision engine. Since plan-b8 every one of those READS is
separately behind agent.secret_paths.refuse_secret_read, enforced by
tests/test_read_paths_structural.py; the test-suite spawns are NOT covered
by that -- they execute agent-written code, tracked in that file's
ACKNOWLEDGED_UNGUARDED_CODE_EXECUTION.) See CLAUDE.md's "Important coding conventions"
section (the ResearchAgent-exception rule) and ROADMAP.md's "MemoryAgent
bypass audit" entry (now resolved -- gated as of this pass) for the full
history.

Run with: python -m unittest tests.test_gating_structural -v
"""
import ast
import os
import unittest
from dataclasses import dataclass

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Every file a genuinely separate OS subprocess can reach starting from
# agent/agents/worker.py's `coworker.execute(task, context)` -- the four
# coworker agents' own modules, plus the one file each of ResearchAgent/
# QAAgent's real work is one hop into (research_agent.py). Deliberately
# NOT agent/coding_checkpoint.py: that file is the safety mechanism an
# agent's own actions run through, not a task-directed action itself.
_SCAN_TARGETS = (
    "agent/agents/coding.py",
    "agent/agents/qa.py",
    "agent/agents/memory.py",
    "agent/research_agent.py",
)

# Calling any of these, by name, inside a function is what this scan
# treats as "a real side effect" -- subprocess execution, real file I/O,
# real browser/network navigation, or a real memory-store write/read.
# Matched by the bare function/attribute name (ast.Name.id or
# ast.Attribute.attr), not fully-qualified -- precise enough for these
# four files' actual current import shapes (verified by direct
# inspection, not assumed) without needing full call-graph resolution.
_DANGEROUS_CALL_NAMES = frozenset({
    "run", "Popen", "call", "check_call", "check_output",  # subprocess.*
    "open",  # builtin open() -- real file read or write
    "open_and_read",  # tools/browser.py -- real browser/network navigation
    "read_document",  # documents/reader.py -- real local file read
    "remember", "recall",  # agent/memory_agent.py -- real memory store write/read
})

# The one function this same audit found actually calls, directly, to
# route through agent.autonomy's decision engine -- kept as a single
# named constant (not a string literal repeated at each check site) so a
# rename in agent/autonomy.py is a clear, loud failure here too.
_GATE_CALL_NAME = "should_request_confirmation"


@dataclass(frozen=True)
class AcceptedException:
    file: str
    function: str
    reason: str


# The full, explicit table -- built from the M10.0 audit (this
# conversation's own enumeration, independently re-verified here by
# actually parsing the source, not copied from memory). Each entry is a
# real decision, written down, not an omission:
#   - CodingAgent's write_file is NOT here -- it is the one write this
#     round actually gates (see module docstring).
#   - Reads and test-suite spawns stay ungated this round: the blast
#     radius this milestone targets is writes specifically.
#   - agent/agents/memory.py's execute() WAS here (shaped identically to
#     ResearchAgent's exception below, but never itself named as
#     accepted before M10.0 found it) -- resolved by the "MemoryAgent
#     bypass audit" ROADMAP.md item: execute() now gates its remember()
#     call, so it no longer appears in this set. See this file's own
#     module docstring for why recall() isn't tracked as a separate
#     entry either, rather than silently dropping out unexplained.
ACCEPTED_UNGATED_CALL_SITES = frozenset({
    AcceptedException(
        file="agent/agents/coding.py", function="_read_file",
        reason="Not autonomy-gated: read-only, and writes are this round's blast radius. Secret "
               "files are refused separately by agent.secret_paths (plan-b8).",
    ),
    AcceptedException(
        file="agent/agents/coding.py", function="_run_test_suite",
        reason="Real subprocess spawn, but read-only (runs the suite, mutates nothing "
               "in the repo itself) -- CodingAgent's own mandatory final verification step.",
    ),
    AcceptedException(
        file="agent/agents/coding.py", function="_collected_test_count",
        reason="Same read-only test-suite-spawn shape as _run_test_suite, scoped to one "
               "file -- the uncollected-test-file verification check.",
    ),
    AcceptedException(
        file="agent/agents/qa.py", function="_run_test_suite",
        reason="Real subprocess spawn, but read-only by construction -- no write/delete "
               "path exists anywhere in this function.",
    ),
    AcceptedException(
        file="agent/research_agent.py", function="_run_tool",
        reason="Real browser/network navigation and real local file reads, both "
               "read-only. CLAUDE.md rule 3's own explicit, deliberate, pre-existing "
               "exception -- predates this session, not something M10.0 changed.",
    ),
})


def _called_names(node: ast.AST) -> set:
    names = set()
    for child in ast.walk(node):
        if isinstance(child, ast.Call):
            func = child.func
            if isinstance(func, ast.Name):
                names.add(func.id)
            elif isinstance(func, ast.Attribute):
                names.add(func.attr)
    return names


def find_ungated_call_sites(filepath: str) -> list:
    """The real scan: every function in `filepath` that calls something
    in _DANGEROUS_CALL_NAMES without also calling _GATE_CALL_NAME
    somewhere in its own body. Returns (file, function_name) pairs."""
    with open(filepath) as file:
        tree = ast.parse(file.read(), filename=filepath)

    found = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            called = _called_names(node)
            if called & _DANGEROUS_CALL_NAMES and _GATE_CALL_NAME not in called:
                found.append((filepath, node.name))
    return found


class TestUngatedCallSitesMatchTheAcceptedSet(unittest.TestCase):
    def test_real_ungated_call_sites_equal_the_documented_accepted_set(self):
        discovered = set()
        for relative_path in _SCAN_TARGETS:
            for filepath, function_name in find_ungated_call_sites(os.path.join(_PROJECT_ROOT, relative_path)):
                discovered.add((relative_path, function_name))

        accepted = {(exc.file, exc.function) for exc in ACCEPTED_UNGATED_CALL_SITES}

        new_bypasses = discovered - accepted
        no_longer_present = accepted - discovered

        self.assertEqual(
            new_bypasses, set(),
            f"New ungated call site(s) found that aren't in ACCEPTED_UNGATED_CALL_SITES: "
            f"{new_bypasses}. Either gate this action (route it through "
            f"agent.autonomy.should_request_confirmation, the way agent/agents/coding.py's "
            f"_write_file now does) or add it to the accepted set with a real reason.",
        )
        self.assertEqual(
            no_longer_present, set(),
            f"Accepted exception(s) no longer found as ungated -- they were apparently "
            f"gated (or removed): {no_longer_present}. If that's real, remove them from "
            f"ACCEPTED_UNGATED_CALL_SITES; leaving a stale entry hides that this actually "
            f"got safer.",
        )

    def test_write_file_is_not_in_the_accepted_set(self):
        # The one thing this round actually changed: confirms the fix
        # didn't just get documented as an exception instead of applied.
        accepted_functions = {exc.function for exc in ACCEPTED_UNGATED_CALL_SITES}
        self.assertNotIn("_write_file", accepted_functions)

    def test_every_accepted_exception_has_a_real_reason(self):
        for exc in ACCEPTED_UNGATED_CALL_SITES:
            self.assertTrue(exc.reason and len(exc.reason) > 20, f"{exc.file}::{exc.function} needs a real reason")


if __name__ == "__main__":
    unittest.main()
