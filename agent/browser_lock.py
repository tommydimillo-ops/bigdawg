"""Cross-process (and cross-thread, within one process) mutex so only one
Jarvis process at a time owns tools/browser.py's shared, persistent Chrome
profile (see tools/browser.py's own PROFILE_DIR comment for why that
profile is dedicated and deliberately persistent, separate from the
user's everyday Chrome). A real, previously-documented lifecycle risk:
Streamlit, the menu-bar app, and a scheduled task can each independently
try to launch Chrome against the same on-disk profile directory, and
nothing in tools/browser.py coordinated across processes at all before
this -- Chrome's own internal profile lock is the only thing that would
have arbitrated, with no clean handling on Jarvis's side.

Same fcntl.flock primitive as agent/scheduler_lock.py, adapted for a
different lifetime: that lock is acquired fresh every poll tick and
released immediately after; this one is acquired once when the browser
context is first created and held open for as long as that context is
alive, released explicitly when the context is torn down
(tools/browser.py's _shutdown()/_discard_dead_context()). Kernel-managed
either way -- a crash or SIGKILL releases it automatically, no stale-lock
detection needed. Worth calling out given ui/menu_bar.py's own
_handle_termination_signal comment documents a real, confirmed case in
this exact codebase where atexit-based cleanup alone does not reliably
run (rumps' AppKit run loop can skip registered atexit callbacks
entirely on SIGTERM) -- the same reason this lock's safety doesn't
depend on tools/browser.py's atexit-registered _shutdown() actually
firing.

The extra threading.Lock (agent/voice_state.py's _busy_lock is the
precedent for this exact pattern in this codebase) closes a narrower,
in-process race the scheduler lock never had to worry about: two
threads in the same process both finding no context yet and both
racing to open+flock the lock file before either one's ownership is
recorded.
"""
import fcntl
import os
import threading

BROWSER_LOCK_FILE = os.path.expanduser(
    "~/Library/Application Support/CampusPilot/chrome-profile.lock"
)

_lock = threading.Lock()
_lock_file = None


def try_acquire() -> bool:
    """Non-blocking. Returns True if this process now owns (or already
    owned) exclusive access to the browser profile; False if another
    process currently holds it. Idempotent -- calling this again while
    this same process already holds the lock just confirms ownership,
    it never opens a second file handle."""
    global _lock_file
    with _lock:
        if _lock_file is not None:
            return True

        os.makedirs(os.path.dirname(BROWSER_LOCK_FILE), exist_ok=True)
        candidate = open(BROWSER_LOCK_FILE, "a+")
        try:
            fcntl.flock(candidate.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            candidate.close()
            return False

        _lock_file = candidate
        return True


def release() -> None:
    """Safe to call even when not currently held (e.g. a launch that
    failed before ever acquiring anything) -- a no-op in that case."""
    global _lock_file
    with _lock:
        if _lock_file is not None:
            try:
                fcntl.flock(_lock_file.fileno(), fcntl.LOCK_UN)
            finally:
                _lock_file.close()
                _lock_file = None


def is_held() -> bool:
    with _lock:
        return _lock_file is not None
