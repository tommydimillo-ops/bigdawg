"""Formalizes the existing Claude-first/OpenAI-fallback behavior behind a
small interface, without changing that behavior at all. The *choice* of
which provider to try is trivial and static today (always primary, then
fallback only if a live call to primary fails) -- what actually decides
*when* the fallback fires is agent/executor.py's try/except around the
real API call, which this module deliberately doesn't touch, since a
provider failing is inherently something you only find out by trying it.

This exists so a future phase can add real routing logic (task type,
recent provider health, cost) by changing select()'s body, without
executor.py needing to change at all -- it already just asks the router.
"""
from dataclasses import dataclass
from typing import Optional

from config.settings import settings


@dataclass(frozen=True)
class ModelChoice:
    provider: str  # "anthropic" | "openai"
    model: str


def primary_choice() -> ModelChoice:
    return ModelChoice(provider="anthropic", model=settings.default_model)


def fallback_choice() -> ModelChoice:
    return ModelChoice(provider="openai", model=settings.fallback_model)


def select(attempt: int = 0, context=None) -> ModelChoice:
    """attempt=0 -> primary (Claude), attempt>=1 -> fallback (OpenAI).
    `context` is accepted and currently unused -- reserved for when
    routing actually considers task/request information; the parameter
    exists now so callers don't need to change when that lands."""
    return primary_choice() if attempt == 0 else fallback_choice()
