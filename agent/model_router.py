"""Formalized provider selection. primary_choice()/fallback_choice()/
select() are the original Phase 1 interface, kept completely unchanged
(same signatures, same static Claude-then-OpenAI outcome, same tests) --
nothing here removes the simple path other callers may still reasonably
want.

build_fallback_chain() (Phase 9 Milestone 2) is the new, real entry
point: task requirements in, an ordered, filtered ModelChoice candidate
list out. It's additive, not a replacement -- see its own docstring for
the exact filter order and agent/executor.py for where it's actually
consulted. Deterministic throughout, per this project's standing rule
that a routing decision is never delegated to a model call (agent/
skills/router.py, agent/delegation.py, agent/autonomy.py are the
existing examples of the same principle).
"""
from dataclasses import dataclass
from typing import Dict, List, Optional

from agent.provider_budget import is_provider_within_budget
from agent.task_classifier import TaskRequirements, TaskType
from config.settings import settings


@dataclass(frozen=True)
class ModelChoice:
    provider: str  # "anthropic" | "openai" | "xai" | "perplexity"
    model: str


def primary_choice() -> ModelChoice:
    return ModelChoice(provider="anthropic", model=settings.default_model)


def fallback_choice() -> ModelChoice:
    return ModelChoice(provider="openai", model=settings.fallback_model)


def select(attempt: int = 0, context=None) -> ModelChoice:
    """attempt=0 -> primary (Claude), attempt>=1 -> fallback (OpenAI).
    `context` is accepted and currently unused -- kept exactly as before
    for any caller still using the simple two-tier interface. Real
    task-aware routing lives in build_fallback_chain() instead of here,
    so this function's own long-documented behavior never has to change
    out from under an existing caller."""
    return primary_choice() if attempt == 0 else fallback_choice()


@dataclass(frozen=True)
class ProviderCapabilities:
    supports_text: bool = True
    supports_tools: bool = True
    supports_vision: bool = False
    supports_web_grounding: bool = False
    supports_large_context: bool = False
    # Whether Jarvis's OWN implementation streams this provider's replies
    # token-by-token (agent/executor.py's _run_claude_loop_stream) rather
    # than the underlying API's raw capability -- only Claude gets real
    # incremental streaming here today; every OpenAI-compatible provider
    # goes through the shared non-streaming loop and yields once.
    supports_streaming: bool = False


# Best-effort snapshot of what each provider/model actually does in THIS
# project's own usage today, not a claim about the raw API's full
# capability -- same "estimate, not authoritative" honesty agent/usage.py's
# own _PRICING table docstring already uses. Adjust here if a configured
# model's real capability differs (e.g. a different xai_quality_model that
# lacks vision).
CAPABILITIES: Dict[str, ProviderCapabilities] = {
    "anthropic": ProviderCapabilities(supports_vision=True, supports_large_context=True, supports_streaming=True),
    "openai": ProviderCapabilities(supports_vision=True, supports_large_context=True),
    "xai": ProviderCapabilities(supports_large_context=True),
    "perplexity": ProviderCapabilities(supports_web_grounding=True),
}

# Preference order per task type (Phase 9 Milestone 2 Step 4's role
# assignments) -- this is PREFERENCE only, never eligibility. A provider
# absent from a task's list is still reachable as a last-resort fallback
# via _DEFAULT_PROVIDER_ORDER below; capability/health/budget filtering
# in build_fallback_chain() is what actually decides eligibility.
_TASK_PROVIDER_PREFERENCE: Dict[TaskType, List[str]] = {
    TaskType.CODING: ["anthropic", "xai", "openai"],
    TaskType.DEBUGGING: ["anthropic", "xai", "openai"],
    TaskType.RESEARCH_CURRENT: ["perplexity", "xai", "openai"],
    TaskType.RESEARCH_GENERAL: ["anthropic", "perplexity", "openai"],
    TaskType.REASONING_COMPLEX: ["openai", "anthropic", "xai"],
    TaskType.REASONING_SIMPLE: ["anthropic", "openai", "xai"],
    TaskType.WRITING: ["anthropic", "openai", "xai"],
    TaskType.VISION: ["openai", "anthropic"],
    TaskType.VOICE: ["openai", "anthropic"],
    TaskType.TOOL_USE: ["anthropic", "openai", "xai"],
    TaskType.SUMMARIZATION: ["anthropic", "openai", "xai"],
    TaskType.CLASSIFICATION: ["anthropic", "openai"],
    TaskType.PLANNING: ["anthropic", "openai"],
    TaskType.DOCUMENT_ANALYSIS: ["anthropic", "openai"],
    TaskType.GENERAL_CHAT: ["anthropic", "openai", "xai"],
}
_DEFAULT_PROVIDER_ORDER = ["anthropic", "openai", "xai", "perplexity"]


