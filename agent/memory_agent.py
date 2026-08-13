"""Generic key-based fact storage -- a thin wrapper over the unified
memory system. In practice this is only ever called with key="notes" (by
the remember_fact/recall_facts tools and the menu-bar app's "Recent
Notes"), but preserves the original generic-key interface: each memory is
tagged with its key, so remember(key, value)/recall(key) for a different
key would address its own independent group of memories.

One real behavior change from before: a new fact about the same subject
as an existing one (sharing a key/tag) now supersedes it instead of both
persisting forever as contradictory facts -- e.g. remembering "I prefer
dark mode" and later "I prefer light mode now" now correctly replaces the
old fact. remember() can also now refuse content that fails the memory
safety filter (a credential, or something that reads as an injected
instruction) -- previously it always accepted whatever text it was given.
"""
from agent.memory import Confidence, MemoryType, list_all
from agent.memory import remember as _remember_memory


def remember(key, value):
    _, error = _remember_memory(
        value, type=MemoryType.FACT, confidence=Confidence.USER_EXPLICIT, tags=[key],
    )
    if error:
        return f"Didn't save that: {error}"
    return f"I'll remember that {value}"


def recall(key):
    facts = list_all(type=MemoryType.FACT)
    matching = [m for m in facts if key in m.tags]

    if not matching:
        return "I don't remember that yet."

    return "; ".join(m.content for m in matching)
