"""Builds the memory-relevant portion of the system prompt: deterministic
relevance retrieval against the unified memory system (agent/memory/),
budget-limited, so the model gets what's actually relevant to the current
request instead of the full memory database dumped in every time.

Scoped deliberately narrow: PATTERN-type memories (inferred communication
habits) are the one thing that was previously always-injected-in-full
into every prompt via agent.patterns.patterns_as_prompt_text() -- this
replaces that specific behavior with relevance filtering, which is the
actual "stop dumping everything" change Phase 3 asked for.

Two things this deliberately does NOT touch, because they were never
"dump everything" problems to begin with:
- LESSON-type memories (standing rules) stay always-all-included via
  agent.lessons.lessons_as_prompt_text() directly -- they're hard
  requirements, not contextual background subject to a relevance cutoff.
- FACT/PREFERENCE-type memories were never auto-injected into the prompt
  at all (only retrievable on demand via the recall_facts tool) -- this
  doesn't change that; it only changes what already got auto-injected.

Recent conversation is handled through the normal messages list passed to
the model API (the correct way to give a model conversation history), not
duplicated into a text block here. Available tools are handled by the
API's own `tools` parameter, for the same reason -- this module only
produces the one piece that was actually being over-injected.

Phase 4 addition: every retrieval is logged (agent/observability.py,
correlated by request_id) and, if an ExecutionState is provided, recorded
onto it as a MemoryReference -- id/type/reason/score, never the memory's
own content -- so a later "why did Jarvis use this" view has something
real to show without duplicating memory content into execution state.
"""
from dataclasses import dataclass, field
from typing import List, Optional

from agent.memory import Memory, MemoryType, search_scored
from agent.observability import log_event
from config.settings import settings


@dataclass
class RetrievedMemory:
    memory: Memory
    reason: str
    score: float = 0.0


@dataclass
class Context:
    retrieved: List[RetrievedMemory] = field(default_factory=list)

    @property
    def prompt_text(self) -> str:
        if not self.retrieved:
            return ""
        return "\n".join(f"- {r.memory.content}" for r in self.retrieved)


def build_context(
    user_input: str,
    max_memories: Optional[int] = None,
    request_id: Optional[str] = None,
    state=None,
) -> Context:
    """Relevance-filtered, budget-limited pattern retrieval for the given
    request. Returns an empty Context (empty prompt_text) if there's no
    input to match against or the budget is zero -- callers should treat
    that the same as "nothing relevant," not an error.

    request_id/state are optional -- when given, each retrieved memory is
    logged (component="context") and recorded onto `state` for later
    inspection (e.g. the dashboard)."""

    budget = settings.context_memory_budget if max_memories is None else max_memories

    if budget <= 0 or not user_input or not user_input.strip():
        return Context()

    scored_matches = search_scored(query=user_input, type=MemoryType.PATTERN, limit=budget)

    retrieved = []
    for memory, score in scored_matches:
        reason = f"relevant to the current request (matched: {user_input[:60]!r})"
        retrieved.append(RetrievedMemory(memory=memory, reason=reason, score=score))

        log_event(
            "memory_retrieved", request_id=request_id, component="context",
            memory_id=memory.id, memory_type=memory.type.value, score=round(score, 2),
            reason=reason, included=True,
        )
        if state is not None:
            state.record_memory_retrieval(
                memory_id=memory.id, memory_type=memory.type.value,
                reason=reason, score=score, included=True,
            )

    return Context(retrieved=retrieved)


def build_profile_context(
    max_memories: int = 4,
    request_id: Optional[str] = None,
    state=None,
) -> Context:
    """Return a tiny, separately bounded set of stable user-profile facts.

    Profile memories are always potentially useful background, unlike
    request-specific communication patterns. Their separate cap prevents a
    file scan or future import from flooding every prompt.
    """
    if max_memories <= 0:
        return Context()

    matches = search_scored(query="", type=MemoryType.PROFILE, limit=max_memories)
    retrieved = []
    for memory, score in matches:
        reason = "stable user profile context"
        retrieved.append(RetrievedMemory(memory=memory, reason=reason, score=score))
        log_event(
            "memory_retrieved", request_id=request_id, component="context",
            memory_id=memory.id, memory_type=memory.type.value,
            score=round(score, 2), reason=reason, included=True,
        )
        if state is not None:
            state.record_memory_retrieval(
                memory_id=memory.id, memory_type=memory.type.value,
                reason=reason, score=score, included=True,
            )
    return Context(retrieved=retrieved)
