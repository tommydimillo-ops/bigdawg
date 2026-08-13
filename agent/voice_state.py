"""Voice-interface state -- distinct from agent.execution_state's
ExecutionStatus (which describes a TASK's lifecycle) and agent.jarvis_state
(the cross-process "what is the task doing right now" snapshot, Phase 5).
This module only ever tracks phases that are purely about the audio
interface itself -- listening to the mic, transcribing speech, speaking a
reply -- which the Jarvis core has no concept of and never will.

While a request is actually in flight (THINKING/PLANNING/EXECUTING/
WAITING_FOR_CONFIRMATION), that's already tracked precisely by
agent.jarvis_state via the exact same executor.py code path every other
interface (chat, dashboard) already uses -- independently re-deriving it
here would just be a second, potentially-drifting copy of the same
information. get_status() instead composes the two: the real task status
takes precedence whenever one is active (proxied 1:1, since the enum
values below share the same string literals as ExecutionStatus's), and
falls back to whatever voice-local phase was last set otherwise.

Also owns a small non-reentrant "is a voice-originated request currently
running" lock (Phase 6 section 10: "Prevent voice request A + voice
request B from creating competing active executions") -- covers both the
voice loop and the menu-bar's scheduled-task loop, since either one
starting a run must block the other out until it finishes.
"""
import threading
from enum import Enum

from agent import jarvis_state
from agent.execution_state import ExecutionStatus


class VoiceState(str, Enum):
    IDLE = "idle"
    LISTENING = "listening"
    TRANSCRIBING = "transcribing"
    THINKING = "thinking"
    PLANNING = "planning"
    EXECUTING = "executing"
    WAITING_FOR_CONFIRMATION = "waiting_for_confirmation"
    SPEAKING = "speaking"
    CANCELLED = "cancelled"
    ERROR = "error"


# Task-phase statuses agent.jarvis_state already tracks accurately -- when
# it reports one of these, that's what get_status() surfaces instead of
# whatever voice-local phase was last set.
_TASK_PHASES = {
    ExecutionStatus.THINKING.value,
    ExecutionStatus.PLANNING.value,
    ExecutionStatus.EXECUTING.value,
    ExecutionStatus.WAITING_FOR_CONFIRMATION.value,
}

_lock = threading.Lock()
_status = VoiceState.IDLE


def set_status(status: VoiceState) -> None:
    global _status
    with _lock:
        _status = status


def get_status() -> VoiceState:
    """The one thing callers (the menu bar, tests) should read -- combines
    voice-local phases with the real cross-process task status so callers
    never need to know which of the two systems is currently authoritative."""
    task_status = jarvis_state.get_state().status
    if task_status in _TASK_PHASES:
        return VoiceState(task_status)
    with _lock:
        return _status


def reset_to_idle() -> None:
    set_status(VoiceState.IDLE)


# --- Busy-state protection (section 10) ---------------------------------
#
# A plain, non-reentrant lock acquired for the whole span of "run a
# request through the Jarvis core and speak the reply" -- try_start() is
# used (never a blocking acquire()), specifically so a second caller gets
# an immediate, clear "busy" answer instead of silently queueing, which is
# exactly how a second concurrent request would end up executing later
# against stale conversation context.

_busy_lock = threading.Lock()


def try_start() -> bool:
    """Returns True and marks voice/scheduled execution busy if nothing
    else was already running; False (with no state change) if something
    already is."""
    return _busy_lock.acquire(blocking=False)


def finish() -> None:
    if _busy_lock.locked():
        _busy_lock.release()


def is_busy() -> bool:
    return _busy_lock.locked()
