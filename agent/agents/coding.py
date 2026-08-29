"""CodingAgent -- Phase 10 increment 1. Off by default
(config.settings.coding_agent_enabled); when disabled, execute() is
byte-for-byte the original stub (metadata["deferred_to_executor"]=True,
nothing touched) exactly as every prior phase left it.

When enabled, this is real: it checkpoints the repository
(agent.coding_checkpoint), runs its own small internal read/write/test
loop against the live Anthropic API, and automatically rolls back its own
changes if its own final test-suite run fails. See
.relay/PHASE10-DESIGN.md for the full design rationale this implements.

Why NOT agent.claude_gateway.invoke() (the path this file's own earlier
docstring anticipated): that function calls agent.executor.
execute_task_stream -- the full top-level orchestrator, with the FULL
tool registry attached, including consult_coworker_agent itself. Calling
it from inside CodingAgent's own execute() would start a brand-new,
depth-0 execution context that never increments agent.agents.manager's
MAX_AGENT_DEPTH counter at all -- a real, structural bypass of that
guard, not just a documented risk. Built instead as a narrow, dedicated
internal loop with its own tiny, fixed 3-tool set (read_file, write_file,
run_tests) and a direct Anthropic call, mirroring agent/research_agent.
py's own already-established exception to "tools go through
tools/registry.py" (CLAUDE.md rule 3) -- no consult_coworker_agent
capability exists in this loop's tool set, so there is nothing here that
could recurse into another agent even by mistake.

Scope deliberately narrowed for increment 1, beyond what
.relay/PHASE10-DESIGN.md itself required:
  - Anthropic only, no OpenAI/xAI/Perplexity fallback loop (unlike
    agent/research_agent.py's full 3-provider-shape split) -- replicating
    that entire branching for a first, still-unproven increment would be
    a lot of new surface for something that may still change shape.
  - No "run a single named script" tool (design doc section 1 allows it)
    -- the edit/test loop's actual need is covered by run_tests alone.
  - A hard, explicit denylist (_NEVER_WRITABLE_PATHS/_PREFIXES) on top of
    everything the design doc specified: the write path refuses to touch
    the files that implement Jarvis's own permission/checkpoint/CI
    machinery, regardless of task or confirmation. Neither design pass
    wired this into code -- CLAUDE.md's "never let a model's own
    judgment be the enforced safety boundary" rule means a tool that
    could edit its own gating logic needs a hard, unconditional deny, not
    just an instruction in a system prompt. Deliberately not exhaustive
    (a real, comprehensive security boundary is a larger, separate
    effort) -- narrow and explicit, matching increment 1's own scope.
"""
import os
import re
import subprocess
import time
from dataclasses import dataclass, field
from typing import List, Optional

from agent import model_router, provider_health
from agent.autonomy import Decision, ExecutionContext, should_request_confirmation
from agent.canonical_suite import canonical_suite_command
from agent.agents.base import Agent, AgentMetadata
from agent.agents.models import AgentResult
from agent.audit import log_action
from agent.cancellation import cancellation_requested
from agent.chat import anthropic_client
from agent.coding_checkpoint import (
    Checkpoint,
    CheckpointError,
    CheckpointRestoreFailed,
    PathOutsideRepository,
    changed_paths_since,
    confine_to_repo,
    create_checkpoint,
    existed_at_checkpoint,
    prune_checkpoints,
    restore_paths,
)
from agent.request_context import RequestContext
from agent.task_classifier import TaskType
from agent.usage import check_request_limits, record_llm_usage
from config.settings import settings

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

MAX_ITERATIONS = settings.max_agent_iterations
_TEST_SUITE_TIMEOUT_SECONDS = 120
# Per-call override for the model API request itself (see the call site
# in _run_coding_loop for why this differs from the shared client's
# default) -- generous enough for a slow generation over a large,
# file-content-heavy context, still well short of a single call being
# able to consume the whole coding_agent_timeout_seconds budget on its
# own (300s default, up to MAX_ITERATIONS calls plus the final suite run
# still need to fit).
_MODEL_CALL_TIMEOUT_SECONDS = 120
# research_agent.py's own 4096 is tuned for synthesizing a written answer,
# not for write_file's "reproduce the complete new file content" shape --
# rewriting a moderately large existing file plus a real addition can
# genuinely need more than 4096 output tokens. Found for real by
# dogfooding: a call that needed to reproduce a ~450-line test file's
# content was cut off at exactly 4096 output tokens (stop_reason
# "max_tokens", not a natural finish). Raised, not removed -- still a
# real ceiling, just sized for this tool's actual shape.
_MODEL_MAX_OUTPUT_TOKENS = 8192

