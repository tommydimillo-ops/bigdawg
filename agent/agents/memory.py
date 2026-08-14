"""MemoryAgent -- a thin Agent wrapper around the EXISTING memory system
(agent/memory_agent.py's remember()/recall(), themselves a wrapper over
agent/memory/), not a second store of any kind. Uses the same "notes"
key the remember_fact/recall_facts tools already use, so a fact saved
through this agent is the same pool of facts those tools already read
and write -- not a separate, disconnected bucket.

Distinct from agent/agents/models.py's AgentTaskType.MEMORY (what kind
of task the router classified this as) and from the pre-existing module
agent/memory_agent.py (the plain functions this class wraps) -- naming
collision is intentional context, not a mistake: this IS the Agent-
interface adapter FOR that existing module, per Phase 7 section 10's
explicit instruction not to duplicate it.
"""
import time

from agent.agents.base import Agent, AgentMetadata
from agent.agents.models import AgentResult
from agent.memory_agent import recall, remember
from agent.request_context import RequestContext

_NOTES_KEY = "notes"

_RECALL_PREFIXES = ("recall", "what do i", "what did i", "do you remember")
_REMEMBER_STRIP_PREFIXES = (
    "remember that ", "remember to ", "please remember that ", "note that ",
    "keep in mind that ", "keep in mind ", "don't forget that ", "don't forget ",
)


def _is_recall(task_lower: str) -> bool:
    return any(task_lower.startswith(prefix) for prefix in _RECALL_PREFIXES)


def _strip_remember_prefix(task: str) -> str:
    lowered = task.lower()
    for prefix in _REMEMBER_STRIP_PREFIXES:
        if lowered.startswith(prefix):
            return task[len(prefix):].strip()
    return task.strip()


class MemoryAgent(Agent):

    @property
    def metadata(self) -> AgentMetadata:
        return AgentMetadata(
            name="memory",
            description=(
                "Remembers or recalls a fact/preference -- reuses "
                "agent.memory_agent (itself a wrapper over agent.memory), "
                "the same store the remember_fact/recall_facts tools use."
            ),
            capabilities=["remember", "recall", "preferences"],
            supported_task_types=["memory"],
        )

    def execute(self, task: str, context: RequestContext) -> AgentResult:
        start = time.time()
        lowered = task.lower().strip()

        try:
            if _is_recall(lowered):
                answer = recall(_NOTES_KEY)
            else:
                answer = remember(_NOTES_KEY, _strip_remember_prefix(task))
        except Exception as error:
            return AgentResult(
                success=False, agent_name=self.metadata.name, request_id=context.request_id,
                result="", error=f"{type(error).__name__}: {error}",
                duration_seconds=time.time() - start,
            )

        return AgentResult(
            success=True, agent_name=self.metadata.name, request_id=context.request_id,
            result=answer, duration_seconds=time.time() - start,
        )
