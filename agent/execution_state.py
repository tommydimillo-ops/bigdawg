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
from typing import List, Optional

MAX_PREVIEW_LENGTH = 160


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
    iteration: int = 0
    tools_executed: List[str] = field(default_factory=list)
    tool_results: List[ToolCallRecord] = field(default_factory=list)
    memories_retrieved: List[MemoryReference] = field(default_factory=list)
    plan: Optional[object] = None  # agent.planner.Plan -- typed loosely to avoid a hard import here
    confirmation_pending: bool = False
    pending_confirmation_tool: Optional[str] = None
    cancelled: bool = False
    failed: bool = False
    error: Optional[str] = None
    final_result: Optional[str] = None
    started_at: float = field(default_factory=time.time)
    finished_at: Optional[float] = None
    selected_provider: Optional[str] = None
    selected_model: Optional[str] = None

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
        self.confirmation_pending = True
        self.pending_confirmation_tool = tool_name

    def clear_confirmation(self) -> None:
        self.confirmation_pending = False
        self.pending_confirmation_tool = None

    def cancel(self) -> None:
        self.cancelled = True

    def finish(self, result: Optional[str] = None, failed: bool = False, error: Optional[str] = None) -> None:
        self.finished_at = time.time()
        self.final_result = result
        self.failed = failed
        self.error = error

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
