"""The public memory interface -- the rest of Jarvis interacts with
memory through these functions, not by touching agent/memory/store.py or
database/memory.py directly.

remember() is where the actual writing discipline lives: every write goes
through the safety filter (agent/memory/safety.py) first, and a new
memory that's about the same subject as an existing active memory of the
same type supersedes it (marks the old one inactive, links it to the new
one) instead of piling up a second, possibly contradictory entry -- the
"I prefer X" -> "I prefer Y now" case from the Phase 3 spec.

Each logical operation does exactly one load + (if it writes) one save of
the underlying store, regardless of how many memories it touches --
deliberately, since a naive per-memory read-modify-write would multiply
file I/O with the number of results (see the performance requirement:
retrieval has to stay fast enough for normal conversation, no per-item
disk round trips).
"""
import re
import time
from typing import List, Optional, Tuple

from agent.memory import access_log, store
from agent.memory.models import Confidence, Importance, Memory, MemoryType
from agent.memory.safety import is_safe_to_remember

# Types where a same-subject write should supersede the prior one instead
# of accumulating alongside it -- these describe "the current state of
# something" (a preference, a fact, a rule), where a genuine update makes
# the old version wrong, not just outdated-but-still-true. PATTERN is
# deliberately excluded: two different noticed quirks are each still
# individually informative, not competing single-truth statements the way
# a preference is.
_SUPERSEDABLE_TYPES = {MemoryType.PREFERENCE, MemoryType.FACT, MemoryType.LESSON, MemoryType.PROFILE}

_STOPWORDS = {
    "i", "a", "an", "the", "is", "are", "was", "were", "to", "of", "in",
    "on", "for", "and", "or", "my", "me", "it", "that", "this", "be",
    "with", "as", "at", "by", "from", "now", "still", "so", "but",
}

# How much of the shorter memory's significant words must also appear in
# the other for two memories to be considered "about the same subject."
_SUPERSEDE_OVERLAP_THRESHOLD = 0.5

_IMPORTANCE_WEIGHT = {
    Importance.LOW: 1,
    Importance.NORMAL: 2,
    Importance.HIGH: 3,
    Importance.PERMANENT: 4,
}


def _significant_words(text: str) -> set:
    words = re.findall(r"[a-z0-9']+", text.lower())
    return {w for w in words if w not in _STOPWORDS and len(w) > 1}


def _same_subject(a: str, b: str) -> bool:
    words_a, words_b = _significant_words(a), _significant_words(b)
    if not words_a or not words_b:
        return False
    shorter = min(len(words_a), len(words_b))
    overlap = len(words_a & words_b)
    return (overlap / shorter) >= _SUPERSEDE_OVERLAP_THRESHOLD


def remember(
    content: str,
    type: MemoryType = MemoryType.FACT,
    importance: Optional[Importance] = None,
    confidence: Confidence = Confidence.USER_EXPLICIT,
    source: str = "user_chat",
    tags: Optional[List[str]] = None,
):
    """Stores a new memory, or supersedes an existing same-subject one of
    the same type. Returns (Memory, None) on success, or (None, reason)
    if the content was refused by the safety filter -- callers must
    surface `reason` to whoever asked for the memory to be stored, not
    swallow it."""

    safe, reason = is_safe_to_remember(content)
    if not safe:
        return None, reason

    if importance is None:
        importance = Importance.PERMANENT if type == MemoryType.LESSON else Importance.NORMAL

    memories = store.load_all()
    new_memory = Memory(
        content=content.strip(),
        type=type,
        importance=importance,
        confidence=confidence,
        source=source,
        tags=tags or [],
    )

    if type in _SUPERSEDABLE_TYPES:
        for existing in memories:
            if not (existing.active and existing.type == type):
                continue
            # If both sides carry tags (e.g. the generic key-based
            # remember(key, value) interface in agent/memory_agent.py,
            # which tags each memory with its key), require a shared tag
            # too -- otherwise two same-type facts filed under different
            # keys could supersede each other just for sharing wording.
            if existing.tags and new_memory.tags and not (set(existing.tags) & set(new_memory.tags)):
                continue
            if _same_subject(existing.content, content):
                existing.active = False
                existing.superseded_by = new_memory.id
                existing.updated_at = time.time()

    memories.append(new_memory)
    store.save_all(memories)
    return new_memory, None


