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
from agent.audit import log_action
from agent.autonomy import Decision, ExecutionContext, should_request_confirmation
from agent.memory_agent import REFUSAL_PREFIX, recall, remember
from agent.request_context import RequestContext

_NOTES_KEY = "notes"

_RECALL_PREFIXES = ("recall", "what do i", "what did i", "do you remember")
_REMEMBER_STRIP_PREFIXES = (
    "remember that ", "remember to ", "please remember that ", "note that ",
    "keep in mind that ", "keep in mind ", "don't forget that ", "don't forget ",
)

# MemoryAgent's write path shares agent.autonomy.should_request_
# confirmation's permission_level-vs-autonomy decision with every
# registered tool call -- not a second, independently-written copy of
# that logic (mirrors agent/agents/coding.py's _WRITE_FILE_PERMISSION_
# LEVEL / M10.0). 1, not write_file's 2: this matches remember_fact's
# own registered permission_level (tools/schemas/memory_and_learning.py)
# -- a memory write is a "safe local action," not a file/code
# modification. recall (read-only) stays ungated, the same reasoning
# agent/agents/coding.py's _read_file and agent/research_agent.py's
# reads were already left ungated for -- see ROADMAP.md's "MemoryAgent
# bypass audit" entry for the full write-vs-read reasoning.
_REMEMBER_PERMISSION_LEVEL = 1
_REMEMBER_TOOL_NAME = "memory_agent_remember"


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
                # M10.0-style gate, applied here for the first time (see
                # ROADMAP.md's "MemoryAgent bypass audit"): the same
                # permission-level-vs-autonomy decision agent/executor.py's
                # _run_tool already applies to every registered tool,
                # through the SAME function. source="agent_worker" (set by
                # agent/agents/worker.py for every coworker-agent
                # subprocess) means a verdict that would otherwise mean
                # "pause and ask" correctly becomes DENY instead of
                # hanging forever with no live person to answer it.
                decision = should_request_confirmation(
                    _REMEMBER_TOOL_NAME, context.autonomy_level,
                    ExecutionContext(source=context.source), permission_level=_REMEMBER_PERMISSION_LEVEL,
                )
                if decision != Decision.ALLOW:
                    # Unlike agent/agents/coding.py's _write_file, there is
                    # no internal tool loop here to hand an "Error: ..."
                    # string back to and let it decide what to do next --
                    # execute()'s own return IS the agent's final answer,
                    # so a denial must be a real AgentResult(success=False),
                    # not a success-shaped result that merely says no.
                    error = f"not permitted at the current autonomy level ({decision.value})"
                    log_action("memory_agent:remember", {"task": task}, error)
                    return AgentResult(
                        success=False, agent_name=self.metadata.name, request_id=context.request_id,
                        result="", error=error, duration_seconds=time.time() - start,
                    )

                answer = remember(_NOTES_KEY, _strip_remember_prefix(task))
                if answer.startswith(REFUSAL_PREFIX):
                    # remember()'s content-safety filter (agent/memory/
                    # safety.py) refused this write -- a refusal is a
                    # real failure of this agent's own task, not a
                    # successful run that happens to say so in its result
                    # text. Same class of bug as the Phase 10 truncated-
                    # response gap: a string that LOOKS like a normal
                    # result was being treated as a clean success.
                    return AgentResult(
                        success=False, agent_name=self.metadata.name, request_id=context.request_id,
                        result="", error=answer, duration_seconds=time.time() - start,
                    )
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