# Safety/permission/CI machinery -- never writable by this agent, no
# matter what the task asks for or what autonomy level is in effect. A
# real, hard, unconditional deny (checked in code, in write_file itself)
# rather than only a system-prompt instruction a model could be talked
# out of. Not exhaustive; see module docstring.
#
# tests/__init__.py is in this set for a specific reason, not just "it's
# a test file": it's the file whose only job is calling
# tests._safety.install_test_safety() -- the redirect that keeps every
# run_tests call (both the model's own mid-loop calls and CodingAgent's
# own mandatory final verification) off real production paths and the
# real Keychain. Overwriting it would silently disarm that sandbox for
# the rest of the run, from inside a run that still believes it's
# sandboxed -- reproducing exactly the incident CLAUDE.md documents as
# having happened for real. Found by review, not by a live incident here.
_NEVER_WRITABLE_PATHS = frozenset({
    "agent/autonomy.py",
    "agent/verification.py",
    "agent/coding_checkpoint.py",
    "agent/agents/coding.py",
    "agent/agents/manager.py",
    "agent/agents/worker.py",
    "tools/registry.py",
    "tests/_safety.py",
    "tests/__init__.py",
    "config/settings.py",
})
_NEVER_WRITABLE_PREFIXES = (".github/workflows/",)
# Every entry above, pre-lowercased once -- see _is_never_writable's own
# docstring for why the comparison itself must be case-insensitive.
_NEVER_WRITABLE_PATHS_LOWER = frozenset(path.lower() for path in _NEVER_WRITABLE_PATHS)
_NEVER_WRITABLE_PREFIXES_LOWER = tuple(prefix.lower() for prefix in _NEVER_WRITABLE_PREFIXES)


def _is_never_writable(rel: str) -> bool:
    """Case-insensitive on purpose: confine_to_repo resolves via
    os.path.realpath, which does not correct case, but this Mac's
    default APFS volume (and macOS's default generally) is
    case-insensitive-but-case-preserving -- 'Agent/Autonomy.py' and
    'agent/autonomy.py' are the SAME file on disk even though they are
    different strings. A plain `rel in _NEVER_WRITABLE_PATHS` check is
    silently bypassable by asking write_file for a differently-cased
    path to a protected file; verified directly (confine_to_repo really
    does return the caller-supplied casing unchanged, and the real
    filesystem really does resolve both to the same inode) before this
    existed. Found by review, not a live incident."""
    lowered = rel.lower()
    return lowered in _NEVER_WRITABLE_PATHS_LOWER or any(
        lowered.startswith(prefix) for prefix in _NEVER_WRITABLE_PREFIXES_LOWER
    )

SYSTEM_PROMPT = (
    "You are a focused coding assistant working inside a single git repository. "
    "You have exactly three tools: read_file, write_file, and run_tests -- nothing else. "
    "You cannot install dependencies, access the network, run arbitrary shell commands, "
    "or touch git directly. Read whatever files you need to understand the task, make "
    "the smallest change that correctly addresses it, and run the test suite to check "
    "your work before you consider yourself done. If tests fail, read the failure, fix "
    "it, and run the tests again. When you are finished, reply with a short, plain-text "
    "summary of what you changed and why -- do not call any more tools once you are done.\n\n"
    "This project's tests are ALWAYS unittest.TestCase subclasses with test_* methods, "
    "never bare pytest-style functions with a plain assert -- python -m unittest discover "
    "does not collect the latter at all, so a test written that way would silently never "
    "run, and run_tests reporting success would not actually mean your new test was "
    "exercised. If you add a test, write it as a method on a unittest.TestCase subclass, "
    "matching whatever existing test file it belongs near."
)

