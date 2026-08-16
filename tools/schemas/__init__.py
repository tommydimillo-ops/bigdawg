"""Importing this package registers every tool with tools.registry as a
side effect. agent/brain.py imports this before deriving TOOLS from the
registry, so this must stay imported here even though nothing below is
referenced directly -- that's what populates the registry."""
from tools.schemas import (
    agents,
    browsing,
    computer_use,
    execution_control,
    logins_and_email,
    memory_and_learning,
    obsidian,
    openclaw,
    productivity,
    reasoning,
    scheduling,
    skills,
    system,
)

__all__ = [
    "agents",
    "browsing",
    "computer_use",
    "execution_control",
    "logins_and_email",
    "memory_and_learning",
    "obsidian",
    "openclaw",
    "productivity",
    "reasoning",
    "scheduling",
    "skills",
    "system",
]
