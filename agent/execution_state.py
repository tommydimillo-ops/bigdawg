"""Tracks the mutable progress of a single agent-loop run -- distinct from
RequestContext (agent/request_context.py), which is the fixed information
the request started with and never changes. Exists so the loop's progress
is inspectable (for structured logging now, for a UI panel later, per the
"agent execution state" ask) instead of being implicit in loop-local
variables that disappear once the request finishes.

confirmation_pending is included because it was asked for, but is honestly
NOT YET set to True anywhere -- detecting "the model just asked for
confirmation" would require parsing the model's own text response for
that intent, which doesn't exist yet and is out of scope here (that's
workflow-engine-adjacent territory, explicitly deferred). It stays False
always for now; a future phase can wire real detection into it without
changing this class's shape.
"""
import time
from dataclasses import dataclass, field
from typing import List, Optional


@dataclass
class ExecutionState:
    max_iterations: int
    iteration: int = 0
    tools_executed: List[str] = field(default_factory=list)
    confirmation_pending: bool = False  # not yet wired -- see module docstring
    failed: bool = False
    error: Optional[str] = None
    final_result: Optional[str] = None
    started_at: float = field(default_factory=time.time)
    finished_at: Optional[float] = None
    selected_provider: Optional[str] = None
    selected_model: Optional[str] = None

    def record_iteration(self) -> None:
        self.iteration += 1

    def record_tool(self, name: str) -> None:
        self.tools_executed.append(name)

    def record_model(self, provider: str, model: str) -> None:
        self.selected_provider = provider
        self.selected_model = model

    def finish(self, result: Optional[str] = None, failed: bool = False, error: Optional[str] = None) -> None:
        self.finished_at = time.time()
        self.final_result = result
        self.failed = failed
        self.error = error

    @property
    def duration_seconds(self) -> float:
        end = self.finished_at if self.finished_at is not None else time.time()
        return end - self.started_at
