"""Deterministic, non-model-controlled capture of real Jarvis interactions
into agent.history_store -- Phase 9 M4.2, the second implementation slice.

WHY THIS IS A SEPARATE MODULE, NOT LOGIC INSIDE executor.py OR INSIDE
history_store.py: agent/executor.py already does a lot (provider fallback,
tool dispatch, permission gating); folding session/turn bookkeeping into
it directly would widen an already-busy module. agent/history_store.py
owns the database -- it has no opinion about *when* a turn happens or
*which* session a given interaction belongs to, and should stay that way
(M4.1's own boundary: canonical storage, redaction, search -- never
capture policy). This module is the one place that decides "should a
turn be recorded right now, and in which session" and nowhere else does.

THE LLM NEVER DECIDES WHETHER HISTORY IS WRITTEN. Both functions below
are called unconditionally by agent.executor.execute_task_stream at fixed
points in its control flow (request start, every terminal path) -- never
from a ToolSpec, never based on anything a model chose to say or do.
There is no write-history tool in this project and M4.2 does not add one.

SESSION LIFECYCLE (the M4A-approved initial model):
- chat: one process-lifetime session, reused across every chat turn in
  this process (cached in _PROCESS_SESSIONS).
- voice: a separate process-lifetime session from chat, for the same
  reason -- both can run in the same process (see app.py's mic-input
  path, source="voice" from a process that also handles typed "chat"
  turns) without their history merging just because they share a PID.
- scheduled: no caching at all -- every scheduled top-level request gets
  its own brand-new session, one request/one session, deliberately not
  reusing a single "the scheduler" session across unrelated tasks.
No cross-process or cross-restart session continuation exists yet; that
would be persistent UI identity/session-resume logic, explicitly out of
scope for M4.2.

WHY A request_id -> session_id MAP EXISTS (_REQUEST_SESSIONS): the
assistant turn for a request must land in the exact same session the
user turn used, even for "scheduled" sources where nothing is cached by
source alone. Populated by capture_user_turn, consumed (popped) by
capture_assistant_turn -- so it only ever holds entries for requests
whose user turn has been captured but whose assistant turn hasn't yet,
bounded by in-flight request count, never growing across a process's
whole lifetime the way an unbounded log would.

FAILURE ISOLATION: every public function here is best-effort and never
raises. A history-capture failure must never abort the real task, change
the real assistant answer, change tool execution, or change execution
status -- it is caught at this boundary, logged as a bounded
observability warning (operation/source/request_id/error type only,
never raw turn content), and otherwise ignored. This module makes
exactly one capture attempt per logical operation -- no retry loops --
relying on history_store's own (request_id, role) idempotency for
protection against an accidental duplicate call, not a scheme built here.

TEST ISOLATION: every call into agent.history_store below resolves
`history_store.HISTORY_DB` as a plain attribute read at call time (never
as this module's own bound default parameter value), so
tests/__init__.py's central redirection of history_store.HISTORY_DB to a
disposable temp path -- the same, already-established pattern this
project uses for agent.usage.USAGE_FILE -- applies here too, without
needing history_store.py's own already-reviewed public functions to
change their signatures.
"""
import threading
from typing import Dict, Optional

import agent.history_store as history_store
from agent.observability import log_event

_VALID_SOURCES = frozenset({"chat", "voice", "scheduled"})
_PROCESS_SESSION_SOURCES = frozenset({"chat", "voice"})

_session_lock = threading.Lock()
# source -> session_id, only ever populated for chat/voice (see module
# docstring). Never touched for "scheduled".
_process_sessions: Dict[str, str] = {}

_request_sessions_lock = threading.Lock()
# request_id -> session_id, see module docstring's "WHY A request_id ->
# session_id MAP EXISTS" section above.
_request_sessions: Dict[str, str] = {}


def _reset_for_tests() -> None:
    """Test-only. Clears all in-memory session state so one test's
    process-local chat/voice session (or in-flight request_id mapping)
    can never leak into another. Never called from production code."""
    with _session_lock:
        _process_sessions.clear()
    with _request_sessions_lock:
        _request_sessions.clear()


