"""Tracks the mutable progress of a single agent-loop run -- distinct from
RequestContext (agent/request_context.py), which is the fixed information
the request started with and never changes. Exists so the loop's progress
is inspectable (for structured logging, and for the dashboard) instead of
being implicit in loop-local variables that disappear once the request
finishes.

confirmation_pending is now genuinely wired (Phase 4's autonomy engine,
agent/autonomy.py, sets it via request_confirmation()/clear_confirmation()
when a tool call needs the user's explicit yes before it actually runs).

Memory and tool-result tracking store IDs/previews and metadata, not full
content -- agent/memory/ already has the actual memory content, and the
full tool result text is already in the conversation's messages list;
duplicating either in full here would just be redundant, unbounded growth
for no benefit.
"""
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import List, Optional

MAX_PREVIEW_LENGTH = 160


class ExecutionStatus(str, Enum):
    IDLE = "idle"
    LISTENING = "listening"
    THINKING = "thinking"
    PLANNING = "planning"
    EXECUTING = "executing"
    WAITING_FOR_CONFIRMATION = "waiting_for_confirmation"
    VERIFYING = "verifying"
    SPEAKING = "speaking"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


# Explicit, testable transition graph -- an ExecutionState tracks ONE
# request's lifecycle, which always ends at exactly one of the three
# terminal states (no outgoing edges from any of them; a new request gets
# a fresh ExecutionState rather than reusing a finished one). LISTENING
# and SPEAKING are reachable here for completeness/shared vocabulary with
# JarvisState (agent/jarvis_state.py), but in practice executor.py's own
# request lifecycle only ever touches PLANNING/THINKING/EXECUTING/
# WAITING_FOR_CONFIRMATION/VERIFYING/COMPLETED/FAILED/CANCELLED --
# LISTENING/SPEAKING are typically set directly on JarvisState by the
# voice/menu-bar caller, which spans before and after a specific request.
_ALLOWED_TRANSITIONS = {
    ExecutionStatus.IDLE: {
        ExecutionStatus.LISTENING, ExecutionStatus.THINKING, ExecutionStatus.PLANNING,
    },
    ExecutionStatus.LISTENING: {
        ExecutionStatus.THINKING, ExecutionStatus.IDLE, ExecutionStatus.CANCELLED,
    },
    ExecutionStatus.PLANNING: {
        ExecutionStatus.THINKING, ExecutionStatus.CANCELLED, ExecutionStatus.FAILED,
    },
    ExecutionStatus.THINKING: {
        ExecutionStatus.EXECUTING, ExecutionStatus.WAITING_FOR_CONFIRMATION,
        ExecutionStatus.VERIFYING, ExecutionStatus.SPEAKING,
        ExecutionStatus.COMPLETED, ExecutionStatus.FAILED, ExecutionStatus.CANCELLED,
    },
    ExecutionStatus.EXECUTING: {
        ExecutionStatus.THINKING, ExecutionStatus.WAITING_FOR_CONFIRMATION,
        ExecutionStatus.VERIFYING, ExecutionStatus.COMPLETED,
        ExecutionStatus.FAILED, ExecutionStatus.CANCELLED,
    },
    ExecutionStatus.WAITING_FOR_CONFIRMATION: {
        ExecutionStatus.EXECUTING, ExecutionStatus.CANCELLED, ExecutionStatus.FAILED,
    },
    ExecutionStatus.VERIFYING: {
        ExecutionStatus.THINKING, ExecutionStatus.EXECUTING, ExecutionStatus.SPEAKING,
        ExecutionStatus.COMPLETED, ExecutionStatus.FAILED, ExecutionStatus.CANCELLED,
    },
    ExecutionStatus.SPEAKING: {ExecutionStatus.COMPLETED, ExecutionStatus.IDLE},
    ExecutionStatus.COMPLETED: set(),
    ExecutionStatus.FAILED: set(),
    ExecutionStatus.CANCELLED: set(),
}


def _preview(text: str, limit: int = MAX_PREVIEW_LENGTH) -> str:
    text = str(text)
    return text if len(text) <= limit else text[:limit] + "…"


@dataclass(frozen=True)
class ToolCallRecord:
    name: str
    result_preview: str
    ok: bool = True
    duration_seconds: Optional[float] = None


