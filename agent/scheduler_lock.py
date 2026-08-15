"""Cross-process mutex so only one of agent/scheduler_daemon.py's poller
and ui/menu_bar.py's built-in poller ever executes due scheduled tasks on
a given tick, even when both processes are running at once -- a real,
previously-documented lifecycle risk (see CHANGELOG.md) since each
independently polls the same scheduled_tasks.json on its own timer.

fcntl.flock ties the lock to the open file description, so it's released
automatically the instant the holding process exits or is killed --
unlike ui/menu_bar.py's own PID-file single-instance lock, this needs no
stale-lock detection logic at all. Callers re-attempt the lock on every
poll tick (never held across ticks), so ownership fails over to the
other poller within one poll interval if the current owner stops
running its scheduler.
"""
import fcntl
import os
from contextlib import contextmanager

SCHEDULER_LOCK_FILE = os.path.expanduser(
    "~/Library/Application Support/CampusPilot/scheduler.lock"
)


@contextmanager
def try_acquire():
    """Non-blocking. Yields True if this call won exclusive ownership for
    the current poll tick (the caller may process due tasks), or False if
    another process already holds it (the caller must skip this tick
    entirely -- no due-task execution, no mark_run, no UI/voice-state
    interaction). Released the moment the `with` block exits, whether or
    not it was actually acquired."""
    os.makedirs(os.path.dirname(SCHEDULER_LOCK_FILE), exist_ok=True)
    with open(SCHEDULER_LOCK_FILE, "a+") as lock_file:
        try:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            yield False
            return
        try:
            yield True
        finally:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
