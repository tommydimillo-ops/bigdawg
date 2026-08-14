"""Remembered facts, learned rules, and noticed communication patterns."""
import agent.personal_context as personal_context
from agent.lessons import learn_rule, list_rules
from agent.memory_agent import recall, remember
from agent.patterns import forget_pattern, list_patterns, note_pattern
from tools.registry import ToolSpec, register


def _get_personal_context(_tool_input):
    # Builds the catalog on first use rather than requiring a separate
    # "set this up" step -- load_catalog() alone would just say "no
    # catalog yet" forever, since nothing else in the app ever calls
    # refresh_catalog(). Later calls reuse the saved catalog instead of
    # re-scanning the disk/Photos library on every single read.
    if personal_context.load_catalog() is None:
        personal_context.refresh_catalog()
    return personal_context.summary_text()


def _refresh_personal_context(_tool_input):
    personal_context.refresh_catalog()
    return personal_context.summary_text()


register(ToolSpec(
    name="get_personal_context",
    description=(
        "Read the user's privacy-preserving local context summary: aggregate "
        "document counts, recurring course codes, and photo-library date/count "
        "metadata. It contains no raw filenames, content, pixels, or locations. "
        "Use when the user asks what Jarvis knows about them or their files. "
        "Builds the summary on first use, then reuses it -- call "
        "refresh_personal_context instead if the user says their files changed."
    ),
    input_schema={"type": "object", "properties": {}, "required": []},
    permission_level=0,
    handler=_get_personal_context,
    parallel_safe=True,
))

register(ToolSpec(
    name="refresh_personal_context",
    description=(
        "Re-scan the user's document folders and photo library to refresh "
        "the aggregate-only personal context summary (see get_personal_context). "
        "Use when the user says they've added/removed files or wants an "
        "updated count -- not needed for routine reads."
    ),
    input_schema={"type": "object", "properties": {}, "required": []},
    permission_level=1,
    handler=_refresh_personal_context,
))

register(ToolSpec(
    name="remember_fact",
    description="Store a fact the user wants remembered for later, phrased as a clean standalone statement.",
    input_schema={
        "type": "object",
        "properties": {
            "fact": {"type": "string", "description": "The fact to remember."}
        },
        "required": ["fact"],
    },
    permission_level=1,
    handler=lambda ti: remember("notes", ti["fact"]),
    side_effect=True,
))

register(ToolSpec(
    name="recall_facts",
    description="Retrieve everything remembered so far. Use when the user asks what you remember or refers to something they told you to remember earlier.",
    input_schema={"type": "object", "properties": {}, "required": []},
    permission_level=0,
    handler=lambda ti: recall("notes"),
    parallel_safe=True,
))

register(ToolSpec(
    name="learn_rule",
    description=(
        "Save a standing behavioral rule from a correction the user "
        "gave you (e.g. 'don't do X anymore', 'always do Y instead'). "
        "This becomes a permanent instruction included in every future "
        "conversation, not just something recalled when asked."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "rule": {
                "type": "string",
                "description": (
                    "The rule, phrased as a clear standalone instruction "
                    "to yourself, e.g. 'Never use open_browser for "
                    "Amazon searches, use open_application first.'"
                ),
            }
        },
        "required": ["rule"],
    },
    permission_level=1,
    handler=lambda ti: learn_rule(ti["rule"]),
    side_effect=True,
))

register(ToolSpec(
    name="list_rules",
    description="List every standing rule learned so far via learn_rule. Use when the user asks what you've learned or what rules you're following.",
    input_schema={"type": "object", "properties": {}, "required": []},
    permission_level=0,
    handler=lambda ti: list_rules(),
    parallel_safe=True,
))

register(ToolSpec(
    name="note_pattern",
    description=(
        "Record an observation about how this user communicates or "
        "what they tend to want — call this proactively when you "
        "notice something, don't wait to be asked. These are your own "
        "fallible inferences (shown to you in future conversations as "
        "soft context, not hard rules), used to understand casual/"
        "shorthand phrasing better over time."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "observation": {
                "type": "string",
                "description": "The pattern, phrased short and concrete, e.g. \"When the user says 'check the board', they mean list_upcoming.\"",
            }
        },
        "required": ["observation"],
    },
    permission_level=1,
    handler=lambda ti: note_pattern(ti["observation"]),
))

register(ToolSpec(
    name="list_patterns",
    description="List every pattern noticed so far via note_pattern. Use when the user asks what you've picked up on about how they talk, or wants to audit/correct it.",
    input_schema={"type": "object", "properties": {}, "required": []},
    permission_level=0,
    handler=lambda ti: list_patterns(),
))

register(ToolSpec(
    name="forget_pattern",
    description="Remove a previously noted pattern that turned out to be wrong, by matching text.",
    input_schema={
        "type": "object",
        "properties": {
            "text": {"type": "string", "description": "Text to match against noted patterns."}
        },
        "required": ["text"],
    },
    permission_level=1,
    handler=lambda ti: forget_pattern(ti["text"]),
))
