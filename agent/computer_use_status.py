"""Tiny shared flag so the menu-bar app can show a distinct icon whenever
computer-use tools actually have real control of the mouse/keyboard --
kept separate from the audit log (which records what happened after the
fact) so there's also a live, visible signal while it's happening."""
import threading

_lock = threading.Lock()
_active = False


def set_active(value):
    global _active
    with _lock:
        _active = value


def is_active():
    with _lock:
        return _active
