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
