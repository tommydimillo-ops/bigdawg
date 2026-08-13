"""Lightweight provider health checks -- configuration/initialization
only, never a live API call. A live call costs real money/time and this
needs to be cheap enough to call freely (e.g. from a startup check or a
future diagnostics view), not something that burns a request every time
someone asks "is Claude working?".

anthropic_client/openai_client (agent/chat.py) are constructed once at
import time; if that construction had failed, importing agent.chat itself
would have raised. So successfully importing them here already proves
initialization succeeded -- there's no separate "did construction work"
check to duplicate.
"""
from typing import TypedDict

from agent.secrets import get_secret


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


def check_providers() -> dict:
    """Returns a small status dict for each provider. Cheap: no network
    calls, just checking whether a key is present and whether the shared
    client objects exist."""

    from agent.chat import anthropic_client, openai_client

    return {
        "anthropic": ProviderStatus(
            configured=anthropic_configured(),
            initialized=anthropic_client is not None,
        ),
        "openai": ProviderStatus(
            configured=openai_configured(),
            initialized=openai_client is not None,
        ),
    }