def recall(memory_id: str) -> Optional[Memory]:
    memories = store.load_all()
    for memory in memories:
        if memory.id == memory_id:
            # AUTHORITY.md §2: no more store.save_all() here -- recall()'s
            # entire mutation used to be this one timestamp, so this now
            # writes nothing to the durable store at all. Still updated
            # on the returned object itself so a caller sees a fresh
            # value immediately, not just on the next load_all().
            now = time.time()
            memory.last_accessed = now
            access_log.record_access(memory_id, now)
            return memory
    return None


def _relevance_score(memory: Memory, query: str) -> float:
    score = 0.0

    if query:
        overlap = len(_significant_words(query) & _significant_words(memory.content))
        if overlap == 0:
            return 0.0
        score += overlap * 3

    score += _IMPORTANCE_WEIGHT.get(memory.importance, 1)

    age_days = (time.time() - memory.created_at) / 86400
    score += max(0.0, 3 - age_days * 0.05)

    return score


def search_scored(
    query: str = "",
    type: Optional[MemoryType] = None,
    tags: Optional[List[str]] = None,
    limit: int = 10,
    include_inactive: bool = False,
) -> List[Tuple[Memory, float]]:
    """Same deterministic relevance search as search() (keyword overlap
    with `query`, weighted by importance and recency; empty query = no
    keyword filtering; no embeddings/vector search), but also returns
    each result's score -- for callers that need to explain *why* a
    memory was retrieved (agent/context.py's retrieval observability),
    not just which ones. Updates last_accessed on every returned memory."""

    memories = store.load_all()

    candidates = memories
    if not include_inactive:
        candidates = [m for m in candidates if m.active]
    if type is not None:
        candidates = [m for m in candidates if m.type == type]
    if tags:
        candidates = [m for m in candidates if set(tags) & set(m.tags)]

    scored = [(m, _relevance_score(m, query)) for m in candidates]
    if query:
        scored = [(m, s) for m, s in scored if s > 0]
    scored.sort(key=lambda pair: pair[1], reverse=True)

    results = scored[:limit]

    touched_ids = {m.id for m, _ in results}
    if touched_ids:
        # AUTHORITY.md §2: no more store.save_all() here -- that used to
        # rewrite the ENTIRE durable memory.json on every search that
        # returned at least one result, a read that mutates the durable
        # store on every ordinary retrieval. Now writes only to the
        # access_log sidecar; store.load_all() merges it back in on the
        # next real load. Still updated on the returned objects
        # themselves (results and memories share the same Memory
        # instances -- these are the same objects, not copies) so a
        # caller sees a fresh value immediately.
        now = time.time()
        for m, _ in results:
            m.last_accessed = now
        access_log.record_accesses(touched_ids, now)

    return results


def search(
    query: str = "",
    type: Optional[MemoryType] = None,
    tags: Optional[List[str]] = None,
    limit: int = 10,
    include_inactive: bool = False,
) -> List[Memory]:
    """Deterministic relevance search: keyword overlap with `query`
    (empty query = no keyword filtering), weighted by importance and
    recency. No embeddings/vector search. Updates last_accessed on every
    returned memory."""

    return [m for m, _ in search_scored(query, type, tags, limit, include_inactive)]


def update(memory_id: str, new_content: str, **field_updates):
    """Edits a memory's content in place (keeps its id/history). For "a
    new fact contradicts an old one" without already knowing the specific
    memory_id, prefer remember() with the same type instead -- it
    supersedes automatically."""

    safe, reason = is_safe_to_remember(new_content)
    if not safe:
        return None, reason

    memories = store.load_all()
    for memory in memories:
        if memory.id == memory_id:
            memory.content = new_content.strip()
            memory.updated_at = time.time()
            for key, value in field_updates.items():
                if hasattr(memory, key):
                    setattr(memory, key, value)
            store.save_all(memories)
            return memory, None

    return None, f"No memory with id {memory_id}"


def forget(memory_id: str) -> bool:
    """Permanently removes a memory. To deactivate without deleting, use
    update(memory_id, content, active=False) instead."""

    memories = store.load_all()
    remaining = [m for m in memories if m.id != memory_id]
    if len(remaining) == len(memories):
        return False
    store.save_all(remaining)
    return True


def list_all(type: Optional[MemoryType] = None, include_inactive: bool = False) -> List[Memory]:
    memories = store.load_all()
    if not include_inactive:
        memories = [m for m in memories if m.active]
    if type is not None:
        memories = [m for m in memories if m.type == type]
    return memories


def summarize(type: Optional[MemoryType] = None) -> str:
    memories = list_all(type=type)
    if not memories:
        return "Nothing remembered yet." if type is None else f"No {type.value} memories yet."
    return "\n".join(f"- [{m.type.value}] {m.content}" for m in memories)
