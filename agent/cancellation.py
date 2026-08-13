"""The formal, central cancellation API -- request_cancel(request_id).
Built directly on agent/execution_state.py's active-execution registry
(cancel_active/get_active), which remains the actual mechanism; this
module exists to give cancellation a clear, dedicated, documented place
rather than requiring every caller (the cancel_request tool, the
dashboard's Cancel button, a future voice "stop") to reach into
execution_state's internals directly.

SAFETY: cancellation is cooperative, not preemptive. request_cancel()
only ever sets a flag (ExecutionState.cancelled) that the agent loop
checks at safe boundaries -- see agent/executor.py's cancellation check
points (top of each iteration, before each tool dispatch, before each
retry, before advancing to the next plan step). It never interrupts a
tool call that's already in progress; if a tool is mid-execution when
cancellation is requested, that one call is allowed to finish (or fail on
its own) before the loop notices the cancellation and stops. This is
deliberate: an irreversible action (a click already sent, an email
already handed to Mail) can't safely be un-sent, so "stop" here means
"don't start anything else," not "abort whatever's happening right now."

Cross-process requests use a tiny cancellation marker on disk. The process
that owns the request notices that marker at the same cooperative boundaries
used for local cancellation. A caller may only create a marker for the
request currently advertised by JarvisState, preventing arbitrary IDs from
leaving stale cancellation files behind.
"""
import json
import os
import re
import tempfile
import time
from typing import Optional

from agent.execution_state import ExecutionState, cancel_active, get_active
from agent.jarvis_state import get_state, is_busy


CANCEL_DIR = os.path.expanduser(
    "~/Library/Application Support/CampusPilot/cancellation_requests"
)
_VALID_REQUEST_ID = re.compile(r"^[A-Za-z0-9_-]{1,128}$")


def _marker_path(request_id: str) -> Optional[str]:
    if not isinstance(request_id, str) or not _VALID_REQUEST_ID.fullmatch(request_id):
        return None
    return os.path.join(CANCEL_DIR, f"{request_id}.json")


def _write_marker(request_id: str) -> bool:
    path = _marker_path(request_id)
    if path is None:
        return False
    os.makedirs(CANCEL_DIR, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(prefix="cancel-", suffix=".tmp", dir=CANCEL_DIR)
    try:
        with os.fdopen(fd, "w") as file:
            json.dump({"request_id": request_id, "requested_at": time.time()}, file)
        os.replace(tmp_path, path)
    finally:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)
    return True


def request_cancel(request_id: str) -> bool:
    """Returns True if a matching, still-active request was found (in
    this process) and marked cancelled. False if no such request exists
    here -- which is also the correct, safe outcome for a request_id that
    belongs to a different process, was never real, or already finished;
    there is nothing else for this call to affect."""
    if cancel_active(request_id):
        return True

    shared = get_state()
    if shared.active_request_id != request_id or not is_busy():
        return False
    return _write_marker(request_id)


def cancellation_requested(request_id: Optional[str]) -> bool:
    """Return whether another process requested cooperative cancellation."""
    path = _marker_path(request_id) if request_id else None
    return bool(path and os.path.exists(path))


def clear_cancellation(request_id: Optional[str]) -> None:
    """Remove a marker at request startup/cleanup so IDs cannot stay stale."""
    path = _marker_path(request_id) if request_id else None
    if path:
        try:
            os.remove(path)
        except FileNotFoundError:
            pass


def get_request_status(request_id: str) -> Optional[ExecutionState]:
    """For inspecting a specific in-flight request (e.g. the cancel tool
    confirming there's something real to cancel before it tries)."""
    return get_active(request_id)
