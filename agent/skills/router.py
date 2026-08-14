"""Determines whether a request could benefit from a skill -- never
executes anything itself. Deterministic keyword/capability overlap
scoring against the registry (agent.skills.registry.search()), no model
call, mirroring this project's established policy of never letting an
LLM make a routing/permission-relevant decision (agent.autonomy.
should_request_confirmation is the same kind of function). The caller
(agent/executor.py) decides what to do with the recommendation; this
module only produces it.
"""
from dataclasses import dataclass
from typing import List, Optional

from agent.skills import registry
from agent.skills.models import Skill

# Below this score, a "match" is too weak to act on -- one incidental
# shared word (e.g. both the request and a skill's description happen to
# contain "report") shouldn't be enough to attach a whole skill's
# instructions to the prompt.
MIN_CONFIDENCE_SCORE = 2


@dataclass(frozen=True)
class RouteRecommendation:
    skill: Optional[Skill]
    confidence: float  # 0.0-1.0, relative to the best possible score for this request
    reason: str

    @property
    def matched(self) -> bool:
        return self.skill is not None


def route(request: str, candidates: Optional[List[Skill]] = None) -> RouteRecommendation:
    """Returns the best-matching enabled skill for `request`, or a
    no-match recommendation if nothing scores high enough. `candidates`
    is injectable for testing; defaults to every enabled registered
    skill."""
    if not request or not request.strip():
        return RouteRecommendation(skill=None, confidence=0.0, reason="empty request")

    pool = candidates if candidates is not None else registry.list_skills(enabled_only=True)
    if not pool:
        return RouteRecommendation(skill=None, confidence=0.0, reason="no skills registered")

    from agent.skills.safety import matching_terms
    terms = matching_terms(request)
    if not terms:
        return RouteRecommendation(skill=None, confidence=0.0, reason="no matchable terms in request")

    scored = [(skill.matches(terms), skill) for skill in pool]
    scored.sort(key=lambda pair: pair[0], reverse=True)
    best_score, best_skill = scored[0]

    if best_score < MIN_CONFIDENCE_SCORE:
        return RouteRecommendation(
            skill=None, confidence=0.0,
            reason=f"best match ({best_skill.name if best_skill else 'none'}) scored below the confidence threshold",
        )

    # A rough, bounded confidence: the best possible score for this
    # request would be every term hitting the name/description (1 point
    # each) -- capped at 1.0 since a skill can also earn capability bonus
    # points (worth 2 each) that would otherwise push this over 1.0.
    confidence = min(1.0, best_score / max(1, len(terms)))

    return RouteRecommendation(
        skill=best_skill, confidence=confidence,
        reason=f"matched {best_score} term(s) against '{best_skill.name}' (capabilities: {', '.join(best_skill.capabilities) or 'none listed'})",
    )
