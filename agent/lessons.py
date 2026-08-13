"""Standing rules from user corrections -- a thin wrapper over the
unified memory system (agent/memory/), preserving these exact function
signatures/return formats so nothing importing them needs to change.

Lessons are stored as LESSON-type memories with PERMANENT importance,
matching their original meaning ("hard requirements, follow every one of
them"). Unlike patterns, lessons are never relevance-filtered out of the
system prompt (see agent/context.py) -- lessons_as_prompt_text() still
always returns every one of them.

One real behavior change from before: a new lesson about the same subject
as an existing one now supersedes it instead of both persisting forever
as silently contradictory standing rules (Phase 3's memory-supersession
requirement) -- e.g. "call me boss" then later "actually use my name
instead" now correctly replaces the old rule rather than leaving the
model to arbitrarily pick between two standing instructions that
disagree. learn_rule() can also now refuse a rule that fails the memory
safety filter (e.g. reads as an injected instruction) -- previously it
always accepted whatever text it was given.
"""
from agent.memory import MemoryType, list_all, remember


def learn_rule(rule):
    memory, error = remember(rule, type=MemoryType.LESSON)
    if error:
        return f"Didn't save that as a standing rule: {error}"
    return f"Got it — from now on: {rule}"


def list_rules():
    lessons = list_all(type=MemoryType.LESSON)
    if not lessons:
        return "No standing rules learned yet."
    return "\n".join(f"- {m.content}" for m in lessons)


def lessons_as_prompt_text():
    """Blank string when there are none, so the base prompt is unchanged
    until the user actually teaches Jarvis something."""
    lessons = list_all(type=MemoryType.LESSON)
    if not lessons:
        return ""
    return "\n".join(f"- {m.content}" for m in lessons)
