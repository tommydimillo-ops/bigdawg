"""The Skill data model -- structured metadata plus instructions, nothing
executable. A Skill is a bundle of workflow guidance Jarvis can hand to
Claude as extra system-prompt context for a specific kind of request
(research, data analysis, document creation, ...); it is never a second
way to run code or call a tool. Whatever the model does as a result of
reading a skill's instructions still flows through the exact same
tools.registry / agent.autonomy / agent.executor path every other request
uses -- see agent/skills/safety.py for how instructions are wrapped
before ever reaching a prompt, and agent/executor.py for where that
happens.
"""
from dataclasses import dataclass, field
from enum import Enum
from typing import List, Optional


class RiskLevel(str, Enum):
    """A skill's own declared risk -- purely informational/routing
    metadata, distinct from tools.registry.ToolSpec.permission_level
    (which is what's actually enforced). A LOW-risk skill can still
    trigger a HIGH permission_level tool call; that call is gated exactly
    as it always would be, regardless of what this says."""
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class SkillSource(str, Enum):
    BUILTIN = "builtin"
    LOCAL = "local"


@dataclass(frozen=True)
class Skill:
    name: str
    description: str
    version: str
    instructions: str
    capabilities: List[str] = field(default_factory=list)
    required_tools: List[str] = field(default_factory=list)
    risk_level: RiskLevel = RiskLevel.LOW
    enabled: bool = True
    source: SkillSource = SkillSource.LOCAL
    metadata: dict = field(default_factory=dict)
    path: Optional[str] = None  # where this was loaded from, for diagnostics/reload

    def matches(self, query_terms: List[str]) -> int:
        """Cheap, deterministic overlap score against `query_terms`
        (already-lowercased words) -- used by agent.skills.router, not a
        model call. Counts a hit in the name/description once each and
        every matching capability, so a skill whose capabilities line up
        closely with the request scores higher than one that only shares
        a word in its description."""
        haystack = f"{self.name} {self.description}".lower()
        score = sum(1 for term in query_terms if term in haystack)
        for capability in self.capabilities:
            capability_lower = capability.lower()
            if any(term in capability_lower or capability_lower in term for term in query_terms):
                score += 2
        return score