def _get_or_create_session_id(source: str) -> str:
    """Returns the session_id a new turn on `source` should use. chat/voice
    reuse one process-lifetime session each; every other supported source
    (currently just "scheduled") gets a brand-new session every call, no
    caching. Raises exactly what history_store.create_session raises
    (HistoryStoreError and subclasses) -- callers here are always the
    capture functions below, which already catch broadly."""
    if source not in _PROCESS_SESSION_SOURCES:
        return history_store.create_session(source=source, db_path=history_store.HISTORY_DB).session_id

    # No explicit session_id is passed to create_session() below (a fresh
    # UUID4 is minted internally every call), so history_store's own
    # (session_id, source) idempotency can't help here -- unlike a
    # request_id-keyed turn, there is nothing yet for two concurrent
    # first-callers to collide on. The lock therefore has to cover the
    # full check-then-create-then-cache sequence, not just the dict
    # access, or two threads can each decide "no cached session yet" and
    # each create a genuinely separate, orphaned session row. This only
    # matters on the very first call per source in this process: every
    # later call hits the `existing is not None` branch and returns after
    # a single fast dict lookup, never touching SQLite at all -- so this
    # is "held exactly as long as necessary" (one bounded local INSERT,
    # once ever), not the kind of long-held lock the module's own
    # docstring warns against.
    with _session_lock:
        existing = _process_sessions.get(source)
        if existing is not None:
            return existing
        record = history_store.create_session(source=source, db_path=history_store.HISTORY_DB)
        _process_sessions[source] = record.session_id
        return record.session_id


def capture_user_turn(source: str, request_id: str, content: str) -> None:
    """Records the user's request as the first turn of this request's
    history, before any model/provider call is attempted -- so the fact
    the user asked survives even if Jarvis crashes or is cancelled
    immediately afterward. Best-effort: never raises, and a failure here
    must never prevent the real task from being attempted."""
    if source not in _VALID_SOURCES:
        log_event(
            "history_capture_skipped", request_id=request_id, component="history_capture",
            level="warning", operation="record_user_turn", source=source, reason="unsupported_source",
        )
        return
    try:
        session_id = _get_or_create_session_id(source)
        history_store.record_turn(
            session_id=session_id, role="user", content=content,
            request_id=request_id, db_path=history_store.HISTORY_DB,
        )
        with _request_sessions_lock:
            _request_sessions[request_id] = session_id
    except Exception as error:
        log_event(
            "history_capture_failed", request_id=request_id, component="history_capture",
            level="warning", operation="record_user_turn", source=source,
            error_type=type(error).__name__,
        )


def capture_assistant_turn(source: str, request_id: str, content: str) -> None:
    """Records the exact user-visible text Jarvis emitted for this
    request, in the same session the user turn used (or a freshly
    resolved one if the user turn's own capture failed earlier -- see
    module docstring). Callers must not invent placeholder text merely to
    keep the pair symmetrical; pass whatever was actually emitted, which
    may be empty. Best-effort: never raises.

    ALWAYS forgets this request's in-flight session mapping (the
    _request_sessions entry a prior capture_user_turn() call may have
    left behind) before returning, regardless of what happens next --
    empty content, an unsupported source, or a write failure must never
    leave an orphaned entry. This is the sole cleanup point
    agent.executor.execute_task_stream() relies on to guarantee the
    mapping never leaks past one generator execution, including when the
    generator is abandoned/closed early (Phase 9 M4.2's own finally-based
    finalize calls this exactly once per request, unconditionally, on
    every exit path)."""
    with _request_sessions_lock:
        session_id = _request_sessions.pop(request_id, None)

    if not content:
        return

    if source not in _VALID_SOURCES:
        log_event(
            "history_capture_skipped", request_id=request_id, component="history_capture",
            level="warning", operation="record_assistant_turn", source=source, reason="unsupported_source",
        )
        return
    try:
        if session_id is None:
            session_id = _get_or_create_session_id(source)
        history_store.record_turn(
            session_id=session_id, role="assistant", content=content,
            request_id=request_id, db_path=history_store.HISTORY_DB,
        )
    except Exception as error:
        log_event(
            "history_capture_failed", request_id=request_id, component="history_capture",
            level="warning", operation="record_assistant_turn", source=source,
            error_type=type(error).__name__,
        )
