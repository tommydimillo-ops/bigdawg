"""Orchestration glue for one voice interaction -- NOT a second agent.
Every actual decision (what to do, which tool, whether it's allowed) still
happens exactly where it always has: agent.executor.execute_task_stream,
the same entry point chat, the dashboard, and scheduled tasks all use.
This module's only job is composing that core with voice-specific
concerns that have nowhere else to live:

- Running a request and observing the exact ExecutionState created for it,
  so voice can tell whether it ended by asking for confirmation or being
  cancelled without guessing from the process-global active-request list.
- Classifying a spoken response to a pending confirmation as
  affirmative/negative/unclear, and recognizing "Jarvis, stop" -- both
  deterministic, non-LLM text matching, matching this project's existing
  policy of never letting a model decide a security-relevant outcome
  (agent.autonomy.should_request_confirmation is the same kind of
  function).
- Watching the microphone concurrently, on a background thread, while a
  request is executing or a reply is being spoken -- the necessary piece
  for "Jarvis, stop" and speech interruption to work at all, since the
  main voice loop's own thread is busy for the whole duration otherwise.
"""
import re
import threading
import time
from dataclasses import dataclass
from typing import Optional

from agent import tts_control, voice_state
from agent.cancellation import request_cancel
from agent.execution_state import ExecutionState, list_active
from agent.executor import execute_task_stream
from agent.voice_state import VoiceState
from config.settings import settings
from voice.listen import (
    is_exit_phrase,
    listen_for_utterance,
    strip_wake_word,
    transcribe,
    wake_word_detected,
)
from voice.speak import speak_natural

# Deterministic, non-LLM classification of a spoken response to a pending
# confirmation -- these only ever decide how to ROUTE the user's next
# utterance (see classify_confirmation_response's docstring), never
# whether an action is actually allowed to run. That's still enforced
# exclusively by agent.autonomy's existing pending-confirmation ledger,
# completely unaffected by anything in this module.
_AFFIRMATIVE_PATTERN = re.compile(
    r"\b(yes|yeah|yep|yup|sure|go ahead|do it|send it|confirmed?|"
    r"please do|sounds good|okay|ok)\b",
    re.IGNORECASE,
)
_NEGATIVE_PATTERN = re.compile(
    r"\b(no|nope|nah|cancel|don'?t( do)? that|never ?mind|stop|hold off|not now)\b",
    re.IGNORECASE,
)

# A fresh wake-word command that, once the wake word is stripped, is
# EXACTLY one of these (not just contains one of these words somewhere)
# means "cancel" with nothing left to actually ask the model -- an exact-
# match allowlist rather than a substring check, since "stop" alone is
# ambiguous with an ordinary request like "remind me to stop by the
# store" and there's no wake-word co-occurrence left to disambiguate with
# by the time the command reaches this point (voice.listen already
# stripped it).
_BARE_STOP_PHRASES = {"stop", "cancel", "cancel that", "never mind", "nevermind", "that's all"}


@dataclass
class VoiceRunResult:
    text: str
    state: Optional[ExecutionState] = None

    @property
    def needs_confirmation(self) -> bool:
        return bool(self.state and self.state.confirmation_pending)

    @property
    def pending_tool(self) -> Optional[str]:
        return self.state.pending_confirmation_tool if self.state else None

    @property
    def request_id(self) -> Optional[str]:
        return self.state.request_id if self.state else None

    @property
    def was_cancelled(self) -> bool:
        return bool(self.state and self.state.cancelled)


def run_request(request: str, history=None, on_state_created=None) -> VoiceRunResult:
    """Runs `request` through the real Jarvis core -- the exact same
    execute_task_stream() every other interface calls, source="voice" so
    it gets all normal chat permission rules plus voice-specific safety
    gates for persistent actions such as reminders.

    Captures the exact live ExecutionState through the executor's observer
    hook, avoiding ambiguity if another interface has a request active in
    the same process. Safe to keep reading after completion: unregistering
    only drops the registry entry, not this direct reference."""
    state_holder = {}

    def _capture_state(state):
        state_holder["state"] = state
        if on_state_created is not None:
            on_state_created(state)

    gen = execute_task_stream(
        request, history=history, source="voice", on_state_created=_capture_state,
    )
    chunks = []
    for chunk in gen:
        chunks.append(chunk)
    return VoiceRunResult(text="".join(chunks), state=state_holder.get("state"))


def run_request_with_cancellation_watch(
    request: str, history=None, max_wait_seconds: float = 300,
    on_state_created=None,
) -> VoiceRunResult:
    """run_request(), plus a concurrent "Jarvis, stop" listener for the
    whole duration -- the function voice code should actually call for
    anything that might take a while. The watcher only ever calls the
    existing agent.cancellation.request_cancel(request_id) (Phase 5); it
    has no separate cancellation mechanism of its own."""
    done = threading.Event()
    watcher = threading.Thread(target=watch_for_cancellation, args=(done, max_wait_seconds), daemon=True)
    watcher.start()
    try:
        return run_request(
            request, history=history, on_state_created=on_state_created,
        )
    finally:
        done.set()
        watcher.join(timeout=2)


