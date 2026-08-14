"""Process-safe shared conversation storage for browser clients."""
import fcntl
import json
import os
import tempfile
from contextlib import contextmanager


CONVERSATION_FILE = os.path.expanduser(
    "~/Library/Application Support/CampusPilot/conversation.json"
)


@contextmanager
def _locked(exclusive: bool):
    directory = os.path.dirname(CONVERSATION_FILE)
    os.makedirs(directory, exist_ok=True)
    with open(f"{CONVERSATION_FILE}.lock", "a+") as lock:
        mode = fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH
        fcntl.flock(lock.fileno(), mode)
        yield


def _load_unlocked():
    try:
        with open(CONVERSATION_FILE, "r") as file:
            messages = json.load(file)
        return messages if isinstance(messages, list) else []
    except (FileNotFoundError, OSError, ValueError):
        return []


def _save_unlocked(messages):
    directory = os.path.dirname(CONVERSATION_FILE)
    descriptor, temporary = tempfile.mkstemp(
        prefix="conversation-", suffix=".tmp", dir=directory,
    )
    try:
        with os.fdopen(descriptor, "w") as file:
            json.dump(messages, file, indent=2)
        os.replace(temporary, CONVERSATION_FILE)
    finally:
        if os.path.exists(temporary):
            os.remove(temporary)


def load_conversation():
    with _locked(exclusive=False):
        return _load_unlocked()


def save_conversation(messages):
    with _locked(exclusive=True):
        _save_unlocked(list(messages))


def append_message(message):
    """Atomically append one message and return the latest shared history."""
    with _locked(exclusive=True):
        messages = _load_unlocked()
        messages.append(dict(message))
        _save_unlocked(messages)
        return messages


def clear_conversation():
    save_conversation([])
