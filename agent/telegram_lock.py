"""Single-instance lock for agent/telegram_daemon.py.

Only one process may long-poll getUpdates for a given bot token at a time
-- Telegram delivers each update to whichever getUpdates call arrives
first, so two daemons would race and each would see a random half of the
messages. Unlike agent/scheduler_lock.py (reacquired every tick), this is
held for the daemon's entire lifetime.

fcntl.flock ties ownership to the open file description, so the lock is
released the instant the holder exits or is killed -- no PID file, no
stale-lock detection.
"""
import fcntl
import os

TELEGRAM_LOCK_FILE = os.path.expanduser(
    "~/Library/Application Support/CampusPilot/telegram_daemon.lock"
)


class TelegramDaemonAlreadyRunning(RuntimeError):
    pass


def acquire_or_exit():
    """Acquire the lifetime lock. Returns the open file object, which the
    caller must keep referenced for as long as the daemon runs (letting
    it be garbage-collected would close the fd and drop the lock). Raises
    TelegramDaemonAlreadyRunning if another daemon already holds it."""
    os.makedirs(os.path.dirname(TELEGRAM_LOCK_FILE), exist_ok=True)
    handle = open(TELEGRAM_LOCK_FILE, "a+")
    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError as error:
        handle.close()
        raise TelegramDaemonAlreadyRunning(
            "another Telegram daemon is already running on this machine"
        ) from error
    return handle
