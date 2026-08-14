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