TOOLS = [
    {
        "name": "read_file",
        "description": "Read the current text content of a file in this repository.",
        "input_schema": {
            "type": "object",
            "properties": {"path": {"type": "string", "description": "Repository-relative file path."}},
            "required": ["path"],
        },
    },
    {
        "name": "write_file",
        "description": (
            "Write the complete new text content of a file in this repository. Overwrites "
            "the file if it exists, creates it (and any needed parent directories) if not."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Repository-relative file path."},
                "content": {"type": "string", "description": "The complete new content of the file."},
            },
            "required": ["path", "content"],
        },
    },
    {
        "name": "run_tests",
        "description": (
            "Run this project's own canonical test suite and report pass/fail counts. Use "
            "this after making a change to check whether it works, and again before finishing."
        ),
        "input_schema": {"type": "object", "properties": {}},
    },
]

_TESTS_RUN_PATTERN = re.compile(r"Ran (\d+) tests? in")
_FAILED_COUNTS_PATTERN = re.compile(r"=(\d+)")


def _parse_test_summary(output: str):
    """Best-effort parse of unittest's own text summary -- tests_run/
    tests_failed are informational metadata, never the authoritative
    pass/fail signal (that is always suite_exit_code, a real process
    return code, not text parsing). Never raises; a summary line
    unittest's own output format doesn't match just yields None."""
    tests_run = None
    match = _TESTS_RUN_PATTERN.search(output)
    if match:
        tests_run = int(match.group(1))

    tests_failed = None
    if re.search(r"^OK$", output, re.MULTILINE):
        tests_failed = 0
    else:
        failed_match = re.search(r"FAILED \(([^)]*)\)", output)
        if failed_match:
            tests_failed = sum(int(n) for n in _FAILED_COUNTS_PATTERN.findall(failed_match.group(1)))

    return tests_run, tests_failed


def _run_test_suite(repo_root: str) -> dict:
    """CodingAgent's own final verification step. Cannot be QAAgent's
    existing helper of the same shape: MAX_AGENT_DEPTH=1 means
    CodingAgent can never call QAAgent, so this necessarily runs its own
    copy of the subprocess call -- but the command itself comes from
    agent.canonical_suite.canonical_suite_command, the one place the
    actual flags (`-t .` above all) are defined, so this copy and
    QAAgent's own can no longer diverge on that."""
    try:
        result = subprocess.run(
            canonical_suite_command() + ["-v"],
            capture_output=True, text=True, timeout=_TEST_SUITE_TIMEOUT_SECONDS, cwd=repo_root,
        )
    except subprocess.TimeoutExpired:
        return {
            "suite_exit_code": None, "tests_run": None, "tests_failed": None,
            "summary": "The test suite did not finish within the timeout.", "raw_tail": "",
        }

    output = (result.stdout or "") + (result.stderr or "")
    tests_run, tests_failed = _parse_test_summary(output)
    tail = "\n".join(output.strip().splitlines()[-8:])
    summary = "All tests passed." if result.returncode == 0 else "One or more tests failed."
    return {
        "suite_exit_code": result.returncode, "tests_run": tests_run, "tests_failed": tests_failed,
        "summary": summary, "raw_tail": tail,
    }


_TEST_FILE_PATTERN = re.compile(r"^tests/test_[^/]+\.py$")


def _collected_test_count(repo_root: str, basename: str) -> Optional[int]:
    """Runs exactly one file through the SAME discovery mechanism
    _run_test_suite uses (`-p <basename>`, still `-s tests -t .`), so
    whatever it reports is what the real canonical suite would actually
    collect from that file -- not a separate, could-drift
    reimplementation of unittest's own collection rules. Returns None if
    the check itself couldn't run (treated permissively -- an
    infrastructure problem with this EXTRA check must never block or
    misreport a real success/failure verdict the main suite run already
    established)."""
    try:
        result = subprocess.run(
            canonical_suite_command(pattern=basename),
            capture_output=True, text=True, timeout=_TEST_SUITE_TIMEOUT_SECONDS, cwd=repo_root,
        )
    except subprocess.TimeoutExpired:
        return None
    tests_run, _ = _parse_test_summary((result.stdout or "") + (result.stderr or ""))
    return tests_run