def watch_for_cancellation(done_event: threading.Event, max_wait_seconds: float = 300) -> None:
    """Runs on its own thread for the duration of a request's execution.
    Listens for a wake-word-gated stop phrase ("Jarvis, stop") and, if
    heard, cancels whatever's actually active via request_cancel() --
    cooperative, same as every other caller of that function; this never
    kills a process or interrupts a tool call already in flight. Returns
    (closing its own microphone stream) as soon as done_event is set --
    callers must always set it when the underlying request finishes, or
    this keeps listening until max_wait_seconds regardless."""
    deadline = time.monotonic() + max_wait_seconds
    while not done_event.is_set():
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return
        audio, samplerate = listen_for_utterance(
            stop_flag=done_event, max_wait_seconds=remaining,
        )
        if done_event.is_set() or audio is None:
            return
        text = transcribe(audio, samplerate).strip()
        if not text or not is_cancellation_phrase(text):
            # Background speech must not make Jarvis permanently deaf to a
            # later, explicit "Jarvis, stop" during the same request.
            continue
        for state in list_active():
            if state.request_id:
                request_cancel(state.request_id)
        return


def speak_with_interruption_watch(text: str, max_wait_seconds: float = 60) -> Optional[str]:
    """Speaks `text` via voice.speak.speak_natural, listening concurrently
    for the wake word the whole time -- if heard, playback is cut short
    (agent.tts_control.stop_speaking(), the same shared mechanism the
    Streamlit multi-device chat already uses) and whatever the user said
    right after the wake word is returned, to be treated as the start of
    their next request instead of making them repeat the wake word from
    scratch. Returns None if speech finished normally.

    Gated by settings.voice_interruption_enabled -- disabled, this is a
    plain speak_natural() call and nothing listens while it plays.

    Calibrates the watcher's mic threshold against the room BEFORE
    playback starts (on_ready), not during -- Jarvis's own voice playing
    over the speakers must never be allowed to bias what counts as "loud"
    for the watcher's own detection."""
    voice_state.set_status(VoiceState.SPEAKING)

    if not settings.voice_interruption_enabled:
        speak_natural(text)
        return None

    done = threading.Event()
    ready = threading.Event()
    result_holder = {}

    def _watch():
        result_holder["command"] = _watch_for_speech_interrupt(done, ready, max_wait_seconds)

    watcher = threading.Thread(target=_watch, daemon=True)
    watcher.start()
    ready.wait(timeout=2)  # let calibration finish against the still-quiet room first
    try:
        speak_natural(text)
    finally:
        done.set()
        watcher.join(timeout=2)

    return result_holder.get("command")


def _watch_for_speech_interrupt(
    done_event: threading.Event, ready_event: threading.Event, max_wait_seconds: float,
) -> Optional[str]:
    deadline = time.monotonic() + max_wait_seconds
    first_listen = True
    while not done_event.is_set():
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return None
        audio, samplerate = listen_for_utterance(
            stop_flag=done_event,
            max_wait_seconds=remaining,
            on_ready=ready_event.set if first_listen else None,
        )
        first_listen = False
        if done_event.is_set() or audio is None:
            return None
        text = transcribe(audio, samplerate).strip()
        if not text or not wake_word_detected(text):
            # Ignore room speech and keep watching for an intentional wake
            # word while playback is still active -- same bare-substring
            # gap voice/listen.py's wait_for_command had (background
            # audio containing the wake word anywhere would interrupt
            # active playback identically to a deliberate interruption),
            # closed the same way here since Jarvis is *speaking* for a
            # meaningful fraction of active use, not a rare edge case.
            continue
        tts_control.stop_speaking()
        return strip_wake_word(text) or None


def classify_confirmation_response(text: str) -> str:
    """Deterministic, non-LLM classification of a spoken response to a
    pending confirmation request -- "affirmative", "negative", or
    "unclear". Never itself decides whether the pending action is allowed
    to run: an affirmative response is just relayed as the next normal
    conversational turn (see agent.voice_session's module docstring), so
    the model sees full context and, if it re-attempts the exact same
    tool call, agent.autonomy.is_confirmed() -- unchanged, untouched by
    voice -- is what actually lets it through. A negative response is
    acknowledged locally without ever going back through the model at
    all, so there's nothing for it to creatively reinterpret."""
    if not text or not text.strip():
        return "unclear"
    is_affirmative = bool(_AFFIRMATIVE_PATTERN.search(text))
    is_negative = bool(_NEGATIVE_PATTERN.search(text))
    if is_affirmative and not is_negative:
        return "affirmative"
    if is_negative and not is_affirmative:
        return "negative"
    return "unclear"


def is_cancellation_phrase(text: str) -> bool:
    """Wake-word-gated, reusing voice.listen.is_exit_phrase's existing
    "Jarvis, <stop word>" pattern -- deliberately requires the wake word,
    not a bare "stop", so ordinary conversation can't accidentally cancel
    a real task."""
    return is_exit_phrase(text)


def is_bare_stop_phrase(command: str) -> bool:
    """True if `command` (already wake-word-stripped, as everything
    wait_for_command returns is) is exactly a stop/cancel phrase and
    nothing else -- for recognizing "Jarvis, stop" said as a fresh
    command with no active task to cancel, so it can get a direct "I'm
    not currently running a task" reply instead of being sent to the
    model as a strange, content-free request."""
    if not command:
        return False
    return command.strip().lower().rstrip(".!") in _BARE_STOP_PHRASES
