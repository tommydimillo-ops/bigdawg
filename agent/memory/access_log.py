"""AUTHORITY.md §2's decided fix: agent/memory/manager.py's
search_scored()/recall() used to update Memory.last_accessed by
rewriting the ENTIRE durable memory.json on every read that touched at
least one memory -- a read that mutates the durable store, on every
ordinary retrieval, is exactly the read/write contention class Phase 9
Reliability S1.1 spent a whole milestone fixing for agent/history_store.py.
The access signal is useful (agent/memory/manager.py's own
_relevance_score already factors recency; pages/1_Dashboard.py sorts by
it) but it is not memory content, and it should not share memory's
durability, its lock, or its full-document rewrite cost.

This module is the sidecar: a small, separate file keyed by memory id,
written best-effort. A failure to write here must NEVER break the
actual search/recall it's recording access for -- the access signal is
secondary, the search result is not. agent/memory/store.py's load_all()
merges this file's values onto each Memory.last_accessed at load time,
so every existing reader (pages/1_Dashboard.py's sort, in particular)
keeps working with zero changes on its end.

Lock file kept separate from the data file (matches
agent/execution_history.py's own _persist() convention exactly) rather
than locking the data file itself opened in "a+" mode -- flock's job is
mutual exclusion around the read-modify-write cycle, not holding the
actual bytes, and a dedicated lock file whose own content never matters
sidesteps append-mode's "every write() jumps to EOF regardless of
seek()" surprise entirely. The data file itself is still a plain
tmp-file-then-os.replace atomic write, same as database/memory.py.
"""
import fcntl
import json
import os
import tempfile
import time
from typing import Dict, Iterable, Optional

ACCESS_LOG_FILE = os.path.expanduser(
    "~/Library/Application Support/CampusPilot/memory_access_log.json",
)


def _load_raw() -> dict:
    try:
        with open(ACCESS_LOG_FILE) as file:
            data = json.load(file)
    except (OSError, json.JSONDecodeError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _save_raw(data: dict) -> None:
    directory = os.path.dirname(ACCESS_LOG_FILE)
    os.makedirs(directory, exist_ok=True)
    descriptor, tmp_path = tempfile.mkstemp(dir=directory)
    try:
        with os.fdopen(descriptor, "w") as file:
            json.dump(data, file)
        os.replace(tmp_path, ACCESS_LOG_FILE)
    finally:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)


def record_access(memory_id: str, timestamp: Optional[float] = None) -> None:
    """Best-effort. Any failure (permissions, disk full) is swallowed
    here, not raised -- the caller's own search/recall result must
    never fail or block because this secondary write couldn't happen."""
    record_accesses([memory_id], timestamp)


def record_accesses(memory_ids: Iterable[str], timestamp: Optional[float] = None) -> None:
    """Batch form -- agent/memory/manager.py's search_scored() can touch
    several memories in one call; this is one lock acquisition and one
    file replace for all of them, not one per id."""
    memory_ids = list(memory_ids)
    if not memory_ids:
        return
    when = timestamp if timestamp is not None else time.time()
    lock_path = f"{ACCESS_LOG_FILE}.lock"
    try:
        os.makedirs(os.path.dirname(ACCESS_LOG_FILE), exist_ok=True)
        with open(lock_path, "a+") as lock_file:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
            try:
                data = _load_raw()
                for memory_id in memory_ids:
                    data[memory_id] = when
                _save_raw(data)
            finally:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
    except OSError:
        pass


def get_all() -> Dict[str, float]:
    """Read-only. Returns {} on any failure to read/parse -- this is a
    secondary signal being merged onto real memory content in
    store.load_all(); a corrupt or missing sidecar must degrade to "no
    access data available yet," never break loading memories
    themselves. No lock needed for a read of an atomically-replaced
    file -- same reasoning database/memory.py's own get_memory() already
    relies on."""
    return _load_raw()
