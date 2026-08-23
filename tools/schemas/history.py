"""Phase 9 M4.3 -- two narrow, read-only Jarvis tools over the durable
conversation history store (agent/history_store.py). See that module's
own docstring and ARCHITECTURE.md for the full HISTORY-vs-MEMORY
boundary rationale -- these tools never touch agent/memory/.

Deliberately NOT a generic history-query surface: no raw SQL, no raw
FTS5 MATCH syntax (routed only through history_store.build_safe_match_query
via search_history()), no caller-supplied db_path -- HISTORY_DB is always
passed explicitly by this module, matching agent/history_capture.py's own
established pattern. Both tools are permission_level=0 (pure reads, no
side effect, safe unattended, safe in parallel), matching
tools/schemas/graphify.py's precedent exactly.

The ToolSpec name search_conversation_history is deliberately NOT the
same as history_store.search_history() -- the Jarvis-facing tool surface
gets the disambiguated name so it never collides conceptually with
agent/memory/manager.py's search_scored(), given History vs Memory is a
stated architectural invariant. The module keeps its own short name.

Every one of history_store's six distinct exception classes is mapped to
its own stable, machine-readable `state` in the returned JSON -- never
collapsed into a generic error string, never allowed to escape as a raw
traceback.
"""
import json

import agent.history_store as history_store
from tools.registry import ToolSpec, register

_MAX_RESULTS_CAP = 50
_DEFAULT_MAX_RESULTS = 10

_ERROR_STATES = {
    history_store.HistoryUnavailable: "unavailable",
    history_store.HistorySchemaError: "schema_incompatible",
    history_store.HistoryCorruption: "corrupt",
    history_store.HistoryBusy: "busy",
    history_store.HistoryValidationError: "invalid_input",
    history_store.HistoryUnsupportedRuntime: "unsupported_runtime",
}


def _error_state(error: Exception) -> str:
    for exc_type, state in _ERROR_STATES.items():
        if isinstance(error, exc_type):
            return state
    return "error"


def _history_status(tool_input: dict) -> str:
    try:
        status = history_store.history_status(db_path=history_store.HISTORY_DB)
    except history_store.HistoryStoreError as error:
        return json.dumps({"state": _error_state(error), "available": False, "error": str(error)})

    return json.dumps({
        "state": "ok",
        "available": status.available,
        "schema_version": status.schema_version,
        "session_count": status.session_count,
        "turn_count": status.turn_count,
        "oldest_turn_at": status.oldest_turn_at,
        "newest_turn_at": status.newest_turn_at,
        "fts_available": status.fts_available,
    })


def _search_conversation_history(tool_input: dict) -> str:
    query = tool_input.get("query", "")
    source = tool_input.get("source")
    role = tool_input.get("role")
    session_id = tool_input.get("session_id")

    # A model can emit a malformed max_results (a non-numeric string, a
    # list, ...) despite the schema -- int() raising ValueError/TypeError
    # here is neither a HistoryStoreError nor caught below, which would
    # let a raw traceback escape a permission-0 read-only tool. Coerced
    # defensively instead, mapped to the same "invalid_input" state
    # HistoryValidationError already uses. An explicit `is None` check
    # (not `or`) so a real 0 clamps to 1 like any other out-of-range
    # number, rather than silently becoming the default.
    raw_max_results = tool_input.get("max_results")
    if raw_max_results is None:
        max_results = _DEFAULT_MAX_RESULTS
    else:
        try:
            max_results = int(raw_max_results)
        except (TypeError, ValueError):
            return json.dumps({
                "state": "invalid_input",
                "results": [],
                "error": f"max_results must be an integer, got {raw_max_results!r}",
            })
    max_results = max(1, min(max_results, _MAX_RESULTS_CAP))

    try:
        results = history_store.search_history(
            query, source=source, role=role, session_id=session_id,
            max_results=max_results, db_path=history_store.HISTORY_DB,
        )
    except history_store.HistoryStoreError as error:
        return json.dumps({"state": _error_state(error), "results": [], "error": str(error)})

    return json.dumps({
        "state": "ok",
        "results": [
            {
                "turn_id": r.turn_id,
                "session_id": r.session_id,
                "request_id": r.request_id,
                "created_at": r.created_at,
                "source": r.source,
                "role": r.role,
                "snippet": r.snippet,
                "rank": r.rank,
                "redacted": r.redacted,
                "truncated": r.truncated,
            }
            for r in results
        ],
    })


register(ToolSpec(
    name="history_status",
    description=(
        "Check whether Jarvis's durable conversation-history store is "
        "available, and if so, how many sessions/turns it holds and its "
        "date range. Read-only, no arguments. Distinguishes 'not yet "
        "initialized' from 'corrupt' from 'busy' from 'schema too new for "
        "this build' -- never a generic failure."
    ),
    input_schema={"type": "object", "properties": {}, "required": []},
    permission_level=0,
    handler=_history_status,
    parallel_safe=True,
))

register(ToolSpec(
    name="search_conversation_history",
    description=(
        "Full-text search over Jarvis's durable record of past "
        "conversations and actions -- what was actually said/done, not "
        "distilled facts (see memory tools for that). Returns bounded, "
        "provenance-complete hits: which turn/session/request, what "
        "source (chat/voice/scheduled) and role (user/assistant), when, "
        "and a short snippet -- never the full raw turn. Plain natural-"
        "language query only; FTS operators/wildcards are not "
        "interpreted specially. Optionally filter by source, role, or a "
        "known session_id. Distinguishes 'no history yet' from 'corrupt' "
        "from 'busy, retry later' from 'schema too new' -- never a "
        "generic failure."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "Plain-language search text.",
            },
            "source": {
                "type": "string",
                "enum": ["chat", "voice", "scheduled"],
                "description": "Optional: restrict to one source.",
            },
            "role": {
                "type": "string",
                "enum": ["user", "assistant"],
                "description": "Optional: restrict to one role.",
            },
            "session_id": {
                "type": "string",
                "description": "Optional: restrict to one known session.",
            },
            "max_results": {
                "type": "integer",
                "description": f"Maximum results to return (capped at {_MAX_RESULTS_CAP}). Defaults to {_DEFAULT_MAX_RESULTS}.",
            },
        },
        "required": ["query"],
    },
    permission_level=0,
    handler=_search_conversation_history,
    parallel_safe=True,
))
