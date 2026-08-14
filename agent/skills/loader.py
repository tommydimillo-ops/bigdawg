"""Loads Skill definitions from disk -- SKILL.md files (YAML-style
frontmatter + a Markdown instructions body), the same shape Claude's own
Agent Skills use. Skill files are untrusted input (agent/skills/safety.py's
module docstring): this only ever parses plain text into a Skill
dataclass. It never imports, executes, evals, or otherwise runs anything
found inside one -- there is no code path from a skill file to Python
execution anywhere in this module.
"""
import os
from pathlib import Path
from typing import List, Optional, Tuple

from agent.observability import log_event
from agent.skills.models import RiskLevel, Skill, SkillSource
from agent.skills.registry import register

# Built-in, first-party skills shipped with the project (repo_root/skills/),
# plus a user-local directory in the same "Application Support" home every
# other CampusPilot store already uses -- so someone can drop in their own
# skill without touching the repo.
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DEFAULT_SKILLS_DIRS = (
    os.path.join(_REPO_ROOT, "skills"),
    os.path.expanduser("~/Library/Application Support/CampusPilot/skills"),
)


def _parse_frontmatter(text: str) -> Tuple[dict, str]:
    """A deliberately narrow, hand-rolled parser -- not a general YAML
    parser (no arbitrary object construction, no anchors/aliases/tags).
    A skill file is untrusted input, and a full YAML parser is far more
    attack surface than the handful of scalar/list fields a skill
    actually needs. Understands `key: value` and a `key:` followed by
    indented `- item` bullets for lists; anything else is ignored rather
    than raising, since a slightly malformed file should degrade to
    "missing that field," not crash the loader for every other skill.
    Returns ({}, text) if there's no valid frontmatter block at all."""
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return {}, text

    end = None
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            end = i
            break
    if end is None:
        return {}, text

    fields: dict = {}
    current_list_key = None
    for line in lines[1:end]:
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith("- ") and current_list_key is not None:
            fields[current_list_key].append(stripped[2:].strip().strip("\"'"))
            continue
        if ":" in line:
            key, _, value = line.partition(":")
            key = key.strip()
            value = value.strip().strip("\"'")
            if value:
                fields[key] = value
                current_list_key = None
            else:
                fields[key] = []
                current_list_key = key
        else:
            current_list_key = None

    body = "\n".join(lines[end + 1:]).strip()
    return fields, body


def _skill_from_text(text: str, path: Optional[str] = None) -> Optional[Skill]:
    fields, body = _parse_frontmatter(text)
    if not fields or not fields.get("name"):
        log_event(
            "skill_load_skipped", component="skills", level="warning",
            path=path or "<inline>", reason="no valid frontmatter or missing name",
        )
        return None

    try:
        risk = RiskLevel(str(fields.get("risk_level", "low")).lower())
    except ValueError:
        risk = RiskLevel.LOW

    enabled = str(fields.get("enabled", "true")).strip().lower() not in ("false", "0", "no")
    capabilities = fields.get("capabilities", [])
    required_tools = fields.get("required_tools", [])

    return Skill(
        name=fields["name"],
        description=fields.get("description", ""),
        version=str(fields.get("version", "1.0")),
        instructions=body,
        capabilities=capabilities if isinstance(capabilities, list) else [],
        required_tools=required_tools if isinstance(required_tools, list) else [],
        risk_level=risk,
        enabled=enabled,
        source=SkillSource.LOCAL,
        path=path,
    )


def load_skills_from_dir(directory: str) -> List[Skill]:
    """Each skill is one subdirectory of `directory` containing a
    SKILL.md -- e.g. skills/research/SKILL.md. Returns the skills that
    loaded and registered successfully; a malformed or invalid one is
    logged and skipped rather than failing the whole batch."""
    if not os.path.isdir(directory):
        return []

    loaded = []
    for entry in sorted(Path(directory).iterdir()):
        if not entry.is_dir():
            continue
        skill_file = entry / "SKILL.md"
        if not skill_file.is_file():
            continue

        try:
            text = skill_file.read_text()
        except OSError as error:
            log_event(
                "skill_load_failed", component="skills", level="warning",
                path=str(skill_file), error_type=type(error).__name__,
            )
            continue

        skill = _skill_from_text(text, path=str(skill_file))
        if skill is None:
            continue

        try:
            register(skill)
            loaded.append(skill)
        except ValueError as error:
            log_event(
                "skill_registration_failed", component="skills", level="warning",
                path=str(skill_file), error=str(error),
            )

    return loaded


def load_all_skills() -> List[Skill]:
    loaded = []
    for directory in DEFAULT_SKILLS_DIRS:
        loaded.extend(load_skills_from_dir(directory))
    return loaded
