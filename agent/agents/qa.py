"""QAAgent -- testing, validation, and "did the requested work actually
happen" checks. Two genuinely different capabilities, kept narrow and
explicit rather than a general-purpose QA engine:

1. A request specifically about the project's OWN test suite ("run the
   tests", "do the tests still pass") is handled directly: runs the
   existing automated test suite read-only (no code changes -- this is
   exactly the safety check this whole codebase's own development
   process already runs constantly) and returns structured pass/fail
   counts. Bounded by an explicit subprocess timeout, same pattern as
   tools/agenda.py's AppleScript calls.
2. Everything else this phase -- "did tool X's result actually verify",
   general regression review, anything needing real tool access --
   defers to the ordinary executor (metadata["deferred_to_executor"]),
   reusing agent.verification.verify() for the specific "did a tool call
   actually take effect" question rather than building a second
   verification framework, exactly as Phase 7 section 9 requires.

QAAgent never approves its own (or anyone else's) destructive actions --
it only ever reads/checks, never writes, never calls a side-effecting
tool.
"""
import os
import subprocess
import sys
import time

from agent.agents.base import Agent, AgentMetadata
from agent.agents.models import AgentResult
from agent.request_context import RequestContext

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_TEST_SUITE_TIMEOUT_SECONDS = 45

_TEST_SUITE_HINTS = (
    "run the tests", "run tests", "all tests", "still pass", "still passing",
    "tests pass", "test suite", "unit tests", "run the test suite",
)


def _wants_test_suite_run(task_lower: str) -> bool:
    return any(hint in task_lower for hint in _TEST_SUITE_HINTS)


def _run_test_suite() -> dict:
    """Runs this project's own test suite read-only and returns a
    structured summary -- no destructive action, no test-suite mutation,
    just the same `python -m unittest discover` invocation used
    throughout this project's own development."""
    try:
        result = subprocess.run(
            [sys.executable, "-m", "unittest", "discover", "-s", "tests", "-p", "test_*.py"],
            capture_output=True, text=True, timeout=_TEST_SUITE_TIMEOUT_SECONDS,
            cwd=_PROJECT_ROOT,
        )
    except subprocess.TimeoutExpired:
        return {"ok": False, "summary": "Test suite did not finish within the QA timeout.", "raw_tail": ""}

    output = (result.stdout or "") + (result.stderr or "")
    tail = output.strip().splitlines()[-5:]
    passed = result.returncode == 0
    return {
        "ok": passed,
        "summary": "All tests passed." if passed else "One or more tests failed.",
        "raw_tail": "\n".join(tail),
    }


class QAAgent(Agent):

    @property
    def metadata(self) -> AgentMetadata:
        return AgentMetadata(
            name="qa",
            description=(
                "Checks whether work is correct: runs this project's own "
                "test suite read-only for test-health questions, and "
                "reuses agent.verification for specific tool-result checks. "
                "Never executes a destructive or side-effecting action."
            ),
            capabilities=["testing", "verification", "regression checks"],
            supported_task_types=["qa"],
        )

    def execute(self, task: str, context: RequestContext) -> AgentResult:
        start = time.time()
        lowered = task.lower()

        if _wants_test_suite_run(lowered):
            outcome = _run_test_suite()
            summary = outcome["summary"]
            if outcome["raw_tail"]:
                summary += f"\n\n{outcome['raw_tail']}"
            return AgentResult(
                success=outcome["ok"], agent_name=self.metadata.name, request_id=context.request_id,
                result=summary, duration_seconds=time.time() - start,
                verification_status="passed" if outcome["ok"] else "failed",
                metadata={"checked": "test_suite"},
            )

        # Nothing QAAgent can safely and directly answer without real
        # tool access this phase -- defer to the ordinary executor,
        # which already has the full permission/autonomy/verification
        # path for whatever tool this actually needs.
        return AgentResult(
            success=True, agent_name=self.metadata.name, request_id=context.request_id,
            result="", duration_seconds=time.time() - start,
            metadata={"deferred_to_executor": True},
        )
