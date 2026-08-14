"""Determines which specialist agent (if any) should handle a request --
never executes anything itself (section 12 of the Phase 7 spec: "The
router must not execute the task"), the same separation agent.skills.
router keeps between recommending a skill and agent/executor.py deciding
what to do with that recommendation.

Distinct axis from agent/delegation.py: delegation.py decides which
CAPABILITY LAYER handles a request (the ordinary native-tool loop vs. a
Claude Skill vs. -- when available -- Cowork). This module decides which
SPECIALIST handles it (coding vs. research vs. QA vs. memory vs. no
specialist at all). A request can be routed to CodingAgent by this
module and still end up on the native-tool capability layer per
delegation.py -- they answer different questions and are deliberately
not merged.

Deterministic keyword-overlap scoring, no model call -- matching this
project's standing policy of never letting an LLM make a routing
decision (see agent.autonomy's module docstring, and agent.skills.router,
for the same reasoning applied to permission and skill-matching
decisions respectively). Phrases are weighted (2 points for a strong,
fairly unambiguous signal like "research the" or "remember that", 1 for
a weaker/generic one) rather than flat-counted, the same way agent.
skills.models.Skill.matches() weights a capability hit above a plain
name/description word hit -- a single strong phrase should be able to
route on its own; a single weak, generic word shouldn't.
"""
from dataclasses import dataclass
from enum import Enum
from typing import Dict, Optional

from agent.agents.models import AgentTaskType

# Below this score, a "match" is too weak to route on -- one incidental
# shared word shouldn't be enough to hand a request to a specialist
# instead of the ordinary direct path. Mirrors agent.skills.router's
# MIN_CONFIDENCE_SCORE for the same reason.
MIN_CONFIDENCE_SCORE = 2


class AgentDestination(str, Enum):
    DIRECT = "direct"
    CODING = "coding"
    RESEARCH = "research"
    QA = "qa"
    MEMORY = "memory"


@dataclass(frozen=True)
class AgentDecision:
    destination: AgentDestination
    reason: str
    agent_name: Optional[str] = None
    confidence: float = 0.0
    requires_confirmation: bool = False
    task_type: Optional[str] = None


# phrase -> weight. Kept explicit and readable rather than derived from
# anything dynamic -- easy to extend, easy to unit test exhaustively, and
# there's no registry of "routable task types" the way agent.skills.
# registry holds installed skills (these four destinations are fixed for
# this phase; see the module docstring's note on future model-based
# routing). Debugging a failing test ("why is this test failing") is
# weighted as CODING, not QA -- QA here means validating/running tests,
# not fixing the code a failing test points at.
_CODING_PHRASES = {
    "python error": 2, "fix this": 2, "fix the": 2, "refactor": 2,
    "add a feature": 2, "new feature": 2, "test is failing": 2,
    "tests are failing": 2, "software bug": 2,
    "syntax error": 2, "pull request": 2,
    "python": 1, "javascript": 1, "typescript": 1, "bug": 1, "traceback": 1,
    "function": 1, "class ": 1, "module": 1, "script": 1, "repository": 1,
    "repo": 1, "git": 1, "commit": 1, "debug": 1, "compile": 1,
    "variable": 1, "programming": 1, "algorithm": 1, "codebase": 1,
    "error": 1,
}
_RESEARCH_PHRASES = {
    "research the": 2, "research a": 2, "research on": 2, "do research": 2,
    "look into": 2, "look up": 2, "find out about": 2, "cross-check": 2,
    "compare the": 2,
    "research": 1, "investigate": 1, "sources": 1, "reviews of": 1,
}
_QA_PHRASES = {
    "check whether": 2, "still pass": 2, "still passing": 2,
    "run the tests": 2, "all tests": 2, "quality check": 2,
    "pass all": 2,
    "regression": 1, "verify": 1, "validate": 1, "qa": 1,
}
_MEMORY_PHRASES = {
    "remember that": 2, "remember to": 2, "forget that": 2,
    "note that": 2, "keep in mind": 2, "don't forget": 2,
    "my preference": 2, "i prefer": 2,
    "recall": 1,
}

_PHRASES_BY_DESTINATION: Dict[AgentDestination, dict] = {
    AgentDestination.CODING: _CODING_PHRASES,
    AgentDestination.RESEARCH: _RESEARCH_PHRASES,
    AgentDestination.QA: _QA_PHRASES,
    AgentDestination.MEMORY: _MEMORY_PHRASES,
}

_TASK_TYPE_BY_DESTINATION = {
    AgentDestination.CODING: AgentTaskType.CODING.value,
    AgentDestination.RESEARCH: AgentTaskType.RESEARCH.value,
    AgentDestination.QA: AgentTaskType.QA.value,
    AgentDestination.MEMORY: AgentTaskType.MEMORY.value,
}


def _score(request_lower: str, phrases: dict) -> int:
    return sum(weight for phrase, weight in phrases.items() if phrase in request_lower)


def route(request: str) -> AgentDecision:
    """Returns the best-matching specialist destination, or DIRECT if
    nothing scores high enough (including an empty request). Ties are
    broken by iteration order (CODING, RESEARCH, QA, MEMORY) and, below
    the confidence floor, always resolve to DIRECT -- the least
    assumption, matching agent.delegation.decide()'s NATIVE_TOOL default
    when no skill matches confidently."""
    if not request or not request.strip():
        return AgentDecision(destination=AgentDestination.DIRECT, reason="empty request")

    lowered = request.lower()
    scored = [
        (_score(lowered, phrases), destination)
        for destination, phrases in _PHRASES_BY_DESTINATION.items()
    ]
    scored.sort(key=lambda pair: pair[0], reverse=True)
    best_score, best_destination = scored[0]

    if best_score < MIN_CONFIDENCE_SCORE:
        return AgentDecision(
            destination=AgentDestination.DIRECT,
            reason="no specialist phrase match scored above the confidence threshold",
        )

    word_count = max(1, len(lowered.split()))
    confidence = min(1.0, best_score / word_count * 3)

    return AgentDecision(
        destination=best_destination,
        reason=f"matched score {best_score} for {best_destination.value}",
        agent_name=best_destination.value,
        confidence=confidence,
        task_type=_TASK_TYPE_BY_DESTINATION[best_destination],
    )
