"""Importing this package registers every tool with tools.registry as a
side effect. agent/brain.py imports this before deriving TOOLS from the
registry, so this must stay imported here even though nothing below is
referenced directly -- that's what populates the registry."""
from tools.schemas import (
    browsing,
    computer_use,
    logins_and_email,
    memory_and_learning,
    productivity,
    reasoning,
    scheduling,
    system,
)

__all__ = [
    "browsing",
    "computer_use",
    "logins_and_email",
    "memory_and_learning",
    "productivity",
    "reasoning",
    "scheduling",
    "system",
]
