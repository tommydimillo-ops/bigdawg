"""The Agent Manager -- a registry of coworker agents plus the one
function (route_and_execute) agent/executor.py calls to find out "who
should work on this," mirroring tools/registry.py's register/get/list
pattern instead of a growing if/elif chain keyed on agent name.

The Agent Manager does NOT replace the Executor. It decides which
specialist agent (if any) should handle a request; agent/executor.py
remains the only thing that actually dispatches a tool call, exactly the
same relationship agent/delegation.py already has with it (delegation
decides which capability layer applies and never touches a tool itself).

Two different execution shapes, both returned as AgentResult:
- ResearchAgent/MemoryAgent wrap an existing, already-safe, self-
  contained function (agent.research_agent.research /
  agent.memory_agent+agent.memory). Their result IS the final answer --
  agent/executor.py uses it directly instead of also paying for a full
  Claude/OpenAI tool-loop turn, which is a real cost reduction (Phase 7
  section 23's concern), not just an architectural nicety.
- CodingAgent/QAAgent do not run their own tool loop this phase (no
  unrestricted computer/shell access is allowed yet -- see their own
  docstrings). Their AgentResult carries metadata["deferred_to_executor"]
  = True and an empty `result`; agent/executor.py treats that as "just
  record which agent this was attributed to, then run the ordinary,
  completely unmodified execution path" -- the same way delegation.py's
  NATIVE_TOOL destination changes nothing about how a request executes.

Bounded to a maximum agent depth of 1 (Phase 7 section 24): an agent's
own execute() must never itself call route_and_execute -- enforced here,
not just by convention, via the `depth` guard below, so a bug in a
future agent can't create A -> B -> A recursion.

execute_agent() (Phase 8 part 4) is the separate, hardened function that
actually backs live execution -- see its own docstring below for why it's
distinct from route_and_execute rather than route_and_execute simply
calling it: route_and_execute's own test suite depends on registering
fake, in-process-only Agent instances (see tests/test_agents_manager.py),
which a subprocess spawned fresh from agent/agents/worker.py could never
see (that subprocess re-populates its registry from scratch via the real
agent.agents package, not this process's live _REGISTRY). Keeping the two
functions separate means route_and_execute stays exactly as fully
testable with fakes as it always was, while execute_agent -- reachable
only through tools/schemas/agents.py's consult_coworker_agent, the real
execution entry point -- gets genuine OS-level process isolation.

execute_agents_parallel() (Phase 9 Milestone 3) is a bounded caller ON
TOP of execute_agent, not a third dispatch path: it runs several
independent subtasks concurrently (each through its own execute_agent
call, its own subprocess, its own depth/registered/enabled/cancellation
checks) and returns one AgentBatchResult, reachable only through
tools/schemas/agents.py's delegate_parallel_tasks tool -- same
tool-registry-gated, permission-checked entry-point pattern as the
single-task path, never a bypass of it.
"""
import concurrent.futures
import json
import os
import subprocess
import sys
import time
from typing import Dict, List, Optional

from agent.agents.base import Agent, AgentMetadata
from agent.agents.models import AgentBatchItem, AgentBatchResult, AgentResult, AgentTaskRequest, BatchStatus
from agent.agents.router import AgentDestination, route
from agent.cancellation import cancellation_requested
from agent.observability import log_event
from agent.provider_budget import global_budget_status
from agent.request_context import RequestContext
from agent.usage import total_cost_for_request
from agent.verification import verify_agent_result
from config.settings import settings

MAX_AGENT_DEPTH = 1

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Phase 9 Milestone 3: how often _run_agent_subprocess wakes up to check
# for cooperative cancellation while a coworker subprocess is running,
# and how long a terminated (SIGTERM'd) subprocess gets to exit cleanly
# before being killed outright. Small, fixed constants, not settings --
# this is polling-loop granularity, not a policy a deployment would ever
# need to tune.
_CANCELLATION_POLL_SECONDS = 0.5
_CANCEL_GRACE_SECONDS = 3.0

_REGISTRY: Dict[str, Agent] = {}


