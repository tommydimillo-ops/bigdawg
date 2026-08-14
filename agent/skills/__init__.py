"""Skills: structured instruction bundles Jarvis can hand to Claude as
extra workflow guidance for a specific kind of request. Never a second
way to execute code or call a tool -- see agent/skills/safety.py for the
actual security boundary (it isn't anything in this package; it's that
tools.registry/agent.autonomy/agent.executor don't know or care what a
system prompt says).

Importing this package does not load any skills from disk -- call
agent.skills.loader.load_all_skills() explicitly (agent/executor.py does
this once, the same way tools.schemas populates tools.registry)."""
from agent.skills.models import RiskLevel, Skill, SkillSource
from agent.skills.registry import get, list_skills, register, search, set_enabled, unregister
from agent.skills.router import RouteRecommendation, route

__all__ = [
    "Skill", "RiskLevel", "SkillSource",
    "register", "unregister", "get", "list_skills", "search", "set_enabled",
    "route", "RouteRecommendation",
]
