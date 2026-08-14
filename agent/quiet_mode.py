"""Shared, persistent quiet mode for every Jarvis interface.

Quiet/wake phrase classification is deterministic and happens before a
request reaches any model. State is file-backed because Streamlit and the
native menu-bar listener are separate processes.
"""
import json
import os
import re
import tempfile
import time
from enum import Enum


QUIET_MODE_FILE = os.path.expanduser(
    "~/Library/Application Support/CampusPilot/quiet_mode.json"
)

_QUIET_PHRASES = {
    "quiet", "be quiet", "go quiet", "quiet mode", "silence", "mute",
    "stop talking", "dont talk", "do not talk", "no talking",
}
_WAKE_PHRASES = {
    "wake", "wake up", "hi", "hello", "hey", "good morning",
    "you can talk", "start talking", "im back", "i am back",
}


class QuietAction(str, Enum):
    PASS_THROUGH = "pass_through"
    ENTER_QUIET = "enter_quiet"
    WAKE = "wake"
    IGNORE = "ignore"


def _normalize(text: str) -> str:
    normalized = re.sub(r"[^a-z0-9']+", " ", (text or "").lower())
    normalized = re.sub(r"\bjarvis\b", " ", normalized)
    return re.sub(r"\s+", " ", normalized).strip().replace("'", "")


def is_quiet_phrase(text: str) -> bool:
    return _normalize(text) in _QUIET_PHRASES


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


def is_quiet() -> bool:
    try:
        with open(QUIET_MODE_FILE) as file:
            value = json.load(file)
        return bool(value.get("quiet", False)) if isinstance(value, dict) else False
    except (FileNotFoundError, OSError, ValueError):
        return False


def set_quiet(enabled: bool) -> None:
    directory = os.path.dirname(QUIET_MODE_FILE)
    os.makedirs(directory, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix="quiet-mode-", suffix=".tmp", dir=directory)
    try:
        with os.fdopen(fd, "w") as file:
            json.dump({"quiet": bool(enabled), "updated_at": time.time()}, file)
        os.replace(temporary, QUIET_MODE_FILE)
    finally:
        if os.path.exists(temporary):
            os.remove(temporary)


def classify(text: str) -> QuietAction:
    """Classify and apply mode changes before model/executor dispatch."""
    if is_quiet_phrase(text):
        set_quiet(True)
        return QuietAction.ENTER_QUIET
    if not is_quiet():
        return QuietAction.PASS_THROUGH
    if is_wake_phrase(text):
        set_quiet(False)
        return QuietAction.WAKE
    return QuietAction.IGNORE
