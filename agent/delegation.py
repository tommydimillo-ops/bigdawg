"""The delegation policy -- decides which capability layer (native tools
via the normal loop, a Claude Skill, or -- when available -- Cowork)
should handle a request. This never executes anything; agent/executor.py
is the only thing that acts on a DelegationDecision, the same way it
already only ever acts on agent.autonomy.Decision without that module
touching a tool itself.

Provider selection (Claude vs OpenAI vs a future local model) is a
separate, existing, untouched concern -- agent/model_router.py -- this is
only about which skill/capability layer applies, not which model
provider ends up serving the request. Deterministic wherever possible:
skill matching (agent.skills.router) is a plain keyword-overlap score,
never a model call, matching this project's standing policy of never
letting an LLM make a routing or permission-relevant decision (see
agent/autonomy.py's module docstring for the same reasoning).
"""
from dataclasses import dataclass
from enum import Enum
from typing import Optional

from agent.skills import router as skill_router
from agent.skills.models import RiskLevel


class DelegationDestination(str, Enum):
    NATIVE_TOOL = "native_tool"
    CLAUDE = "claude"
    CLAUDE_SKILL = "claude_skill"
    COWORK = "cowork"
    OPENAI = "openai"
    LOCAL_MODEL = "local_model"
    NONE = "none"


@dataclass(frozen=True)
class DelegationDecision:
    destination: DelegationDestination
    reason: str
    skill: Optional[str] = None
    confidence: float = 0.0
    risk_level: str = RiskLevel.LOW.value
    # Informational only -- a HIGH-risk skill being matched at all is
    # worth surfacing (dashboard, logs), but attaching skill instructions
    # to a prompt has no real-world side effect of its own. Whatever
    # tool calls actually result still go through the ordinary, unrelated
    # confirmation gate (agent.autonomy) exactly as they always have --
    # this field never skips or replaces that.
    requires_confirmation: bool = False


def decide(request: str) -> DelegationDecision:
    """The one entry point agent/executor.py calls, mirroring exactly
    where agent.planner.is_complex()/create_plan() already run. Returns
    NATIVE_TOOL (the ordinary, unmodified path) when no skill matches
    confidently -- CLAUDE_SKILL only when one genuinely does."""

    recommendation = skill_router.route(request)

    if not recommendation.matched:
        return DelegationDecision(
            destination=DelegationDestination.NATIVE_TOOL,
            reason=recommendation.reason,
            confidence=recommendation.confidence,
        )

    skill = recommendation.skill
    return DelegationDecision(
        destination=DelegationDestination.CLAUDE_SKILL,
        reason=recommendation.reason,
        skill=skill.name,
        confidence=recommendation.confidence,
        risk_level=skill.risk_level.value,
        requires_confirmation=(skill.risk_level == RiskLevel.HIGH),
    )
