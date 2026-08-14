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
"""
import concurrent.futures
import json
import os
import subprocess
import sys
import time
from typing import Dict, List, Optional

from agent.agents.base import Agent, AgentMetadata
from agent.agents.models import AgentResult
from agent.agents.router import AgentDestination, route
from agent.cancellation import cancellation_requested
from agent.observability import log_event
from agent.request_context import RequestContext
from config.settings import settings

MAX_AGENT_DEPTH = 1

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

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


def execute_agent(
    agent_name: str, task: str, context: RequestContext, depth: int = 0,
) -> AgentResult:
    """Phase 8 part 4 -- the real execution entry point, called by
    tools/schemas/agents.py's consult_coworker_agent tool for every live
    agent invocation. Runs the named agent in a fresh `python -m agent.
    agents.worker` subprocess and enforces settings.agent_timeout_seconds
    via subprocess.run's own timeout, which SIGKILLs the child (and
    everything it's doing) if it doesn't finish in time -- a genuine,
    OS-level guarantee, unlike a ThreadPoolExecutor future timing out
    while its underlying thread keeps running.

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

    try:
        completed = subprocess.run(
            [sys.executable, "-m", "agent.agents.worker"],
            input=payload, capture_output=True, text=True,
            timeout=settings.agent_timeout_seconds, cwd=_PROJECT_ROOT,
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
