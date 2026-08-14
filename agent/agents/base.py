"""The coworker-agent interface -- a typed contract every specialist
agent (CodingAgent, ResearchAgent, QAAgent, MemoryAgent) implements, so
agent.agents.manager.AgentManager can register/route/execute any of them
uniformly instead of a growing if/elif chain keyed on agent name.

An Agent is a planner/worker, never an authority: nothing in this
interface grants tool access, model access, or permission-bypass of any
kind. Concrete agents reuse existing infrastructure (agent/chat.py,
agent/model_router.py, tools/registry.py via the ordinary executor path,
agent/memory/, agent/research_agent.py) rather than owning their own
client, tool-execution engine, or permission logic -- see each concrete
agent's own docstring for exactly what it reuses.
"""
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import List, Optional

from agent.agents.models import AgentResult
from agent.request_context import RequestContext


@dataclass(frozen=True)
class AgentMetadata:
    name: str
    description: str
    capabilities: List[str] = field(default_factory=list)
    version: str = "1.0"
    enabled: bool = True
    priority: int = 0
    supported_task_types: List[str] = field(default_factory=list)


class Agent(ABC):
    """Subclasses implement `metadata` and `execute`; `can_handle` has a
    sensible default (a case-insensitive substring check against the
    agent's own declared capabilities) that most agents won't need to
    override -- the actual routing DECISION is agent.agents.router's job,
    not any individual agent's; can_handle exists only as the manager's
    last-line sanity check that a routed agent is actually willing to
    take the task, not as a second router."""

    @property
    @abstractmethod
    def metadata(self) -> AgentMetadata:
        raise NotImplementedError

    def can_handle(self, task: str) -> bool:
        # Defaults to trusting the router (agent.agents.router already
        # did the real classification work) rather than re-checking
        # capability keywords here too -- two independently-tuned
        # keyword checks could disagree and incorrectly decline a task
        # the router legitimately routed here. Override only if an
        # agent has a genuine, narrower reason to refuse a task despite
        # being routed to it (e.g. a required precondition isn't met).
        return bool(task and task.strip())

    @abstractmethod
    def execute(self, task: str, context: RequestContext) -> AgentResult:
        """Runs the task and returns a structured, immediate AgentResult.
        Must never raise for an ordinary task failure (return
        success=False with `error` set instead) -- agent.agents.manager
        still wraps every call in its own try/except as a backstop for
        agents that don't hold to this, the same defense-in-depth
        agent/executor.py already applies around tools.registry.dispatch."""
        raise NotImplementedError