def register(agent: Agent) -> None:
    name = agent.metadata.name
    if name in _REGISTRY:
        raise ValueError(f"Agent '{name}' is already registered")
    _REGISTRY[name] = agent


def unregister(name: str) -> None:
    _REGISTRY.pop(name, None)


def get(name: str) -> Optional[Agent]:
    return _REGISTRY.get(name)


def list_agents() -> List[AgentMetadata]:
    return [agent.metadata for agent in _REGISTRY.values()]


def available_agents() -> List[AgentMetadata]:
    return [m for m in list_agents() if m.enabled]


def clear() -> None:
    """Test-only reset -- mirrors agent.skills.registry.clear()."""
    _REGISTRY.clear()


_DESTINATION_TO_AGENT_NAME = {
    AgentDestination.CODING: "coding",
    AgentDestination.RESEARCH: "research",
    AgentDestination.QA: "qa",
    AgentDestination.MEMORY: "memory",
}


def route_and_execute(
    task: str, context: RequestContext, depth: int = 0,
) -> Optional[AgentResult]:
    """Returns None for DIRECT (nothing for the caller to do differently)
    or when no agent ends up running. Returns an AgentResult otherwise --
    check `.metadata.get("deferred_to_executor")` to tell a genuine
    short-circuit (use `.result` as the final answer) apart from an
    attribution-only routing (ignore `.result`, just note `.agent_name`)."""

    if depth >= MAX_AGENT_DEPTH:
        log_event(
            "agent_recursion_blocked", request_id=context.request_id,
            component="agents", level="error", depth=depth,
        )
        return None

    decision = route(task)
    log_event(
        "agent_selected", request_id=context.request_id, component="agents",
        destination=decision.destination.value, confidence=round(decision.confidence, 2),
        reason=decision.reason,
    )

    if decision.destination == AgentDestination.DIRECT:
        return None

    agent_name = _DESTINATION_TO_AGENT_NAME[decision.destination]
    agent = _REGISTRY.get(agent_name)

    if agent is None:
        log_event(
            "agent_unknown", request_id=context.request_id, component="agents",
            level="warning", agent_name=agent_name,
        )
        return None

    if not agent.metadata.enabled:
        log_event(
            "agent_disabled", request_id=context.request_id, component="agents",
            level="warning", agent_name=agent_name,
        )
        return None

    if not agent.can_handle(task):
        log_event(
            "agent_declined", request_id=context.request_id, component="agents",
            level="warning", agent_name=agent_name,
        )
        return None

    if cancellation_requested(context.request_id):
        log_event("agent_cancelled", request_id=context.request_id, component="agents", agent_name=agent_name)
        return AgentResult(
            success=False, agent_name=agent_name, request_id=context.request_id,
            result="", cancelled=True, error="cancelled before the agent started",
        )

    log_event("agent_started", request_id=context.request_id, component="agents", agent_name=agent_name)
    start = time.time()

    try:
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(agent.execute, task, context)
            try:
                result = future.result(timeout=settings.agent_timeout_seconds)
            except concurrent.futures.TimeoutError:
                log_event(
                    "agent_timeout", request_id=context.request_id, component="agents",
                    level="error", agent_name=agent_name,
                    duration_seconds=round(time.time() - start, 2),
                )
                return AgentResult(
                    success=False, agent_name=agent_name, request_id=context.request_id,
                    result="", error="agent timed out",
                    duration_seconds=time.time() - start,
                )
    except Exception as error:
        log_event(
            "agent_failed", request_id=context.request_id, component="agents",
            level="error", agent_name=agent_name, error_type=type(error).__name__,
        )
        return AgentResult(
            success=False, agent_name=agent_name, request_id=context.request_id,
            result="", error=f"{type(error).__name__}: {error}",
            duration_seconds=time.time() - start,
        )

    log_event(
        "agent_completed", request_id=context.request_id, component="agents",
        agent_name=agent_name, success=result.success,
        duration_seconds=round(result.duration_seconds, 2),
    )
    return result