def _provider_configured(provider: str) -> bool:
    from agent import provider_health

    checks = {
        "anthropic": provider_health.anthropic_configured,
        "openai": provider_health.openai_configured,
        "xai": provider_health.xai_configured,
        "perplexity": provider_health.perplexity_configured,
    }
    check = checks.get(provider)
    return bool(check and check())


def _capability_satisfies(capabilities: ProviderCapabilities, requirements: TaskRequirements) -> bool:
    if requirements.needs_vision and not capabilities.supports_vision:
        return False
    if requirements.needs_current_web and not capabilities.supports_web_grounding:
        return False
    if requirements.needs_large_context and not capabilities.supports_large_context:
        return False
    if requirements.needs_tools and not capabilities.supports_tools:
        return False
    return True


def _model_for(provider: str, requirements: TaskRequirements) -> str:
    if provider == "anthropic":
        # Reuses the existing planner_model (Haiku) as the cheap
        # candidate rather than introducing a new setting -- it's
        # already this project's one designated fast/cheap model.
        return settings.planner_model if requirements.cost_priority else settings.default_model
    if provider == "openai":
        # Three tiers (Phase 9 Milestone 2 currency review): economy/
        # quality only ever apply when a task actually asked for one;
        # fallback_model (the "balanced" tier) is the default middle
        # ground, same role it always played as the static fallback.
        if requirements.cost_priority:
            return settings.openai_economy_model
        if requirements.quality_priority:
            return settings.openai_quality_model
        return settings.fallback_model
    if provider == "xai":
        # Grok has no third "balanced" tier configured (see settings.py) --
        # quality_priority gets the flagship, everything else (including
        # plain default routing) gets the materially cheaper general-
        # purpose model rather than paying flagship prices by default.
        return settings.xai_quality_model if requirements.quality_priority else settings.xai_economy_model
    if provider == "perplexity":
        return settings.perplexity_model
    raise ValueError(f"unknown provider: {provider!r}")


def build_fallback_chain(requirements: TaskRequirements) -> List[ModelChoice]:
    """The task-aware router's real entry point. Filter order matches
    Phase 9 Milestone 2 Step 14 exactly: capability-incompatible
    candidates are removed first, then unconfigured/unhealthy
    (cooling down after a recent failure) ones, then over-budget ones --
    never the other way around, so routing can't discover mid-call that
    the cheapest candidate simply can't do the task.

    Privacy-sensitive requests (requirements.privacy_sensitive) are
    tagged for observability but not yet filtered on: every candidate
    provider today is a remote API, so filtering them all out would make
    a privacy-sensitive request unanswerable rather than safer. This is
    the intended hook for a future local-model candidate (Phase 9
    Milestone 2 Step 4's own "architecture support only" scope for
    local), deliberately not built out further this milestone.

    Falls back to the original static [anthropic, openai] chain if
    settings.task_aware_routing_enabled is False, or if every candidate
    gets filtered out -- a real routing decision never returns an empty
    list, and disabling the feature is a one-setting rollback, not a
    code change."""
    if not settings.task_aware_routing_enabled:
        return [primary_choice(), fallback_choice()]

    preferred = _TASK_PROVIDER_PREFERENCE.get(requirements.task_type, list(_DEFAULT_PROVIDER_ORDER))
    ordered_providers = list(preferred) + [p for p in _DEFAULT_PROVIDER_ORDER if p not in preferred]

    from agent import provider_health

    candidates = []
    for provider in ordered_providers:
        if not _capability_satisfies(CAPABILITIES[provider], requirements):
            continue
        if not _provider_configured(provider):
            continue
        if provider_health.is_in_cooldown(provider):
            continue
        if not is_provider_within_budget(provider):
            continue
        candidates.append(ModelChoice(provider=provider, model=_model_for(provider, requirements)))

    return candidates or [primary_choice(), fallback_choice()]
