"""Deterministic task classification for Phase 9 Milestone 2's task-aware
model router (agent/model_router.py). Keyword/pattern-based on purpose --
the same "never let a model call decide a routing outcome" philosophy
already enforced everywhere else in this project (agent/skills/router.py,
agent/delegation.py, agent/autonomy.py). Which LLM answers a request is
itself a routing decision, so it stays out of the model's own hands the
same way permission and skill-matching already are -- and it's cheap: no
extra API call is spent just to decide which API call to make.

A bounded set of categories, not an open-ended taxonomy -- matching this
project's existing "use existing conventions instead of dozens of
unnecessary categories" bias. CLASSIFICATION and VOICE exist in TaskType
for completeness/documentation (Phase 9 Milestone 2's own suggested
category list) but classify() -- which only ever looks at request text --
doesn't produce them directly: classification-flavored calls are
internal (the planner's own decomposition step, not something a user
types), and "voice" is about the request's source, not its content, so
it's folded into `source` below instead of guessed from wording.
"""
import re
from dataclasses import dataclass
from enum import Enum


class TaskType(str, Enum):
    CODING = "coding"
    DEBUGGING = "debugging"
    RESEARCH_CURRENT = "research_current"
    RESEARCH_GENERAL = "research_general"
    REASONING_COMPLEX = "reasoning_complex"
    REASONING_SIMPLE = "reasoning_simple"
    WRITING = "writing"
    VISION = "vision"
    VOICE = "voice"
    TOOL_USE = "tool_use"
    SUMMARIZATION = "summarization"
    CLASSIFICATION = "classification"
    PLANNING = "planning"
    DOCUMENT_ANALYSIS = "document_analysis"
    GENERAL_CHAT = "general_chat"


@dataclass(frozen=True)
class TaskRequirements:
    """What a candidate provider/model must satisfy (or is preferred to
    satisfy) to handle this request -- the router's own input, kept
    separate from TaskType itself since two requests of the same type
    can still carry different requirements (a short vs. a long coding
    question, e.g.)."""
    task_type: TaskType
    needs_current_web: bool = False
    needs_vision: bool = False
    needs_tools: bool = True
    needs_large_context: bool = False
    privacy_sensitive: bool = False
    latency_priority: bool = False
    quality_priority: bool = False
    cost_priority: bool = False


_VISION_PATTERN = re.compile(
    r"\b(screenshot|on my screen|screen right now|this image|this photo|"
    r"this picture|what.?s (on|in) (my )?(screen|image|photo|picture))\b",
    re.IGNORECASE,
)
_DEBUG_PATTERN = re.compile(
    r"\b(debug|fix (this|the|my) (bug|error|issue|code)|why (is|does|isn'?t|won'?t)|"
    r"not working|crashes?|stack ?trace|traceback|exception)\b",
    re.IGNORECASE,
)
_CODING_PATTERN = re.compile(
    r"\b(code|coding|function|refactor|python|javascript|typescript|"
    r"class |compile|syntax error|unit test|pull request|repository|"
    r"codebase|api endpoint|script|regex|algorithm)\b",
    re.IGNORECASE,
)
_CURRENT_EVENTS_PATTERN = re.compile(
    r"\b(today|right now|currently|this week|latest|recent(ly)?|breaking|"
    r"news|stock price|score|happening now|as of (today|now)|"
    r"current events|up.?to.?date)\b",
    re.IGNORECASE,
)
_RESEARCH_PATTERN = re.compile(
    # "compare" deliberately excluded -- it's a reasoning-complexity
    # signal (_COMPLEX_SIGNALS below) more often than a research one
    # ("compare these two options" doesn't imply needing new
    # information), and research is checked first in classify()'s
    # if/elif chain, so including it here would shadow that signal.
    r"\b(research|find (out|information)|look up|sources?|citations?|"
    r"investigate)\b",
    re.IGNORECASE,
)
_WRITING_PATTERN = re.compile(r"\b(write|draft|compose|essay|blog post|article)\b", re.IGNORECASE)
_PLANNING_PATTERN = re.compile(r"\b(plan|itinerary|roadmap|organize (my|a|the))\b", re.IGNORECASE)
_SUMMARIZE_PATTERN = re.compile(r"\b(summarize|summary|tl;?dr|shorten|condense)\b", re.IGNORECASE)
_DOCUMENT_PATTERN = re.compile(r"\b(document|pdf|contract|this file|attached|read this)\b", re.IGNORECASE)
_PRIVACY_PATTERN = re.compile(
    r"\b(private|confidential|sensitive|don'?t (send|share)|keep (this )?"
    r"(local|offline)|offline only)\b",
    re.IGNORECASE,
)
_COMPLEX_SIGNALS = re.compile(
    r"\band then\b|\bafter that\b|\bcompare\b|\band also\b|\bstep by step\b|"
    r"\bcarefully\b|\bin depth\b|\bthoroughly\b",
    re.IGNORECASE,
)

_LONG_REQUEST_WORDS = 60
_SHORT_REQUEST_WORDS = 6


def classify(text: str, source: str = "chat") -> TaskRequirements:
    """Pure text classification -- no network call, no model call, same
    request text in always produces the same TaskRequirements out."""
    text = text or ""
    word_count = len(text.split())

    needs_vision = bool(_VISION_PATTERN.search(text))
    needs_current_web = bool(_CURRENT_EVENTS_PATTERN.search(text))
    privacy_sensitive = bool(_PRIVACY_PATTERN.search(text))
    is_long = word_count > _LONG_REQUEST_WORDS

    if needs_vision:
        task_type = TaskType.VISION
    elif _DEBUG_PATTERN.search(text):
        task_type = TaskType.DEBUGGING
    elif _CODING_PATTERN.search(text):
        task_type = TaskType.CODING
    elif needs_current_web or _RESEARCH_PATTERN.search(text):
        task_type = TaskType.RESEARCH_CURRENT if needs_current_web else TaskType.RESEARCH_GENERAL
    elif _SUMMARIZE_PATTERN.search(text):
        task_type = TaskType.SUMMARIZATION
    elif _DOCUMENT_PATTERN.search(text):
        task_type = TaskType.DOCUMENT_ANALYSIS
    elif _WRITING_PATTERN.search(text):
        task_type = TaskType.WRITING
    elif _PLANNING_PATTERN.search(text):
        task_type = TaskType.PLANNING
    elif _COMPLEX_SIGNALS.search(text) or is_long:
        task_type = TaskType.REASONING_COMPLEX
    elif 0 < word_count <= _SHORT_REQUEST_WORDS:
        task_type = TaskType.REASONING_SIMPLE
    else:
        task_type = TaskType.GENERAL_CHAT

    return TaskRequirements(
        task_type=task_type,
        needs_current_web=needs_current_web,
        needs_vision=needs_vision,
        needs_tools=True,  # every real Jarvis request runs through the same tool-capable loop
        needs_large_context=is_long or task_type == TaskType.DOCUMENT_ANALYSIS,
        privacy_sensitive=privacy_sensitive,
        # Voice is a latency-sensitive interface regardless of what the
        # words themselves suggest -- nobody wants a long pause before
        # Jarvis starts speaking.
        latency_priority=task_type == TaskType.REASONING_SIMPLE or source == "voice",
        quality_priority=task_type in (TaskType.CODING, TaskType.DEBUGGING, TaskType.REASONING_COMPLEX),
        cost_priority=task_type in (TaskType.REASONING_SIMPLE, TaskType.CLASSIFICATION, TaskType.SUMMARIZATION),
    )