def _new_test_files_collecting_nothing(checkpoint: Checkpoint, changed_paths: List[str]) -> List[str]:
    """The real, dogfooding-found gap this exists to close: a brand-new
    test file using the wrong convention (bare assert + a plain function,
    not this project's unittest.TestCase) is collected by nothing --
    python -m unittest discover silently skips it, suite_exit_code stays
    0, and nothing else in this module's own verification would ever
    notice. Only checks paths that (a) look like a test file, (b) did
    NOT exist at checkpoint time (an existing file being edited is
    already covered by ordinary discovery finding its class), and (c)
    still exist on disk (a deleted file has nothing to collect, by
    design). Genuinely open, not solved by this alone: this catches
    "collects zero tests," not "collects fewer tests than the task
    actually needed" -- see .relay/PHASE10-DESIGN.md's own discussion for
    why a stronger check was left for a future pass rather than guessed
    at here."""
    flagged = []
    for rel in changed_paths:
        if not _TEST_FILE_PATTERN.match(rel):
            continue
        abs_path = os.path.join(checkpoint.repo_root, rel)
        if not os.path.isfile(abs_path):
            continue
        if existed_at_checkpoint(checkpoint, rel):
            continue
        collected = _collected_test_count(checkpoint.repo_root, os.path.basename(rel))
        if collected == 0:
            flagged.append(rel)
    return flagged


def _read_file(repo_root: str, path: str) -> str:
    try:
        rel = confine_to_repo(repo_root, path)
    except PathOutsideRepository as error:
        return f"Error: {error}"
    abs_path = os.path.join(repo_root, rel)
    if not os.path.isfile(abs_path):
        return f"Error: '{rel}' does not exist or is not a regular file."
    try:
        with open(abs_path, "r", errors="surrogateescape") as file:
            return file.read()
    except OSError as error:
        return f"Error: could not read '{rel}': {error}"


# CodingAgent's write path is not, and must never become, a model-
# callable registered tool (see module docstring on why this is a
# dedicated internal loop) -- but it is still a real, permission-
# relevant action, and "modifies files/executes code" is exactly
# tools/registry.py's own LEVEL_NAMES[2], the same classification
# run_python already has. Passed explicitly to should_request_
# confirmation's permission_level override (M10.0) rather than reusing
# any registered tool's name.
_WRITE_FILE_PERMISSION_LEVEL = 2


def _write_file(repo_root: str, path: str, content: str, files_written: List[str], context: RequestContext) -> str:
    try:
        rel = confine_to_repo(repo_root, path)
    except PathOutsideRepository as error:
        return f"Error: {error}"
    if _is_never_writable(rel):
        return (
            f"Error: refusing to write to '{rel}' -- this path is part of Jarvis's own "
            "safety/permission/CI machinery and is never writable by CodingAgent, "
            "regardless of the task."
        )

    # M10.0: the same permission-level-vs-autonomy decision agent/
    # executor.py's _run_tool already applies to every registered tool,
    # through the SAME function -- not a second, independently-written
    # copy of this logic. source="agent_worker" (set by agent/agents/
    # worker.py for every coworker-agent subprocess) means a verdict
    # that would otherwise mean "pause and ask" correctly becomes DENY
    # instead of hanging forever with no live person to answer it.
    decision = should_request_confirmation(
        "coding_agent_write_file", context.autonomy_level,
        ExecutionContext(source=context.source), permission_level=_WRITE_FILE_PERMISSION_LEVEL,
    )
    if decision != Decision.ALLOW:
        return (
            f"Error: refusing to write to '{rel}' -- not permitted at the current autonomy "
            f"level ({decision.value})."
        )

    abs_path = os.path.join(repo_root, rel)
    try:
        os.makedirs(os.path.dirname(abs_path), exist_ok=True)
        with open(abs_path, "w") as file:
            file.write(content)
    except OSError as error:
        return f"Error: could not write '{rel}': {error}"
    if rel not in files_written:
        files_written.append(rel)
    return f"Wrote {len(content)} character(s) to '{rel}'."


