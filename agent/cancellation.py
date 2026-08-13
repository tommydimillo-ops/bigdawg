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

Process-scoped by construction: the active registry only ever contains
requests running in the same Python process as the caller. request_cancel
has no way to reach a request in a different process (e.g. cancelling a
Streamlit-originated request from the menu-bar app, or vice versa) --
that's a real limitation, not a bug, and is why it can only ever cancel
"requests belonging to the current Jarvis process/session," never an
arbitrary OS process or another interface's in-flight request.
"""
from typing import Optional

from agent.execution_state import ExecutionState, cancel_active, get_active


def request_cancel(request_id: str) -> bool:
    """Returns True if a matching, still-active request was found (in
    this process) and marked cancelled. False if no such request exists
    here -- which is also the correct, safe outcome for a request_id that
    belongs to a different process, was never real, or already finished;
    there is nothing else for this call to affect."""
    return cancel_active(request_id)


def get_request_status(request_id: str) -> Optional[ExecutionState]:
    """For inspecting a specific in-flight request (e.g. the cancel tool
    confirming there's something real to cancel before it tries)."""
    return get_active(request_id)
