"""M10.0 -- the structural regression guard for the finding this
milestone exists to address: agent/agents/worker.py's `coworker.
execute()` runs in a genuinely separate OS subprocess that never imports
agent.executor, so none of the coworker agents' own real side-effecting
actions pass through agent.executor's `_run_tool` -- where tools.
registry's permission levels and agent.autonomy's decision actually
live for every registered tool.

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

agent.autonomy.should_request_confirmation is this milestone's
chokepoint: a pure decision function (never enforces anything itself --
the caller is responsible for acting on its verdict, the same contract
_run_tool has always had). Its signature and placement are deliberately
general -- any coworker action could route through it -- but this round
routes none of them through it yet; it only builds the mechanism and
documents, with this file, exactly what is NOT yet covered. The one
genuinely new finding from the audit that produced this file is
MemoryAgent's: identical in shape to ResearchAgent's already-documented
exception (agent/research_agent.py's own internal tool loop, CLAUDE.md
rule 3's pre-existing, deliberate carve-out), but never itself named as
accepted anywhere before this. agent/memory/safety.py's content filter
still applies to MemoryAgent's writes (it's inside agent.memory.remember
itself, a layer below the registry) -- but that is a content filter, not
a permission gate, and the registry/autonomy gate is genuinely bypassed.
Deliberately NOT fixed this round -- see CLAUDE.md rule 3 and ROADMAP.md's
"MemoryAgent bypass audit" entry for the follow-up this file does not
attempt to resolve.

A near-term follow-up commit (Phase 10 increment 1, landing right after
this one) gives agent/agents/coding.py a real, non-stub implementation
and is expected to extend this file's _SCAN_TARGETS/
ACCEPTED_UNGATED_CALL_SITES accordingly, with CodingAgent's write path
routed through should_request_confirmation as the first coworker action
this chokepoint actually gates -- deliberately not attempted in this
commit, which only builds and proves the mechanism itself.

Run with: python -m unittest tests.test_gating_structural -v
"""
import ast
import os
import unittest
from dataclasses import dataclass

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Every file a genuinely separate OS subprocess can reach starting from
# agent/agents/worker.py's `coworker.execute(task, context)`, that
# already exists with real side-effecting behavior as of this commit --
# agent/agents/coding.py is deliberately excluded here: at this point in
# history it is still the Phase-9 stub (no real file I/O at all), so it
# has nothing to scan yet. See module docstring.
_SCAN_TARGETS = (
    "agent/agents/qa.py",
    "agent/agents/memory.py",
    "agent/research_agent.py",
)

# Calling any of these, by name, inside a function is what this scan
# treats as "a real side effect" -- subprocess execution, real file I/O,
# real browser/network navigation, or a real memory-store write/read.
# Matched by the bare function/attribute name (ast.Name.id or
# ast.Attribute.attr), not fully-qualified -- precise enough for these
# files' actual current import shapes (verified by direct inspection,
# not assumed) without needing full call-graph resolution.
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
# real decision, written down, not an omission. MemoryAgent's is the
# genuinely new finding: shaped identically to ResearchAgent's already-
# documented exception, never itself named as accepted before this file.
# Also added to CLAUDE.md rule 3 and opened as its own ROADMAP.md item --
# not fixed here.
ACCEPTED_UNGATED_CALL_SITES = frozenset({
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
    AcceptedException(
        file="agent/agents/memory.py", function="execute",
        reason="Real memory-store writes AND reads, shaped identically to ResearchAgent's "
               "documented exception above but never itself named as accepted anywhere "
               "before this file. agent.memory.safety's content filter still applies "
               "(it's inside agent.memory.remember itself, a layer below the registry) "
               "-- but that is a content filter, not a permission gate, and the registry/ "
               "autonomy gate IS bypassed. Deliberately NOT fixed this round -- see "
               "CLAUDE.md rule 3 and ROADMAP.md's 'MemoryAgent bypass audit' item.",
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
            f"agent.autonomy.should_request_confirmation) or add it to the accepted set "
            f"with a real reason.",
        )
        self.assertEqual(
            no_longer_present, set(),
            f"Accepted exception(s) no longer found as ungated -- they were apparently "
            f"gated (or removed): {no_longer_present}. If that's real, remove them from "
            f"ACCEPTED_UNGATED_CALL_SITES; leaving a stale entry hides that this actually "
            f"got safer.",
        )

    def test_every_accepted_exception_has_a_real_reason(self):
        for exc in ACCEPTED_UNGATED_CALL_SITES:
            self.assertTrue(exc.reason and len(exc.reason) > 20, f"{exc.file}::{exc.function} needs a real reason")


if __name__ == "__main__":
    unittest.main()
