"""Builds the history-relevant portion of the system prompt -- Phase 9
M4.4's foundation: deterministic, bounded, opt-in retrieval against the
durable conversation history store (agent/history_store.py, M4.1),
surfacing a small number of relevant past exchanges automatically rather
than only on explicit search (M4.3's search_conversation_history tool).

INERT BY DEFAULT, TWICE OVER: build_history_context() is not called from
anywhere in the real request path yet -- agent/brain.py and
agent/executor.py are untouched by this module, and even once wired in,
nothing here runs unless settings.proactive_history_enabled is True
(default False). Wiring this in is a deliberate, separate, later step so
that if anything here is wrong it cannot affect a real conversation
before it has been reviewed.

Mirrors agent/context.py's module boundary and shape deliberately: a
dedicated module per store (history here, memory there), a small
dataclass return type with a `prompt_text` property, per-hit
observability logging, budget-limited retrieval instead of dumping
everything in. history_store and agent/memory/ still never import each
other, and this module never imports agent/context.py or vice versa --
History vs. Memory stays one architectural line, not two
implementations of the same idea wearing different names.

WHY A SEPARATE, SHORTER busy_timeout: this module always passes
settings.history_context_timeout_ms to search_history(), never the
store's own 5-second default -- a feature gating every ordinary
conversation turn must fail fast, not fail thoroughly. See
agent.history_store._connect_readonly's own docstring for why this is
defense-in-depth rather than a fix for a reproduced hazard: a held write
lock was empirically found NOT to block an ordinary read at all under
this store's real WAL journal mode (tests/test_history_store.py).

FAILURE ISOLATION: matches agent/history_capture.py's own philosophy,
retrieval-side instead of write-side -- the entire attempt is wrapped in
one try/except HistoryStoreError and never raises. A failed, slow, or
absent history store must never break or delay a normal conversation.
HistoryUnavailable (no store yet) is silent -- it's the normal state for
a fresh install, not an anomaly. HistoryBusy logs at DEBUG -- expected,
ordinary contention, not a problem. Every other HistoryStoreError
subclass (schema/corruption/unsupported-runtime/validation) logs at
WARNING -- invisible to the user, but a real environment or internal
anomaly worth knowing about.

TOKEN COUNTING IS A WORD-COUNT APPROXIMATION, NOT A REAL TOKENIZER --
deliberately, so nobody later "fixes" this into a real tokenizer
dependency mistaking it for a bug it isn't. It exists only for the
observability estimate (how much of an injected block's cost is
attributable to history, for the dashboard/logs -- the user has been
burned by confusing cost surprises before, this is about visibility,
not billing correctness). The real per-request cost is already captured
correctly regardless, in the provider's own reported input_tokens on
the same request agent.usage.record_llm_usage() already logs -- this
estimate never substitutes for that.
"""
from dataclasses import dataclass, field
from typing import List, Optional

import agent.history_store as history_store
from agent.observability import log_event
from config.settings import settings

_SECTION_HEADER = (
    "RELEVANT PAST CONVERSATIONS — evidence retrieved from history, not "
    "memory. These are excerpts of what was actually said, not distilled "
    "facts; wording/dates are exact but the excerpt itself is a bounded "
    "snippet, not the full exchange. Cite what you use (e.g. \"we talked "
    "about this on [date]\") rather than presenting it as spontaneous "
    "recall:"
)

# Rough word-to-token approximation for the observability estimate only
# (see module docstring) -- never used to decide what gets included.
_APPROX_TOKENS_PER_WORD = 1.3


@dataclass
class RetrievedHistoryTurn:
    result: history_store.SearchResult
    approx_tokens: int


@dataclass
class HistoryContext:
    retrieved: List[RetrievedHistoryTurn] = field(default_factory=list)

    @property
    def prompt_text(self) -> str:
        if not self.retrieved:
            return ""
        lines = [_SECTION_HEADER]
        for item in self.retrieved:
            r = item.result
            lines.append(f'- [{r.created_at}, {r.source}, {r.role}] "{r.snippet}"')
        return "\n".join(lines)


def _approx_token_count(text: str) -> int:
    """Word-count-based approximation, not a real tokenizer -- see this
    module's own docstring. Good enough for a rough cost-visibility
    estimate; never used to decide correctness or what gets included."""
    if not text:
        return 0
    return max(1, round(len(text.split()) * _APPROX_TOKENS_PER_WORD))


def build_history_context(
    user_input: str,
    request_id: Optional[str] = None,
    state=None,
) -> HistoryContext:
    """Relevance-filtered, budget-limited history retrieval for the
    given request. Returns an empty HistoryContext (empty prompt_text)
    if the feature is off, there's no input to match against, or
    anything about the store isn't available -- callers should treat
    all of those identically to "nothing relevant," never as an error.

    NOT currently called from agent.brain/agent.executor -- see module
    docstring. `state` is accepted now (mirroring agent/context.py's
    signature) but not yet written to -- ExecutionState has no
    history-specific reference type yet, deliberately out of this
    round's scope."""
    if not settings.proactive_history_enabled:
        return HistoryContext()

    if not user_input or not user_input.strip():
        return HistoryContext()

    try:
        results = history_store.search_history(
            user_input,
            max_results=settings.history_context_max_results,
            db_path=history_store.HISTORY_DB,
            busy_timeout_ms=settings.history_context_timeout_ms,
        )
    except history_store.HistoryStoreError as error:
        if isinstance(error, history_store.HistoryUnavailable):
            pass  # normal state for a fresh install -- not worth logging
        elif isinstance(error, history_store.HistoryBusy):
            log_event(
                "history_retrieval_skipped", request_id=request_id, component="history_context",
                level="debug", reason="busy", error_type=type(error).__name__,
            )
        else:
            log_event(
                "history_retrieval_skipped", request_id=request_id, component="history_context",
                level="warning", reason="store_error", error_type=type(error).__name__,
            )
        return HistoryContext()

    budget = settings.history_context_budget_tokens
    retrieved = []
    used_tokens = 0
    for result in results:
        tokens = _approx_token_count(result.snippet)
        if used_tokens + tokens > budget:
            break  # drop the remainder whole -- never truncate a snippet mid-string
        used_tokens += tokens
        retrieved.append(RetrievedHistoryTurn(result=result, approx_tokens=tokens))

        log_event(
            "history_retrieved", request_id=request_id, component="history_context",
            turn_id=result.turn_id, session_id=result.session_id, source=result.source,
            role=result.role, score=round(result.rank, 4), approx_tokens=tokens, included=True,
        )

    return HistoryContext(retrieved=retrieved)
