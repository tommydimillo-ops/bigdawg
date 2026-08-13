"""Inferred communication patterns -- a thin wrapper over the unified
memory system, preserving these exact function signatures/return formats.

Patterns are stored as PATTERN-type memories with LOW importance and
MODEL_INFERRED confidence -- unlike lessons, never treated as equivalent
to something the user actually said. patterns_as_prompt_text() still
returns every pattern, same as before (kept as a genuine "list everything"
utility) -- but agent/brain.py's actual system-prompt construction no
longer calls it directly. It now goes through agent/context.py instead,
which relevance-filters patterns against the current request rather than
injecting all of them into every single prompt (Phase 3's "retrieve
relevant memories, not dump the whole database" requirement). Lessons
stay always-all-included regardless, since they're hard requirements, not
contextual background.
"""
from agent.memory import Confidence, MemoryType, forget as _forget_memory, list_all, remember

MAX_PATTERNS = 50


def note_pattern(observation):
    memory, error = remember(
        observation, type=MemoryType.PATTERN, confidence=Confidence.MODEL_INFERRED,
        source="model_inferred",
    )
    if error:
        return f"Didn't note that: {error}"

    _enforce_pattern_cap()
    return f"Noted: {observation}"


def _enforce_pattern_cap():
    """Keeps only the most recent MAX_PATTERNS, same cap the original
    raw-list implementation had -- unbounded growth would otherwise
    silently balloon prompt size (and cost) forever."""
    patterns = list_all(type=MemoryType.PATTERN)
    if len(patterns) <= MAX_PATTERNS:
        return
    oldest_first = sorted(patterns, key=lambda m: m.created_at)
    for memory in oldest_first[: len(patterns) - MAX_PATTERNS]:
        _forget_memory(memory.id)


def list_patterns():
    patterns = list_all(type=MemoryType.PATTERN)
    if not patterns:
        return "No patterns noticed yet."
    return "\n".join(f"- {m.content}" for m in patterns)


def forget_pattern(text):
    patterns = list_all(type=MemoryType.PATTERN)
    matching = [m for m in patterns if text.lower() in m.content.lower()]

    if not matching:
        return f"No noted pattern matching '{text}'."

    for memory in matching:
        _forget_memory(memory.id)

    return f"Forgot {len(matching)} pattern(s) matching '{text}'."


def patterns_as_prompt_text():
    patterns = list_all(type=MemoryType.PATTERN)
    if not patterns:
        return ""
    return "\n".join(f"- {m.content}" for m in patterns)