def _run_agent_subprocess(cmd, payload, timeout, cwd, request_id):
    """Phase 9 Milestone 3 -- a subprocess.run()-equivalent (same
    CompletedProcess return shape, same guaranteed SIGKILL if `timeout`
    is exceeded, never weakened) that additionally polls for cooperative
    cancellation (agent.cancellation.cancellation_requested) while
    waiting, so a parent request cancelled mid-flight can actually stop
    an already-launched coworker subprocess instead of only refusing to
    start new ones (execute_agent's pre-launch check below already
    covered that half). Graceful SIGTERM first with a bounded grace
    period, SIGKILL only if it doesn't exit in time -- this project's
    general "graceful before forced" preference (see agent/execution_
    state.py's cancellation philosophy). Every exit path -- normal
    completion, timeout, cancellation, or an unexpected exception -- reaps
    the process before returning, so no orphan/zombie can result.

    Uses Popen.communicate()'s documented retry-safe pattern (catching
    TimeoutExpired and calling communicate() again continues collecting
    output without losing any of it, per the stdlib's own docs) rather
    than manual pipe reads, specifically to avoid the classic deadlock
    risk of polling proc.poll()/proc.wait() without ever draining stdout/
    stderr while a child writes enough output to fill the OS pipe buffer.

    Returns (CompletedProcess, was_cancelled). Raises
    subprocess.TimeoutExpired on a real timeout -- identical contract to
    subprocess.run, so the caller's existing except clause didn't need
    to change."""
    proc = subprocess.Popen(
        cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, cwd=cwd,
    )
    start = time.time()
    remaining_input = payload

    try:
        while True:
            elapsed = time.time() - start
            if elapsed >= timeout:
                proc.kill()
                stdout, stderr = proc.communicate()
                raise subprocess.TimeoutExpired(cmd=cmd, timeout=timeout, output=stdout, stderr=stderr)

            slice_timeout = min(_CANCELLATION_POLL_SECONDS, timeout - elapsed)
            try:
                stdout, stderr = proc.communicate(input=remaining_input, timeout=slice_timeout)
                return subprocess.CompletedProcess(cmd, proc.returncode, stdout, stderr), False
            except subprocess.TimeoutExpired:
                # Already written (or attempted) on the first call --
                # communicate() only accepts `input` once per process.
                remaining_input = None

            if request_id and cancellation_requested(request_id):
                proc.terminate()
                try:
                    stdout, stderr = proc.communicate(timeout=_CANCEL_GRACE_SECONDS)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    stdout, stderr = proc.communicate()
                return subprocess.CompletedProcess(cmd, proc.returncode, stdout, stderr), True
    finally:
        # Defensive backstop only -- every explicit branch above already
        # reaps the process before returning/raising. This only matters
        # if something unexpected (e.g. a KeyboardInterrupt) unwinds the
        # loop some other way; it must still never leave a live child.
        if proc.poll() is None:
            proc.kill()
            proc.communicate()