def _run_internal_tool(
    name: str, tool_input: dict, repo_root: str, files_written: List[str], context: RequestContext,
) -> str:
    if name == "read_file":
        result = _read_file(repo_root, tool_input.get("path", ""))
    elif name == "write_file":
        result = _write_file(
            repo_root, tool_input.get("path", ""), tool_input.get("content", ""), files_written, context,
        )
    elif name == "run_tests":
        outcome = _run_test_suite(repo_root)
        result = f"{outcome['summary']} (exit code {outcome['suite_exit_code']})\n{outcome['raw_tail']}"
    else:
        result = f"Unknown tool: {name}"

    # Every file this agent reads, writes, or test-runs it attempts goes
    # through the audit log -- same reasoning agent/research_agent.py's
    # own _run_tool gives for logging every page it visits, not just the
    # fact that CodingAgent ran.
    log_action(f"coding_agent:{name}", tool_input, result)
    return result


@dataclass
class _LoopOutcome:
    summary: str
    files_written: List[str] = field(default_factory=list)
    iterations: int = 0
    cancelled: bool = False


def _run_coding_loop(task: str, context: RequestContext, checkpoint: Checkpoint) -> _LoopOutcome:
    """This module's own model-calling loop -- see module docstring for
    why this is a dedicated internal loop rather than
    agent.claude_gateway.invoke(). Anthropic only (see module docstring's
    scope note); a live-provider failure here is not retried, it
    propagates to execute()'s own try/except as a clean failure."""
    messages = [{"role": "user", "content": task}]
    files_written: List[str] = []
    request_id = context.request_id
    model = model_router.primary_choice().model

    for iteration in range(1, MAX_ITERATIONS + 1):
        if cancellation_requested(request_id):
            return _LoopOutcome(
                summary="Cancelled while editing.", files_written=files_written,
                iterations=iteration, cancelled=True,
            )

        limit_check = check_request_limits(request_id)
        if limit_check.exceeded:
            return _LoopOutcome(
                summary=f"Stopping here -- {limit_check.reason}",
                files_written=files_written, iterations=iteration,
            )

        call_start = time.time()
        try:
            response = anthropic_client.messages.create(
                model=model, max_tokens=_MODEL_MAX_OUTPUT_TOKENS, system=SYSTEM_PROMPT, tools=TOOLS, messages=messages,
                # Overrides the shared client's default (config.settings.
                # api_read_timeout, 25s -- fine for a typical chat/research
                # turn) for this call only. Found for real by dogfooding:
                # this loop's own non-streaming calls (same pattern
                # agent/research_agent.py already uses) accumulate real
                # file content into `messages` across iterations, and a
                # larger input context genuinely takes longer to fully
                # generate a response for before anything comes back --
                # two consecutive real dogfood runs hit a real
                # APITimeoutError at the shared 25s default. Scoped to
                # this call only, not a change to the shared client every
                # other caller (chat, ResearchAgent, MemoryAgent) uses.
                timeout=_MODEL_CALL_TIMEOUT_SECONDS,
            )
        except Exception:
            provider_health.record_failure("anthropic")
            raise
        provider_health.clear_failure("anthropic")

        usage = getattr(response, "usage", None)
        if usage is not None:
            record_llm_usage(
                provider="anthropic", model=model, operation="coding",
                request_id=request_id, agent="coding",
                input_tokens=getattr(usage, "input_tokens", 0), output_tokens=getattr(usage, "output_tokens", 0),
                duration_seconds=time.time() - call_start, task_type=TaskType.CODING.value, fallback_position=0,
            )

        if response.stop_reason == "max_tokens":
            # A truncated response is NOT the model deciding it's done --
            # found for real by dogfooding: this loop's write_file
            # requires the complete new file content every call, so
            # rewriting a moderately large file can genuinely need more
            # output than the cap allows, and a response cut off mid-way
            # through composing a tool_use block previously fell through
            # to the same branch as a clean "end_turn" finish, silently
            # reporting "Done." for a call that never actually finished.
            raise RuntimeError(
                f"model response was truncated at the {_MODEL_MAX_OUTPUT_TOKENS}-token output "
                f"cap on iteration {iteration} -- not a clean finish, not treated as one"
            )

        if response.stop_reason != "tool_use":
            text = "".join(block.text for block in response.content if block.type == "text").strip()
            return _LoopOutcome(summary=text or "Done.", files_written=files_written, iterations=iteration)

        messages.append({"role": "assistant", "content": response.content})

        tool_results = []
        for block in response.content:
            if block.type == "tool_use":
                result_text = _run_internal_tool(
                    block.name, block.input, checkpoint.repo_root, files_written, context,
                )
                tool_results.append({"type": "tool_result", "tool_use_id": block.id, "content": str(result_text)})
        messages.append({"role": "user", "content": tool_results})

    return _LoopOutcome(
        summary="Reached the iteration limit before finishing.",
        files_written=files_written, iterations=MAX_ITERATIONS,
    )


