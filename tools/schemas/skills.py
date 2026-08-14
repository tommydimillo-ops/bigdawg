"""Visibility into, and control over, installed skills (Phase 6.5) --
distinct from every tool-registering module here in that these act on
Jarvis's own skill registry, not the world outside it. Enabling/disabling
a skill only ever changes whether agent.delegation.decide() can match it
for a future request; it never changes what tools are allowed or skips a
permission/confirmation check for anything that skill's instructions
might lead to."""
from agent.skills.registry import list_skills as _list_skills
from agent.skills.registry import set_enabled
from tools.registry import ToolSpec, register


def _view_skills(_tool_input: dict) -> str:
    skills = _list_skills()
    if not skills:
        return "No skills installed."
    lines = []
    for skill in sorted(skills, key=lambda s: s.name):
        status = "enabled" if skill.enabled else "disabled"
        lines.append(
            f"- {skill.name} (v{skill.version}, {status}, risk={skill.risk_level.value}): "
            f"{skill.description}"
        )
        if skill.capabilities:
            lines.append(f"    capabilities: {', '.join(skill.capabilities)}")
    return "\n".join(lines)


def _set_skill_enabled(tool_input: dict, enabled: bool) -> str:
    name = (tool_input.get("name") or "").strip()
    if not name:
        return "A skill name is required -- call view_skills first to see what's installed."
    if set_enabled(name, enabled):
        return f"{'Enabled' if enabled else 'Disabled'} skill '{name}'."
    return f"No skill named '{name}' is installed."


register(ToolSpec(
    name="view_skills",
    description=(
        "List installed skills -- name, version, enabled/disabled state, risk "
        "level, description, and capabilities. Use when the user asks what "
        "skills Jarvis has, or wants to enable/disable one and needs the exact "
        "name first."
    ),
    input_schema={"type": "object", "properties": {}, "required": []},
    permission_level=0,
    handler=_view_skills,
    parallel_safe=True,
))

register(ToolSpec(
    name="enable_skill",
    description="Enable a previously disabled skill by name (see view_skills).",
    input_schema={
        "type": "object",
        "properties": {"name": {"type": "string", "description": "The exact skill name, from view_skills."}},
        "required": ["name"],
    },
    permission_level=1,
    handler=lambda ti: _set_skill_enabled(ti, True),
))

register(ToolSpec(
    name="disable_skill",
    description="Disable a skill by name (see view_skills) so it's never matched for future requests.",
    input_schema={
        "type": "object",
        "properties": {"name": {"type": "string", "description": "The exact skill name, from view_skills."}},
        "required": ["name"],
    },
    permission_level=1,
    handler=lambda ti: _set_skill_enabled(ti, False),
))