def execute_agent(
    agent_name: str, task: str, context: RequestContext, depth: int = 0,
) -> AgentResult:
    """Phase 8 part 4 -- the real execution entry point, called by
    tools/schemas/agents.py's consult_coworker_agent tool for every live
    agent invocation. Runs the named agent in a fresh `python -m agent.
    agents.worker` subprocess via _run_agent_subprocess above (Phase 9
    Milestone 3: Popen-based, not subprocess.run, specifically so a
    cancelled parent request can terminate an already-running subprocess,
    not just refuse to start a new one) and enforces
    settings.agent_timeout_seconds, which still SIGKILLs the child (and
    everything it's doing) if it doesn't finish in time -- a genuine,
    OS-level guarantee, unlike a ThreadPoolExecutor future timing out
    while its underlying thread keeps running. That timeout guarantee is
    unchanged by the Milestone 3 cancellation polling -- see
    _run_agent_subprocess's own docstring.

    Always returns an AgentResult (never None, unlike route_and_execute)
    -- every caller of this function already knows a specific, concrete
    agent should run; there's no DIRECT/no-match case to represent here."""

    if depth >= MAX_AGENT_DEPTH:
        log_event(
            "agent_recursion_blocked", request_id=context.request_id,
            component="agents", level="error", agent_name=agent_name, depth=depth,
        )
        return AgentResult(
            success=False, agent_name=agent_name, request_id=context.request_id,
            result="", error="blocked: maximum agent depth exceeded",
        )

    agent = _REGISTRY.get(agent_name)
    if agent is None:
        log_event(
            "agent_unknown", request_id=context.request_id, component="agents",
            level="warning", agent_name=agent_name,
        )
        return AgentResult(
            success=False, agent_name=agent_name, request_id=context.request_id,
            result="", error=f"'{agent_name}' is not registered",
        )
    if not agent.metadata.enabled:
        log_event(
            "agent_disabled", request_id=context.request_id, component="agents",
            level="warning", agent_name=agent_name,
        )
        return AgentResult(
            success=False, agent_name=agent_name, request_id=context.request_id,
            result="", error=f"'{agent_name}' is currently disabled",
        )

    if cancellation_requested(context.request_id):
        log_event("agent_cancelled", request_id=context.request_id, component="agents", agent_name=agent_name)
        return AgentResult(
            success=False, agent_name=agent_name, request_id=context.request_id,
            result="", cancelled=True, error="cancelled before the agent started",
        )

    log_event("agent_started", request_id=context.request_id, component="agents", agent_name=agent_name, isolated=True)
    start = time.time()

    payload = json.dumps({
        "agent_name": agent_name,
        "task": task,
        "request_id": context.request_id,
        "autonomy_level": context.autonomy_level,
    })

    # CodingAgent (Phase 10 increment 1) gets its own, much longer budget
    # -- a real edit/test iteration loop plus a full canonical suite run
    # doesn't fit in the shared default every other coworker agent's
    # typical call comfortably meets. Every other agent's timeout is
    # completely unchanged.
    timeout_seconds = (
        settings.coding_agent_timeout_seconds if agent_name == "coding" else settings.agent_timeout_seconds
    )

    try:
        completed, cancelled_midflight = _run_agent_subprocess(
            [sys.executable, "-m", "agent.agents.worker"],
            payload, timeout_seconds, _PROJECT_ROOT, context.request_id,
        )
    except subprocess.TimeoutExpired:
        log_event(
            "agent_timeout", request_id=context.request_id, component="agents",
            level="error", agent_name=agent_name,
            duration_seconds=round(time.time() - start, 2),
        )
        return AgentResult(
            success=False, agent_name=agent_name, request_id=context.request_id,
            result="", error="agent timed out", duration_seconds=time.time() - start,
        )

    duration = time.time() - start

    if cancelled_midflight:
        log_event(
            "agent_cancelled", request_id=context.request_id, component="agents",
            agent_name=agent_name, duration_seconds=round(duration, 2), midflight=True,
        )
        return AgentResult(
            success=False, agent_name=agent_name, request_id=context.request_id,
            result="", cancelled=True, error="cancelled while running", duration_seconds=duration,
        )

    if completed.returncode != 0:
        log_event(
            "agent_failed", request_id=context.request_id, component="agents",
            level="error", agent_name=agent_name, returncode=completed.returncode,
            stderr_preview=(completed.stderr or "")[:500],
        )
        return AgentResult(
            success=False, agent_name=agent_name, request_id=context.request_id,
            result="", error=f"agent process exited with code {completed.returncode}",
            duration_seconds=duration,
        )

    try:
        data = json.loads(completed.stdout)
    except (json.JSONDecodeError, TypeError) as error:
        log_event(
            "agent_failed", request_id=context.request_id, component="agents",
            level="error", agent_name=agent_name, error_type=type(error).__name__,
        )
        return AgentResult(
            success=False, agent_name=agent_name, request_id=context.request_id,
            result="", error="agent process returned an unreadable result",
            duration_seconds=duration,
        )

    if "error" in data and "success" not in data:
        return AgentResult(
            success=False, agent_name=agent_name, request_id=context.request_id,
            result="", error=data["error"], duration_seconds=duration,
        )

    result = AgentResult(**data)
    log_event(
        "agent_completed", request_id=context.request_id, component="agents",
        agent_name=agent_name, success=result.success,
        duration_seconds=round(result.duration_seconds, 2), isolated=True,
    )
    return result


