"""The AgentResult data model -- an immediate, structured outcome from a
single coworker-agent invocation. Distinct from agent.execution_history.
ExecutionRecord (the persistent, bounded history of past REQUESTS as a
whole, one row per request/response cycle) the same way agent.
verification.VerificationResult is distinct from ExecutionRecord: this is
what the caller inside execute_task_stream gets back immediately, not
what eventually gets persisted. agent/executor.py maps the fields it
cares about onto ExecutionState/ExecutionHistory itself; nothing here
writes to either.
"""
from dataclasses import dataclass, field
from enum import Enum
from typing import List, Optional


class AgentTaskType(str, Enum):
    """What kind of work a request was classified as -- matches
    agent.agents.router.AgentDestination's non-DIRECT values, kept as a
    separate (str, Enum) here rather than importing router's enum so
    agents.models has no dependency on agents.router (router depends on
    models, not the other way around -- mirrors agent.skills.models /
    agent.skills.router's existing one-way dependency)."""
    CODING = "coding"
    RESEARCH = "research"
    QA = "qa"
    MEMORY = "memory"


@dataclass(frozen=True)
class AgentResult:
    success: bool
    agent_name: str
    request_id: Optional[str]
    result: str
    error: Optional[str] = None
    duration_seconds: float = 0.0
    tools_used: List[str] = field(default_factory=list)
    model_used: Optional[str] = None
    provider_used: Optional[str] = None
    verification_status: Optional[str] = None
    cancelled: bool = False
    # Short, bounded, structured extras specific to one agent (e.g.
    # QAAgent's test pass/fail counts) -- never raw tool output or
    # anything unbounded; agent.execution_state.ExecutionState's own
    # docstring documents the same "IDs/previews, not full content"
    # convention this follows.
    metadata: dict = field(default_factory=dict)


@dataclass(frozen=True)
class AgentTaskRequest:
    """One requested subtask for a bounded-parallel delegation batch --
    the INPUT descriptor, as distinct from AgentBatchItem (the OUTCOME).
    required=False marks a subtask whose failure shouldn't sink the whole
    batch (agent.agents.manager.execute_agents_parallel's BatchStatus
    computation treats a failed non-required subtask as PARTIAL, not
    FAILED) -- defaults to True (safest assumption: treat every subtask
    as load-bearing unless the caller explicitly says otherwise)."""
    agent_name: str
    task: str
    required: bool = True


class BatchStatus(str, Enum):
    """Phase 9 Milestone 3's bounded-parallel-delegation outcome for a
    whole batch, distinct from any single AgentResult.success. ALL_SUCCEEDED
    means every subtask succeeded; PARTIAL means only optional (non-
    required) subtasks failed and the batch is still usable; FAILED means
    a required subtask failed and the configured retry bound didn't
    recover it, so the caller should not treat this batch's combined
    result as trustworthy."""
    ALL_SUCCEEDED = "all_succeeded"
    PARTIAL = "partial"
    FAILED = "failed"


@dataclass(frozen=True)
class AgentBatchItem:
    """One subtask's outcome within a batch -- pairs the (agent_name,
    task) that was actually asked for with the AgentResult it produced.
    AgentResult alone doesn't carry the original task text (in the
    existing single-task consult_coworker_agent path the caller already
    has it, so there was never a need to duplicate it there); a batch's
    combined report needs it to say which result answers which subtask."""
    agent_name: str
    task_preview: str
    result: AgentResult
    required: bool = True
    retried: bool = False


@dataclass(frozen=True)
class AgentBatchResult:
    """The structured outcome of one bounded-parallel coworker-delegation
    batch (agent.agents.manager.execute_agents_parallel) -- what the
    orchestrator needs to answer: which agent succeeded, which failed,
    what did each produce, can the request continue, was anything
    retried. cost_usd is the WHOLE batch's attributable spend (usage
    records recorded for this request_id during the batch window), not
    broken down per subtask -- every subtask in a batch shares the outer
    request's request_id (same convention tools/schemas/agents.py's
    consult_coworker_agent already uses), so agent/usage.py's existing
    request_id-keyed aggregation can't separate one subtask's spend from
    another's without new per-subtask id plumbing; that's a real,
    documented limitation, not an oversight (see STEP 7 in this
    milestone's own design notes)."""
    status: BatchStatus
    items: List[AgentBatchItem]
    request_id: Optional[str]
    duration_seconds: float = 0.0
    cost_usd: Optional[float] = None
    note: str = ""