class CodingAgent(Agent):

    @property
    def metadata(self) -> AgentMetadata:
        return AgentMetadata(
            name="coding",
            description=(
                "Fixes errors, adds features, and refactors code inside this repository. "
                "Off by default (config.settings.coding_agent_enabled) -- when disabled, "
                "defers to the ordinary executor exactly as before."
            ),
            capabilities=["coding", "debugging", "refactoring", "software engineering"],
            supported_task_types=["coding"],
        )

    def execute(self, task: str, context: RequestContext) -> AgentResult:
        start = time.time()

        if not settings.coding_agent_enabled:
            return AgentResult(
                success=True, agent_name=self.metadata.name, request_id=context.request_id,
                result="", duration_seconds=time.time() - start,
                metadata={"deferred_to_executor": True},
            )

        try:
            checkpoint = create_checkpoint(context.request_id, repo_root=_PROJECT_ROOT)
        except CheckpointError as error:
            return AgentResult(
                success=False, agent_name=self.metadata.name, request_id=context.request_id, result="",
                error=f"could not checkpoint the repository before starting: {error}",
                duration_seconds=time.time() - start,
            )

        def _finalize(result: AgentResult) -> AgentResult:
            # Runs on every exit from here on, success or failure alike --
            # bounds how many refs/jarvis/checkpoints/* accumulate in the
            # real repo over time (config.settings.
            # coding_checkpoint_retention_count). Housekeeping only: a
            # prune failure is logged by prune_checkpoints itself when it
            # succeeds, and here is deliberately swallowed rather than
            # allowed to override the actual task's own outcome -- a
            # request that otherwise succeeded (or failed and correctly
            # rolled back) must not be reported as a failure just because
            # cleanup of OLDER, unrelated checkpoints hit a problem.
            try:
                prune_checkpoints(settings.coding_checkpoint_retention_count, repo_root=checkpoint.repo_root)
            except CheckpointError:
                pass
            return result

        if checkpoint.dirty_at_checkpoint:
            # The one rule this whole design rests on (see
            # .relay/PHASE10-DESIGN.md section 3): if the tree already had
            # uncommitted changes this agent did not make, do not touch
            # it at all -- not "don't roll back," don't proceed.
            return _finalize(AgentResult(
                success=False, agent_name=self.metadata.name, request_id=context.request_id, result="",
                error=(
                    "the working tree already had uncommitted changes before this task started "
                    f"({', '.join(checkpoint.dirty_paths_at_checkpoint)}); refusing to make any "
                    "edit to avoid any risk of losing that work"
                ),
                duration_seconds=time.time() - start,
                metadata={"checkpoint_ref": checkpoint.ref, "dirty_at_checkpoint": True},
            ))

        try:
            outcome = _run_coding_loop(task, context, checkpoint)

            if outcome.cancelled:
                return _finalize(AgentResult(
                    success=False, agent_name=self.metadata.name, request_id=context.request_id,
                    result=outcome.summary, cancelled=True, error="cancelled while editing",
                    duration_seconds=time.time() - start,
                    metadata={"checkpoint_ref": checkpoint.ref, "files_written": outcome.files_written},
                ))

            test_result = _run_test_suite(checkpoint.repo_root)
            changed_paths = changed_paths_since(checkpoint)
            metadata = {
                "checkpoint_ref": checkpoint.ref,
                "files_written": outcome.files_written,
                "changed_paths": changed_paths,
                "suite_exit_code": test_result["suite_exit_code"],
                "tests_run": test_result["tests_run"],
                "tests_failed": test_result["tests_failed"],
                "iterations": outcome.iterations,
            }

            if test_result["suite_exit_code"] is None:
                # Couldn't determine whether the change actually works --
                # uncertain, not a clean pass or fail. Never auto-rollback
                # an uncertain outcome; the checkpoint ref persists either
                # way so a human can still decide (section 3).
                return _finalize(AgentResult(
                    success=False, agent_name=self.metadata.name, request_id=context.request_id,
                    result=f"{outcome.summary}\n\nCould not verify: {test_result['summary']}",
                    duration_seconds=time.time() - start, metadata=metadata,
                ))

            # Rollback scope is deliberately narrower than changed_paths:
            # changed_paths_since is a pure tree diff, so it cannot tell
            # a concurrent, unrelated process's edit apart from this
            # agent's own. Restoring the FULL diff would silently
            # discard someone else's real work if anything else wrote to
            # this repo during this task's own run -- a real possibility
            # this project explicitly designs around (relay mode's own
            # premise is a second, concurrent Claude Code session in the
            # same working tree), and exactly the same "never discard
            # work this agent didn't cause" principle checkpoint.dirty_
            # at_checkpoint already enforces for changes that predate the
            # checkpoint, extended here to changes DURING the task.
            # Intersecting with the agent's own self-reported
            # files_written keeps changed_paths_since as the
            # authoritative "did this really change" check (self-report
            # alone was already rejected as circular -- see
            # .relay/PHASE10-DESIGN.md section 7) while never restoring a
            # path the agent didn't itself claim to write. Found by
            # review, not a live incident.
            rollback_candidates = [path for path in changed_paths if path in outcome.files_written]

            def _attempt_rollback() -> str:
                if not rollback_candidates:
                    metadata["rolled_back"] = False
                    if changed_paths:
                        return (
                            " Every changed path belongs to something other than this agent's own "
                            "edits (a concurrent process, most likely) -- nothing safe to roll back."
                        )
                    return (
                        " No files were actually changed, so there is nothing to roll back -- "
                        "this failure appears unrelated to this task."
                    )
                try:
                    restored = restore_paths(checkpoint, rollback_candidates)
                    metadata["rolled_back"] = True
                    metadata["restored_paths"] = restored
                    return f" Rolled back {len(restored)} changed file(s)."
                except CheckpointRestoreFailed as error:
                    metadata["rolled_back"] = False
                    metadata["rollback_error"] = str(error)
                    return f" Could NOT roll back automatically: {error}"

            if test_result["suite_exit_code"] != 0:
                rollback_note = _attempt_rollback()
                return _finalize(AgentResult(
                    success=False, agent_name=self.metadata.name, request_id=context.request_id,
                    result=f"{outcome.summary}\n\nTest suite failed after the change.{rollback_note}",
                    verification_status="failed", duration_seconds=time.time() - start, metadata=metadata,
                ))

            # The suite reports 0 failures -- but that alone isn't proof
            # a new test file the agent wrote is actually running. Found
            # for real by dogfooding: a file using the wrong test-writing
            # convention is silently never collected, suite_exit_code
            # stays 0, and nothing else here would ever notice.
            uncollected = _new_test_files_collecting_nothing(checkpoint, changed_paths)
            if uncollected:
                metadata["uncollected_test_files"] = uncollected
                rollback_note = _attempt_rollback()
                return _finalize(AgentResult(
                    success=False, agent_name=self.metadata.name, request_id=context.request_id,
                    result=(
                        f"{outcome.summary}\n\nThe test suite passed, but "
                        f"{', '.join(uncollected)} collects zero tests -- likely written in a "
                        f"convention python -m unittest discover doesn't pick up, so this wasn't "
                        f"actually verified.{rollback_note}"
                    ),
                    verification_status="failed", duration_seconds=time.time() - start, metadata=metadata,
                ))

            return _finalize(AgentResult(
                success=True, agent_name=self.metadata.name, request_id=context.request_id,
                result=outcome.summary, verification_status="passed",
                duration_seconds=time.time() - start, metadata=metadata,
            ))
        except Exception as error:
            # Never let an unexpected failure escape uncaught (Agent.
            # execute's own documented contract) -- and deliberately no
            # rollback attempt here: an unplanned mid-loop exception is
            # the same "more dangerous to restore a partial change than
            # to leave it and say so" case cancellation is (section 3).
            # The checkpoint ref still persists for manual recovery.
            return _finalize(AgentResult(
                success=False, agent_name=self.metadata.name, request_id=context.request_id, result="",
                error=f"{type(error).__name__}: {error}",
                duration_seconds=time.time() - start,
                metadata={"checkpoint_ref": checkpoint.ref},
            ))
