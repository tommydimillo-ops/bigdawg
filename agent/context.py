"""Builds the memory-relevant portion of the system prompt: deterministic
relevance retrieval against the unified memory system (agent/memory/),
budget-limited, so the model gets what's actually relevant to the current
request instead of the full memory database dumped in every time.

Scoped deliberately narrow: PATTERN-type memories (inferred communication
habits) are the one thing that was previously always-injected-in-full
into every prompt via agent.patterns.patterns_as_prompt_text() -- this
replaces that specific behavior with relevance filtering, which is the
actual "stop dumping everything" change Phase 3 asks for.

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
"""
from dataclasses import dataclass, field
from typing import List

from agent.memory import Memory, MemoryType, search
from config.settings import settings


@dataclass
class RetrievedMemory:
    memory: Memory
    reason: str


@dataclass
class Context:
    retrieved: List[RetrievedMemory] = field(default_factory=list)

    @property
    def prompt_text(self) -> str:
        if not self.retrieved:
            return ""
        return "\n".join(f"- {r.memory.content}" for r in self.retrieved)


def build_context(user_input: str, max_memories: int = None) -> Context:
    """Relevance-filtered, budget-limited pattern retrieval for the given
    request. Returns an empty Context (empty prompt_text) if there's no
    input to match against or the budget is zero -- callers should treat
    that the same as "nothing relevant," not an error."""

    budget = settings.context_memory_budget if max_memories is None else max_memories

    if budget <= 0 or not user_input or not user_input.strip():
        return Context()

    matches = search(query=user_input, type=MemoryType.PATTERN, limit=budget)

    retrieved = [
        RetrievedMemory(memory=m, reason=f"relevant to the current request (matched: {user_input[:60]!r})")
        for m in matches
    ]
    return Context(retrieved=retrieved)
