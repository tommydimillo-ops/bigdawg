"""Lightweight provider health checks -- configuration/initialization
only, never a live API call. A live call costs real money/time and this
needs to be cheap enough to call freely (e.g. from a startup check or a
future diagnostics view), not something that burns a request every time
someone asks "is Claude working?".

anthropic_client/openai_client/xai_client/perplexity_client (agent/chat.py)
are constructed once at import time; if that construction had failed for
a required provider, importing agent.chat itself would have raised. So
successfully importing them here already proves initialization
succeeded for whichever ones are configured -- there's no separate "did
construction work" check to duplicate. xai_client/perplexity_client are
None (not a raised exception) when their key is absent, matching Phase 9
Milestone 2's "provider adapters should degrade gracefully when
credentials are absent" requirement -- see agent/chat.py's own comment.

Also owns a small, deliberately in-memory (not persisted -- this is
soft, best-effort deprioritization, not a hard cross-process guarantee)
failure-cooldown tracker the router (agent/model_router.py) consults to
temporarily skip a provider that just failed, instead of a live
health-check ping before every request -- exactly the "prefer cached/
recent health state or failure-derived state" instruction this was
built against.
"""
import time
from typing import TypedDict

from agent.secrets import get_secret
from config.settings import settings


class ProviderStatus(TypedDict):
    configured: bool
    initialized: bool


def anthropic_configured() -> bool:
    # Same lookup agent/chat.py actually uses to construct the client
    # (Keychain first, then .env) -- checking that path directly instead
    # of reimplementing its fallback order here.
    return bool(get_secret("ANTHROPIC_API_KEY"))


def openai_configured() -> bool:
    return bool(get_secret("OPENAI_API_KEY"))


def xai_configured() -> bool:
    return bool(get_secret("XAI_API_KEY"))


def perplexity_configured() -> bool:
    return bool(get_secret("PERPLEXITY_API_KEY"))


_CONFIGURED_CHECKS = {
    "anthropic": anthropic_configured,
    "openai": openai_configured,
    "xai": xai_configured,
    "perplexity": perplexity_configured,
}


def check_providers() -> dict:
    """Returns a small status dict for each provider. Cheap: no network
    calls, just checking whether a key is present and whether the shared
    client object exists."""

    from agent.chat import anthropic_client, openai_client, perplexity_client, xai_client

    clients = {
        "anthropic": anthropic_client,
        "openai": openai_client,
        "xai": xai_client,
        "perplexity": perplexity_client,
    }

    return {
        name: ProviderStatus(configured=is_configured(), initialized=clients[name] is not None)
        for name, is_configured in _CONFIGURED_CHECKS.items()
    }


# --- Failure-derived cooldown -------------------------------------------
#
# A provider that just failed (a live call, not a config check) is worth
# skipping for a short window rather than immediately retrying it on the
# very next request too -- but this is deliberately soft and short-lived:
# an in-memory dict, reset on process restart, not a persisted store. A
# real outage is a rare event this is meant to ride out gracefully, not
# a case that needs surviving a Jarvis restart.

_last_failure_at: dict = {}


def record_failure(provider: str) -> None:
    _last_failure_at[provider] = time.time()


def clear_failure(provider: str) -> None:
    _last_failure_at.pop(provider, None)


def is_in_cooldown(provider: str) -> bool:
    failed_at = _last_failure_at.get(provider)
    if failed_at is None:
        return False
    return (time.time() - failed_at) < settings.provider_failure_cooldown_seconds