@dataclass(frozen=True)
class MemoryReference:
    """Why a given memory was pulled into context for this request --
    not the memory's content (look it up by memory_id via agent.memory
    if needed; it's never a secret, since the safety filter refuses those
    at write time, but there's no reason to duplicate it here)."""
    memory_id: str
    memory_type: str
    reason: str
    score: Optional[float] = None
    included: bool = True


@dataclass
class ExecutionState:
    max_iterations: int
    request_id: Optional[str] = None  # set by register_active() below
    status: ExecutionStatus = ExecutionStatus.IDLE
    iteration: int = 0
    tools_executed: List[str] = field(default_factory=list)
    tool_results: List[ToolCallRecord] = field(default_factory=list)
    memories_retrieved: List[MemoryReference] = field(default_factory=list)
    plan: Optional[object] = None  # agent.planner.Plan -- typed loosely to avoid a hard import here
    confirmation_pending: bool = False
    pending_confirmation_tool: Optional[str] = None
    confirmation_events: int = 0
    cancelled: bool = False
    failed: bool = False
    error: Optional[str] = None
    final_result: Optional[str] = None
    started_at: float = field(default_factory=time.time)
    finished_at: Optional[float] = None
    selected_provider: Optional[str] = None
    selected_model: Optional[str] = None
    # Phase 6.5: which capability layer handled this request (see
    # agent/delegation.py) -- "native_tool" for the ordinary, unmodified
    # path, "claude_skill" when a matched skill's instructions were
    # attached. selected_skill is only ever a name to look up in
    # agent.skills.registry, never the skill's own instructions text --
    # duplicating that here would be redundant, unbounded growth for no
    # benefit, the same reasoning MemoryReference already documents for
    # memory content.
    selected_skill: Optional[str] = None
    delegation_destination: Optional[str] = None
    # Phase 7: which coworker agent (see agent/agents/) this request was
    # routed to, if any -- distinct axis from delegation_destination the
    # same way agent.agents.router is distinct from agent.delegation
    # (see that module's docstring). active_agent/agent_task track the
    # CURRENTLY running agent (agent_task is a short preview, not the
    # agent's full input/output -- same bounded-preview convention as
    # ToolCallRecord/MemoryReference above); agents_used is the ordered
    # list of every agent name actually invoked during this request
    # (in practice at most one, since agents cannot recursively
    # delegate -- see agent.agents.manager.MAX_AGENT_DEPTH).
    active_agent: Optional[str] = None
    agent_task: Optional[str] = None
    agent_status: Optional[str] = None
    agents_used: List[str] = field(default_factory=list)
    # Phase 9 Milestone 3: bounded-parallel delegation (agent.agents.
    # manager.execute_agents_parallel) tracks MULTIPLE simultaneously
    # active coworkers, which the singular active_agent/agent_task/
    # agent_status fields above were never designed to represent.
    # Deliberately additive, not a replacement: a batch leaves
    # active_agent/agent_task/agent_status exactly as untouched as any
    # ordinary (non-coworker) tool call already leaves them (None unless
    # a single coworker is separately active via record_agent above), so
    # code written against those fields before this milestone keeps
    # seeing exactly what it always did. verification_status here is the
    # BATCH's combined outcome (agent.verification.verify_agent_result
    # applied across every subtask) -- distinct from AgentResult.
    # verification_status, which is one subtask's own.
    active_agents: List[str] = field(default_factory=list)
    completed_agents: List[str] = field(default_factory=list)
    failed_agents: List[str] = field(default_factory=list)
    parallel_batch_size: Optional[int] = None
    verification_status: Optional[str] = None

    def record_agent(self, agent_name: str, task_preview: str, status: str) -> None:
        self.active_agent = agent_name
        self.agent_task = _preview(task_preview)
        self.agent_status = status
        if agent_name not in self.agents_used:
            self.agents_used.append(agent_name)

    def clear_active_agent(self) -> None:
        self.active_agent = None
        self.agent_task = None
        self.agent_status = None

    def record_agent_batch_started(self, agent_names: List[str]) -> None:
        self.active_agents = list(agent_names)
        self.parallel_batch_size = len(agent_names)

    def record_agent_batch_finished(
        self, completed: List[str], failed: List[str], verification_status: Optional[str] = None,
    ) -> None:
        self.completed_agents = list(completed)
        self.failed_agents = list(failed)
        self.verification_status = verification_status
        self.active_agents = []
        for name in completed + failed:
            if name not in self.agents_used:
                self.agents_used.append(name)

    def transition_to(self, new_status: ExecutionStatus) -> bool:
        """Returns True if the transition was applied. A rejected
        (invalid) transition leaves status unchanged and returns False --
        deliberately never raises, since a bug in status bookkeeping must
        never be able to crash the actual request it's just observing."""
        if new_status == self.status:
            return True
        if new_status not in _ALLOWED_TRANSITIONS.get(self.status, set()):
            return False
        self.status = new_status
        return True

    def record_iteration(self) -> None:
        self.iteration += 1

    def record_tool(
        self,
        name: str,
        result_preview: Optional[str] = None,
        ok: bool = True,
        duration_seconds: Optional[float] = None,
    ) -> None:
        self.tools_executed.append(name)
        if result_preview is not None:
            self.tool_results.append(ToolCallRecord(
                name=name,
                result_preview=_preview(result_preview),
                ok=ok,
                duration_seconds=duration_seconds,
            ))
            # Only a call reporting a real outcome (a preview) counts as
            # completing a plan step -- a bare record_tool(name) is just
            # bookkeeping and shouldn't silently consume a step.
            if self.plan is not None:
                self.plan.advance(failed=not ok)

    def record_memory_retrieval(
        self,
        memory_id: str,
        memory_type: str,
        reason: str,
        score: Optional[float] = None,
        included: bool = True,
    ) -> None:
        self.memories_retrieved.append(MemoryReference(
            memory_id=memory_id, memory_type=memory_type, reason=reason,
            score=score, included=included,
        ))

    def record_model(self, provider: str, model: str) -> None:
        self.selected_provider = provider
        self.selected_model = model

    def request_confirmation(self, tool_name: str) -> None:
        self.confirmation_events += 1
        self.confirmation_pending = True
        self.pending_confirmation_tool = tool_name
        self.transition_to(ExecutionStatus.WAITING_FOR_CONFIRMATION)

    def clear_confirmation(self) -> None:
        self.confirmation_pending = False
        self.pending_confirmation_tool = None
        if self.status == ExecutionStatus.WAITING_FOR_CONFIRMATION:
            self.transition_to(ExecutionStatus.EXECUTING)

    def cancel(self) -> None:
        self.cancelled = True
        self.transition_to(ExecutionStatus.CANCELLED)

    def finish(self, result: Optional[str] = None, failed: bool = False, error: Optional[str] = None) -> None:
        self.finished_at = time.time()
        self.final_result = result
        self.failed = failed
        self.error = error
        # Don't overwrite an already-cancelled outcome -- cancel() already
        # set the correct terminal status, and CANCELLED has no outgoing
        # transitions, so this would be a no-op anyway; being explicit
        # here documents the intent rather than relying on that silently.
        if not self.cancelled:
            self.transition_to(ExecutionStatus.FAILED if failed else ExecutionStatus.COMPLETED)

    @property
    def duration_seconds(self) -> float:
        end = self.finished_at if self.finished_at is not None else time.time()
        return end - self.started_at


# --- In-flight execution registry --------------------------------------
#
# Lets a future "Jarvis, stop" mechanism (voice or otherwise -- this
# phase deliberately doesn't wire one up yet) look up a still-running
# request by its request_id and cancel it, without executor.py needing
# to expose or thread a callback through every layer. Cleared in
# execute_task_stream's finally block regardless of how the request ends.

_active: dict = {}


def register_active(request_id: str, state: "ExecutionState") -> None:
    state.request_id = request_id
    _active[request_id] = state


def unregister_active(request_id: str) -> None:
    _active.pop(request_id, None)


def get_active(request_id: str) -> Optional["ExecutionState"]:
    return _active.get(request_id)


def cancel_active(request_id: str) -> bool:
    state = _active.get(request_id)
    if state is None:
        return False
    state.cancel()
    return True


def list_active() -> List["ExecutionState"]:
    """For the dashboard's "current request" view -- typically empty (a
    request usually finishes, and unregisters itself, well before a human
    reloads the page), but genuinely live when non-empty. Only sees
    requests running in the same process calling this -- the Streamlit
    app and the menu-bar app are separate OS processes with separate
    memory, so this can't show a voice request's live state from the
    dashboard or vice versa."""
    return list(_active.values())
