"""Persistent, bounded execution history -- distinct from both:
- agent/memory/ (personal facts/preferences/rules the user wants
  remembered indefinitely), and
- agent/audit.py (the full, unbounded security/action log of individual
  tool calls).

This is a short, rolling window of PAST REQUESTS' metadata (not their
full content), specifically so Jarvis -- via the view_task_history tool
-- and the dashboard can answer "what did you just do" without
re-deriving it from the raw audit log every time.

Stored as a JSON file (same read-modify-write pattern as every other
store in this project, e.g. agent/scheduled_tasks.py), bounded to the
most recent N completed executions (config.settings.execution_history_
limit, default 20) -- oldest entries are dropped, never grows unbounded.

Sanitized before persistence: raw tool inputs are never stored verbatim
here at all (only tool names/counts), and the request summary plus any
recorded error text go through agent.memory.safety.redact_secrets()
before being written, so a request that happened to contain something
credential-shaped doesn't end up sitting in plaintext history.
"""
import json
import os
import tempfile
import time
import fcntl
from dataclasses import asdict, dataclass, field
from typing import List, Optional

from agent.execution_state import ExecutionState, list_active
from agent.memory.safety import redact_secrets
from agent.observability import log_event
from config.settings import settings

HISTORY_FILE = os.path.expanduser("~/Library/Application Support/CampusPilot/execution_history.json")

MAX_SUMMARY_LENGTH = 200


@dataclass
class ExecutionRecord:
    request_id: str
    timestamp: float = field(default_factory=time.time)
    request_summary: str = ""
    model: Optional[str] = None
    duration_seconds: Optional[float] = None
    status: str = "completed"
    tools_used: List[str] = field(default_factory=list)
    tool_count: int = 0
    plan_created: bool = False
    plan_total_steps: int = 0
    plan_completed_steps: int = 0
    errors: List[str] = field(default_factory=list)
    memories_retrieved_count: int = 0
    autonomy_level: Optional[int] = None
    confirmation_events: int = 0
    # Phase 6.5: which capability layer handled this request (see
    # agent/delegation.py) -- "native_tool"/"claude_skill"/etc. delegated_skill
    # is just the skill's name (look it up in agent.skills.registry for
    # its current description/instructions; those aren't duplicated here).
    delegation_destination: Optional[str] = None
    delegated_skill: Optional[str] = None
    # Phase 7: which coworker agent(s) (see agent/agents/) handled this
    # request, if any -- distinct from delegation_destination/
    # delegated_skill the same way agent.agents.router is distinct from
    # agent.delegation (see that module's docstring).
    agents_used: List[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "ExecutionRecord":
        known_fields = {f for f in cls.__dataclass_fields__}
        return cls(**{k: v for k, v in data.items() if k in known_fields})


def _sanitize_summary(text: str) -> str:
    text = redact_secrets(text or "").strip()
    return text if len(text) <= MAX_SUMMARY_LENGTH else text[:MAX_SUMMARY_LENGTH] + "…"


def _load_raw() -> List[dict]:
    if not os.path.exists(HISTORY_FILE):
        return []
    with open(HISTORY_FILE, "r") as f:
        return json.load(f)


def _save_raw(records: List[dict]) -> None:
    directory = os.path.dirname(HISTORY_FILE)
    os.makedirs(directory, exist_ok=True)
    fd, tmp_file = tempfile.mkstemp(prefix="execution-history-", suffix=".tmp", dir=directory)
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(records, f, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_file, HISTORY_FILE)
    finally:
        if os.path.exists(tmp_file):
            os.remove(tmp_file)


def _persist(record: ExecutionRecord) -> None:
    record.request_summary = _sanitize_summary(record.request_summary)
    record.errors = [redact_secrets(e) for e in record.errors]

    lock_file = f"{HISTORY_FILE}.lock"
    os.makedirs(os.path.dirname(HISTORY_FILE), exist_ok=True)
    with open(lock_file, "a+") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        records = _load_raw()
        records.append(record.to_dict())

        limit = max(0, settings.execution_history_limit)
        if len(records) > limit:
            records = records[-limit:]

        _save_raw(records)


def _record_from_state(request_id: str, request_summary: str, state: ExecutionState, status: str) -> ExecutionRecord:
    plan = state.plan
    return ExecutionRecord(
        request_id=request_id,
        request_summary=request_summary,
        model=state.selected_model,
        duration_seconds=state.duration_seconds,
        status=status,
        tools_used=list(state.tools_executed),
        tool_count=len(state.tools_executed),
        plan_created=plan is not None,
        plan_total_steps=plan.total if plan is not None else 0,
        plan_completed_steps=plan.completed_count if plan is not None else 0,
        errors=[state.error] if state.error else [],
        memories_retrieved_count=len(state.memories_retrieved),
        autonomy_level=None,  # set by the caller, which has the RequestContext
        confirmation_events=state.confirmation_events,
        delegation_destination=state.delegation_destination,
        delegated_skill=state.selected_skill,
        agents_used=list(state.agents_used),
    )


# --- Public API (Phase 5 spec shape) ------------------------------------
#
# record_started/record_update are deliberately lightweight (structured
# logging only, no JSON write) -- a record with an unknown final duration/
# status/tool_count doesn't belong in a bounded history of completed
# executions. The three outcome functions below are what actually persist
# a record, once the outcome is actually known.

def record_started(request_id: str, request_summary: str) -> None:
    log_event("history_tracking_started", request_id=request_id, component="execution_history",
               summary_preview=_sanitize_summary(request_summary))


def record_update(request_id: str, **fields) -> None:
    log_event("history_tracking_update", request_id=request_id, component="execution_history", **fields)


def record_completed(request_id: str, request_summary: str, state: ExecutionState, autonomy_level: Optional[int] = None) -> None:
    record = _record_from_state(request_id, request_summary, state, status="completed")
    record.autonomy_level = autonomy_level
    _persist(record)


def record_failed(request_id: str, request_summary: str, state: ExecutionState, autonomy_level: Optional[int] = None) -> None:
    record = _record_from_state(request_id, request_summary, state, status="failed")
    record.autonomy_level = autonomy_level
    _persist(record)


def record_cancelled(request_id: str, request_summary: str, state: ExecutionState, autonomy_level: Optional[int] = None) -> None:
    record = _record_from_state(request_id, request_summary, state, status="cancelled")
    record.autonomy_level = autonomy_level
    _persist(record)


def get_active() -> List[ExecutionState]:
    """Delegates to agent.execution_state -- live, in-process state, not
    part of the persistent history itself. Exposed here too so callers
    (the dashboard, the history tool) have one module to import for both
    "what's happening now" and "what happened before"."""
    return list_active()


def get_recent(limit: Optional[int] = None) -> List[ExecutionRecord]:
    records = [ExecutionRecord.from_dict(r) for r in _load_raw()]
    records.sort(key=lambda r: r.timestamp, reverse=True)
    if limit is not None:
        records = records[:limit]
    return records


def get_by_id(request_id: str) -> Optional[ExecutionRecord]:
    for raw in _load_raw():
        if raw.get("request_id") == request_id:
            return ExecutionRecord.from_dict(raw)
    return None
