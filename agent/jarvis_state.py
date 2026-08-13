"""Cross-interface Jarvis state -- lets the Streamlit app, the menu-bar
app, and (eventually) voice/hardware clients, each running in their own
OS process, answer "what is Jarvis doing right now" without sharing
Python memory. A small JSON file, the same pattern as every other store
in this project (agent/scheduled_tasks.py, agent/execution_history.py) --
not a database server, not IPC, just a filesystem-backed snapshot of the
current moment. Written on each significant status transition (not
polled on a timer -- callers read it on demand, whenever their own UI
happens to refresh).

Deliberately decoupled from any UI framework -- nothing here imports
streamlit or rumps. A future voice or hardware client reads/writes this
exact same file through this exact same module, with no changes needed
here.
"""
import json
import os
import time
from dataclasses import asdict, dataclass, field
from typing import Optional

from agent.execution_state import ExecutionStatus

STATE_FILE = os.path.expanduser("~/Library/Application Support/CampusPilot/jarvis_state.json")

_TERMINAL_OR_IDLE = {
    ExecutionStatus.IDLE.value,
    ExecutionStatus.COMPLETED.value,
    ExecutionStatus.FAILED.value,
    ExecutionStatus.CANCELLED.value,
}


@dataclass
class JarvisState:
    status: str = ExecutionStatus.IDLE.value
    active_request_id: Optional[str] = None
    current_task: Optional[str] = None
    current_tool: Optional[str] = None
    plan_progress: Optional[str] = None  # e.g. "2/4" -- a display string, not the full Plan
    confirmation_pending: bool = False
    last_error: Optional[str] = None
    timestamp: float = field(default_factory=time.time)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "JarvisState":
        known = set(cls.__dataclass_fields__)
        return cls(**{k: v for k, v in data.items() if k in known})


def _save(state: JarvisState) -> None:
    os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
    tmp_file = f"{STATE_FILE}.tmp"
    with open(tmp_file, "w") as f:
        json.dump(state.to_dict(), f, indent=2)
    os.replace(tmp_file, STATE_FILE)


def get_state() -> JarvisState:
    if not os.path.exists(STATE_FILE):
        return JarvisState()
    try:
        with open(STATE_FILE, "r") as f:
            return JarvisState.from_dict(json.load(f))
    except (json.JSONDecodeError, OSError):
        # A torn/corrupt read (e.g. caught mid-write by another process)
        # should degrade to "unknown, assume idle" -- never crash the
        # caller just because a status snapshot was momentarily unreadable.
        return JarvisState()


def set_status(
    status,
    active_request_id: Optional[str] = None,
    current_task: Optional[str] = None,
    current_tool: Optional[str] = None,
    plan_progress: Optional[str] = None,
    confirmation_pending: bool = False,
    last_error: Optional[str] = None,
) -> None:
    _save(JarvisState(
        status=status.value if isinstance(status, ExecutionStatus) else str(status),
        active_request_id=active_request_id,
        current_task=current_task,
        current_tool=current_tool,
        plan_progress=plan_progress,
        confirmation_pending=confirmation_pending,
        last_error=last_error,
        timestamp=time.time(),
    ))


def reset_to_idle() -> None:
    _save(JarvisState(status=ExecutionStatus.IDLE.value))


def is_busy() -> bool:
    """For voice (or any) code to check "is Jarvis currently executing"
    without needing to know the specific status vocabulary. Not wired
    into any actual behavior change yet -- Phase 5 explicitly doesn't
    implement wake-word cancellation -- this just makes the check
    possible for a future caller."""
    return get_state().status not in _TERMINAL_OR_IDLE
