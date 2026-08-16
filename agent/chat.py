from dotenv import load_dotenv
load_dotenv()

import httpx
from anthropic import Anthropic
from openai import OpenAI

from agent.secrets import get_secret
from config.settings import settings

# Home routers commonly tear down idle "keep-alive" connections behind the
# client's back (much more aggressively than office/campus networks), so a
# pooled connection that looks fine to httpx gets handed a request and comes
# back "Connection reset by peer". Some home ISPs/routers also have a
# half-broken IPv6 path that resets instead of failing cleanly. A short
# keepalive_expiry recycles connections before a router would kill them,
# while still reusing one across the several rapid-fire calls a single tool
# loop makes (fully disabling reuse, as an earlier version of this did,
# meant paying a fresh TLS handshake on every single step and made the
# whole assistant feel sluggish). Forcing IPv4 avoids the flaky IPv6 path.
# Both SDKs default to a 10-minute response timeout, meant for long
# completions — for a chat assistant a stalled connection should fail fast
# and hand off to retries/fallback instead of hanging for minutes.
_TIMEOUT = httpx.Timeout(
    connect=settings.api_connect_timeout,
    read=settings.api_read_timeout,
    write=settings.api_write_timeout,
    pool=settings.api_pool_timeout,
)


def _resilient_http_client():
    return httpx.Client(
        limits=httpx.Limits(max_keepalive_connections=10, keepalive_expiry=15.0),
        transport=httpx.HTTPTransport(local_address="0.0.0.0", retries=2),
        timeout=_TIMEOUT,
    )


anthropic_client = Anthropic(
    api_key=get_secret("ANTHROPIC_API_KEY"),
    http_client=_resilient_http_client(),
    timeout=_TIMEOUT,
    max_retries=settings.api_max_retries,
)
openai_client = OpenAI(
    api_key=get_secret("OPENAI_API_KEY"),
    http_client=_resilient_http_client(),
    timeout=_TIMEOUT,
    max_retries=settings.api_max_retries,
)

# xAI is OpenAI-API-compatible (same request/response shape, just a
# different base_url) -- confirmed against its current published docs
# rather than assumed, since guessing wrong here would mean silently
# mis-routed requests, not just a wrong string constant. No new SDK
# dependency needed: the same `openai` package already required for
# openai_client above works unchanged, just pointed elsewhere.
#
# Perplexity is NOT OpenAI-compatible as of this project's Phase 9
# Milestone 2 currency review (2026-08-15) -- its Sonar Chat Completions
# surface (which WAS OpenAI-compatible, and is what this client used to
# call) is deprecated as of 2026-07, with support ending 2026-09-27, in
# favor of the Agent API. The Agent API's request/response shape genuinely
# differs (an `input`/`output` shape, not `messages`/`choices`), so
# agent/executor.py's _call_perplexity_agent makes its own direct HTTP
# call instead of going through this client's .chat.completions.create --
# perplexity_client below is kept only as this provider's configured/
# initialized indicator (agent/provider_health.py's check_providers()),
# matching xai_client/anthropic_client/openai_client's same role there.
#
# Unlike anthropic_client/openai_client above (required providers -- this
# project has always assumed at least these two are configured), xAI and
# Perplexity are optional (Phase 9 Milestone 2). The OpenAI SDK raises
# immediately if constructed with api_key=None (confirmed empirically,
# see CHANGELOG.md's CI-setup entry for the same discovery on
# openai_client itself), so these are only constructed when a key is
# actually present -- None otherwise, never a raised exception, so
# importing this module never depends on either being configured.
_xai_key = get_secret("XAI_API_KEY")
xai_client = OpenAI(
    api_key=_xai_key,
    base_url="https://api.x.ai/v1",
    http_client=_resilient_http_client(),
    timeout=_TIMEOUT,
    max_retries=settings.api_max_retries,
) if _xai_key else None

_perplexity_key = get_secret("PERPLEXITY_API_KEY")
perplexity_client = OpenAI(
    api_key=_perplexity_key,
    base_url="https://api.perplexity.ai/v1",
    http_client=_resilient_http_client(),
    timeout=_TIMEOUT,
    max_retries=settings.api_max_retries,
) if _perplexity_key else None