def _batch_too_large(tasks: List[AgentTaskRequest], context: RequestContext) -> Optional[AgentBatchResult]:
    if len(tasks) <= settings.max_parallel_agents:
        return None
    log_event(
        "agent_batch_rejected", request_id=context.request_id, component="agents",
        level="warning", requested=len(tasks), max_parallel_agents=settings.max_parallel_agents,
    )
    return AgentBatchResult(
        status=BatchStatus.FAILED, items=[], request_id=context.request_id,
        note=(
            f"Rejected: {len(tasks)} parallel subtasks requested, "
            f"exceeds the configured limit of {settings.max_parallel_agents}. "
            "No agent was started -- split this into smaller batches."
        ),
    )


def _run_one_batch_task(task_request: AgentTaskRequest, context: RequestContext, depth: int) -> AgentResult:
    return execute_agent(task_request.agent_name, task_request.task, context, depth=depth)


def execute_agents_parallel(
    tasks: List[AgentTaskRequest], context: RequestContext, depth: int = 0,
) -> AgentBatchResult:
    """Phase 9 Milestone 3 -- the bounded-parallel sibling of execute_agent:
    runs 2+ genuinely independent coworker-agent subtasks concurrently
    (each still through execute_agent's own subprocess isolation and
    genuine OS-level timeout -- this adds no second dispatch path, it's a
    bounded caller of the same one), collects their results, applies a
    small bounded retry to real per-task failures, and returns a single
    structured AgentBatchResult the orchestrator can act on.

    Deciding WHICH subtasks are independent enough to batch is the
    caller's job (today: the model, via delegate_parallel_tasks's tool
    description, which explicitly instructs it to only use this for
    order-independent work) -- what's enforced HERE, in code, not by
    model judgment, is the actual safety: the hard MAX_AGENT_DEPTH guard,
    the hard max_parallel_agents count ceiling (reject the whole batch
    rather than silently truncating it), a pre-flight global-budget check
    before spending anything on parallel calls, and cooperative
    cancellation between attempts. Never calls more than
    settings.max_parallel_agents agent subprocesses concurrently, and
    every subtask still goes through execute_agent's own registered/
    enabled/cancelled/depth checks independently -- this function adds a
    ceiling and coordination, it doesn't loosen anything execute_agent
    already enforces per task.
    """
    if depth >= MAX_AGENT_DEPTH:
        log_event(
            "agent_recursion_blocked", request_id=context.request_id,
            component="agents", level="error", depth=depth, batch_size=len(tasks),
        )
        return AgentBatchResult(
            status=BatchStatus.FAILED, items=[], request_id=context.request_id,
            note="blocked: maximum agent depth exceeded",
        )

    if not tasks:
        return AgentBatchResult(
            status=BatchStatus.FAILED, items=[], request_id=context.request_id,
            note="no subtasks were provided",
        )

    rejected = _batch_too_large(tasks, context)
    if rejected is not None:
        return rejected

    if cancellation_requested(context.request_id):
        log_event(
            "agent_batch_rejected", request_id=context.request_id, component="agents",
            level="warning", reason="cancelled_before_start", batch_size=len(tasks),
        )
        items = [
            AgentBatchItem(
                agent_name=t.agent_name, task_preview=t.task, required=t.required,
                result=AgentResult(
                    success=False, agent_name=t.agent_name, request_id=context.request_id,
                    result="", cancelled=True, error="cancelled before the batch started",
                ),
            )
            for t in tasks
        ]
        return AgentBatchResult(
            status=BatchStatus.FAILED, items=items, request_id=context.request_id,
            note="the request was cancelled before this batch started",
        )

    # Cost pre-flight (Phase 9 Milestone 3 / Milestone 2's budget system):
    # a single check against the GLOBAL daily ceiling before launching
    # several potentially-paid calls at once, not a reservation for each
    # one individually -- agent/provider_budget.py's own docstring
    # already documents why this project deliberately doesn't build a
    # full spend-reservation engine. This is the minimal safe addition:
    # refuse to START a batch that would obviously make an already-over-
    # budget day worse; it does not (and can't, without new per-call
    # accounting) stop an in-flight batch from finishing once started,
    # the same way M2's own per-request provider routing only checks
    # budget before picking a provider, not continuously during a call.
    if global_budget_status().over_limit:
        log_event(
            "agent_batch_rejected", request_id=context.request_id, component="agents",
            level="warning", reason="global_budget_exceeded", batch_size=len(tasks),
        )
        return AgentBatchResult(
            status=BatchStatus.FAILED, items=[], request_id=context.request_id,
            note="global daily budget is already exceeded; parallel delegation was skipped to avoid amplifying spend",
        )

    batch_start = time.time()
    cost_before = total_cost_for_request(context.request_id) if context.request_id else None

    log_event(
        "agent_batch_started", request_id=context.request_id, component="agents",
        batch_size=len(tasks), agent_names=[t.agent_name for t in tasks],
    )

    results: Dict[int, AgentResult] = {}
    retried_flags: Dict[int, bool] = {}

    with concurrent.futures.ThreadPoolExecutor(max_workers=len(tasks)) as executor:
        futures = {
            executor.submit(_run_one_batch_task, task_request, context, depth): index
            for index, task_request in enumerate(tasks)
        }
        for future in concurrent.futures.as_completed(futures):
            index = futures[future]
            try:
                results[index] = future.result()
            except Exception as error:  # defense-in-depth -- execute_agent itself never raises
                results[index] = AgentResult(
                    success=False, agent_name=tasks[index].agent_name, request_id=context.request_id,
                    result="", error=f"{type(error).__name__}: {error}",
                )
            retried_flags[index] = False

    # Bounded retry, applied sequentially and only to tasks whose FIRST
    # attempt actually failed (never a cancelled task -- retrying after
    # cancellation was requested would contradict "stop accepting new
    # coworker work"). Sequential, not another parallel round: keeps the
    # total concurrent-subprocess ceiling honest without needing a second,
    # separate bound just for retries.
    for index, task_request in enumerate(tasks):
        attempts = 0
        while (
            attempts < settings.max_agent_batch_retries
            and not results[index].success
            and not results[index].cancelled
            and not cancellation_requested(context.request_id)
        ):
            attempts += 1
            log_event(
                "agent_retry_started", request_id=context.request_id, component="agents",
                agent_name=task_request.agent_name, attempt=attempts,
            )
            results[index] = _run_one_batch_task(task_request, context, depth)
            retried_flags[index] = True

    items = [
        AgentBatchItem(
            agent_name=tasks[i].agent_name, task_preview=tasks[i].task,
            result=results[i], required=tasks[i].required, retried=retried_flags[i],
        )
        for i in range(len(tasks))
    ]

    verified = [(item, verify_agent_result(item.result)) for item in items]
    if all(v.ok for _, v in verified):
        status = BatchStatus.ALL_SUCCEEDED
    elif any(not v.ok for item, v in verified if item.required):
        status = BatchStatus.FAILED
    else:
        status = BatchStatus.PARTIAL

    cost_after = total_cost_for_request(context.request_id) if context.request_id else None
    cost_usd = (cost_after - cost_before) if (cost_before is not None and cost_after is not None) else None

    failed_names = [item.agent_name for item, v in verified if not v.ok]
    note = (
        "all subtasks succeeded" if status == BatchStatus.ALL_SUCCEEDED
        else f"failed/unverified: {', '.join(failed_names)}"
    )

    log_event(
        "agent_batch_completed" if status != BatchStatus.FAILED else "agent_batch_failed",
        request_id=context.request_id, component="agents",
        status=status.value, batch_size=len(tasks),
        duration_seconds=round(time.time() - batch_start, 2),
        retried=[tasks[i].agent_name for i in range(len(tasks)) if retried_flags[i]],
        cost_usd=cost_usd,
    )

    return AgentBatchResult(
        status=status, items=items, request_id=context.request_id,
        duration_seconds=time.time() - batch_start, cost_usd=cost_usd, note=note,
    )
