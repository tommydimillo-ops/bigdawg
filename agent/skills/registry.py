"""Single source of truth for available skills -- same design philosophy
as tools/registry.py: one place skills are registered, looked up,
enabled/disabled, and searched, so nothing else (the router, the
dashboard, executor.py) needs its own copy of "what skills exist."

Skills are loaded from disk by agent/skills/loader.py, which calls
register() for each valid one it finds; nothing here reads the
filesystem itself, matching tools/registry.py's own separation (the
registry stores specs, it doesn't discover them).
"""
from typing import List, Optional

from agent.skills.models import Skill
from agent.skills.safety import matching_terms, validate_skill

_REGISTRY: dict[str, Skill] = {}


def register(skill: Skill) -> None:
    ok, reason = validate_skill(skill)
    if not ok:
        raise ValueError(f"Skill '{skill.name}' failed validation: {reason}")
    _REGISTRY[skill.name] = skill


def unregister(name: str) -> bool:
    return _REGISTRY.pop(name, None) is not None


def get(name: str) -> Optional[Skill]:
    return _REGISTRY.get(name)


def list_skills(enabled_only: bool = False) -> List[Skill]:
    skills = list(_REGISTRY.values())
    if enabled_only:
        skills = [s for s in skills if s.enabled]
    return skills


def search(query: str, limit: int = 5) -> List[Skill]:
    """Deterministic keyword-overlap search, no model call -- see
    Skill.matches() for the scoring. Returns only skills that actually
    scored above zero, best match first."""
    terms = matching_terms(query)
    if not terms:
        return []
    scored = [(skill.matches(terms), skill) for skill in _REGISTRY.values() if skill.enabled]
    scored = [(score, skill) for score, skill in scored if score > 0]
    scored.sort(key=lambda pair: pair[0], reverse=True)
    return [skill for _, skill in scored[:limit]]


def set_enabled(name: str, enabled: bool) -> bool:
    """Returns True if `name` exists and was updated. Skills are frozen
    dataclasses (like tools.registry.ToolSpec), so this replaces the
    stored entry with a copy rather than mutating it in place."""
    skill = _REGISTRY.get(name)
    if skill is None:
        return False
    from dataclasses import replace
    _REGISTRY[name] = replace(skill, enabled=enabled)
    return True


def clear() -> None:
    """Test-only: drops every registered skill. Never called from
    application code -- the loader only ever adds skills, it never needs
    to reset the whole registry."""
    _REGISTRY.clear()
