"""Persists structured Memory objects through the existing
database/memory.py file -- same file, same read-modify-write mechanism
every other store in this project already uses -- under a new "memories"
key, alongside the old "notes"/"lessons"/"patterns" keys.

The one-time migration (_migrate_legacy_data) reads whatever's still in
those old raw-string-list keys and wraps each entry as a proper Memory
object, so existing data (a standing rule the user already taught Jarvis,
a fact already remembered) survives moving to the new structure instead
of silently disappearing. It runs at most once per memory.json (guarded
by a migration-marker key) and leaves the old keys in place afterward --
nothing is deleted, this only ever adds the new "memories" key.
"""
from typing import List

import agent.memory.access_log as access_log
from database.memory import get_memory, save_memory
from agent.memory.models import Confidence, Importance, Memory, MemoryType

MEMORIES_KEY = "memories"
MIGRATION_MARKER_KEY = "_memory_v2_migrated"

# (legacy dict key -> (type, importance, confidence, tag) to wrap each of
# its raw string entries as). Ordering/values chosen to exactly match
# what each old system already meant: lessons were always treated as
# permanent hard requirements, patterns were always framed as fallible
# inferences, notes/facts were things the user explicitly asked to be
# remembered.
_LEGACY_KEY_TO_TYPE = {
    "lessons": (MemoryType.LESSON, Importance.PERMANENT, Confidence.USER_EXPLICIT, None),
    "patterns": (MemoryType.PATTERN, Importance.LOW, Confidence.MODEL_INFERRED, None),
    "notes": (MemoryType.FACT, Importance.NORMAL, Confidence.USER_EXPLICIT, "notes"),
}


def _migrate_legacy_data() -> None:
    memory = get_memory()

    if memory.get(MIGRATION_MARKER_KEY):
        return

    migrated = []
    for legacy_key, (mem_type, importance, confidence, tag) in _LEGACY_KEY_TO_TYPE.items():
        for entry in memory.get(legacy_key, []):
            if not isinstance(entry, str) or not entry.strip():
                continue
            migrated.append(Memory(
                content=entry,
                type=mem_type,
                importance=importance,
                confidence=confidence,
                source=f"migrated_from_{legacy_key}",
                tags=[tag] if tag else [],
            ))

    existing_raw = memory.get(MEMORIES_KEY, [])
    combined = existing_raw + [m.to_dict() for m in migrated]

    save_memory(MEMORIES_KEY, combined)
    save_memory(MIGRATION_MARKER_KEY, True)


def load_all() -> List[Memory]:
    """AUTHORITY.md §2: last_accessed is no longer written into memory.json
    itself (agent.memory.manager.search_scored()/recall() write only to
    the agent.memory.access_log sidecar now) -- merged in here instead,
    sidecar value winning when present. A memory never touched since
    this fix shipped still shows whatever last_accessed value it already
    had in memory.json (nothing deletes that); once it's accessed again,
    the sidecar takes over for it. See agent/memory/access_log.py's own
    module docstring for the full reasoning."""
    _migrate_legacy_data()
    memory = get_memory()
    raw = memory.get(MEMORIES_KEY, [])
    memories = [Memory.from_dict(d) for d in raw]

    access_times = access_log.get_all()
    for m in memories:
        if m.id in access_times:
            m.last_accessed = access_times[m.id]

    return memories


def save_all(memories: List[Memory]) -> None:
    save_memory(MEMORIES_KEY, [m.to_dict() for m in memories])
