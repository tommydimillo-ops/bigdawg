"""Shared, persistent quiet mode for every Jarvis interface.

Quiet/wake phrase classification is deterministic and happens before a
request reaches any model. State is file-backed because Streamlit and the
native menu-bar listener are separate processes.

Three ways in, one underlying mechanism: "quiet"/"mute"/etc. suppress
indefinitely until an explicit wake phrase, exactly as before. "sleep"
and "off" suppress for a bounded 10/30-minute window that expires on its
own -- added after a live wake-word false-trigger incident (background
audio kept getting transcribed as commands) made clear that "indefinite,
must remember to say a wake phrase" isn't always what's wanted; sometimes
the ask is just "leave me alone for a while." Both stored as the same
{"quiet": true, "until": <timestamp or None>} state -- `until` set for
the timed variants, left None for the indefinite one -- and a timed
period still ends early on an explicit wake phrase too, same as
indefinite quiet mode always has.
"""
import json
import os
import re
import tempfile
import time
from enum import Enum
from typing import Optional


QUIET_MODE_FILE = os.path.expanduser(
    "~/Library/Application Support/CampusPilot/quiet_mode.json"
)

SLEEP_DURATION_SECONDS = 10 * 60
OFF_DURATION_SECONDS = 30 * 60

_QUIET_PHRASES = {
    "quiet", "be quiet", "go quiet", "quiet mode", "silence", "mute",
    "stop talking", "dont talk", "do not talk", "no talking",
}
_SLEEP_PHRASES = {
    "sleep", "go to sleep", "go sleep", "sleep mode", "take a nap",
}
_OFF_PHRASES = {
    "off", "turn off", "shut off", "power off", "shut down",
}
_WAKE_PHRASES = {
    "wake", "wake up", "hi", "hello", "hey", "good morning",
    "you can talk", "start talking", "im back", "i am back",
}


class QuietAction(str, Enum):
    PASS_THROUGH = "pass_through"
    ENTER_QUIET = "enter_quiet"
    ENTER_SLEEP = "enter_sleep"
    ENTER_OFF = "enter_off"
    WAKE = "wake"
    IGNORE = "ignore"


def _normalize(text: str) -> str:
    normalized = re.sub(r"[^a-z0-9']+", " ", (text or "").lower())
    normalized = re.sub(r"\bjarvis\b", " ", normalized)
    return re.sub(r"\s+", " ", normalized).strip().replace("'", "")


def is_quiet_phrase(text: str) -> bool:
    return _normalize(text) in _QUIET_PHRASES


def is_sleep_phrase(text: str) -> bool:
    return _normalize(text) in _SLEEP_PHRASES


def is_off_phrase(text: str) -> bool:
    return _normalize(text) in _OFF_PHRASES


def is_wake_phrase(text: str) -> bool:
    normalized = _normalize(text)
    if normalized in _WAKE_PHRASES:
        return True

    # A wake phrase may naturally include the first command: "Hi Jarvis,
    # what time is it?". Requiring the entire transcript to equal "hi"
    # made that common form stay silently muted. Only greeting-style wake
    # phrases are accepted as prefixes; broad words such as "wake" are
    # kept exact so ordinary muted room speech cannot reactivate Jarvis.
    return any(
        normalized.startswith(prefix + " ")
        for prefix in ("hi", "hello", "hey", "good morning", "wake up")
    )


def _read_state() -> dict:
    """The one place that interprets the persisted file, including
    auto-expiry -- a timed sleep/off period whose `until` has already
    passed is treated as already woken, without needing a separate
    background timer or process to actively flip it back."""
    try:
        with open(QUIET_MODE_FILE) as file:
            value = json.load(file)
    except (FileNotFoundError, OSError, ValueError):
        return {"quiet": False, "until": None}
    if not isinstance(value, dict):
        return {"quiet": False, "until": None}
    quiet = bool(value.get("quiet", False))
    until = value.get("until")
    if quiet and until is not None and time.time() >= until:
        return {"quiet": False, "until": None}
    return {"quiet": quiet, "until": until}


def is_quiet() -> bool:
    return _read_state()["quiet"]


def remaining_seconds() -> Optional[float]:
    """Seconds left before a timed sleep/off period auto-wakes, or None
    if not currently quiet, or quiet indefinitely (no timer at all)."""
    state = _read_state()
    if not state["quiet"] or state["until"] is None:
        return None
    return max(0.0, state["until"] - time.time())


def set_quiet(enabled: bool, duration_seconds: Optional[float] = None) -> None:
    """`duration_seconds=None` (the default) means indefinite -- only an
    explicit wake phrase ends it, exactly the original quiet-mode
    behavior. A number means it also auto-wakes on its own after that
    many seconds (still cancellable early by a wake phrase too)."""
    directory = os.path.dirname(QUIET_MODE_FILE)
    os.makedirs(directory, exist_ok=True)
    until = (time.time() + duration_seconds) if (enabled and duration_seconds is not None) else None
    fd, temporary = tempfile.mkstemp(prefix="quiet-mode-", suffix=".tmp", dir=directory)
    try:
        with os.fdopen(fd, "w") as file:
            json.dump({"quiet": bool(enabled), "until": until, "updated_at": time.time()}, file)
        os.replace(temporary, QUIET_MODE_FILE)
    finally:
        if os.path.exists(temporary):
            os.remove(temporary)


def classify(text: str) -> QuietAction:
    """Classify and apply mode changes before model/executor dispatch.
    Sleep/off/quiet are checked unconditionally, before the is_quiet()
    gate, so any of them can also be said to *change* an already-active
    mode (e.g. "off" said while merely quiet extends to a bounded 30
    minutes) -- the same way saying "quiet" again while already quiet
    always has been."""
    if is_sleep_phrase(text):
        set_quiet(True, duration_seconds=SLEEP_DURATION_SECONDS)
        return QuietAction.ENTER_SLEEP
    if is_off_phrase(text):
        set_quiet(True, duration_seconds=OFF_DURATION_SECONDS)
        return QuietAction.ENTER_OFF
    if is_quiet_phrase(text):
        set_quiet(True)
        return QuietAction.ENTER_QUIET
    if not is_quiet():
        return QuietAction.PASS_THROUGH
    if is_wake_phrase(text):
        set_quiet(False)
        return QuietAction.WAKE
    return QuietAction.IGNORE
