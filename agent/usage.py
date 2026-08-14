"""Phase 8 part 1 -- real token/cost accounting, so "what exactly is
costing me money" has an actual answer instead of a guess. Every LLM/
audio API call this project makes (see the module docstring list below)
records a UsageRecord here: request_id, agent, provider, model,
operation, token/audio/character counts, an estimated dollar cost, and
duration. Persisted the same way agent/execution_history.py already
persists request history -- a bounded, atomically-written JSON file, not
a database.

Call sites that record usage (one per real API call, not per request --
a single request can span several):
  agent/executor.py       -- the main chat loop (Claude primary, OpenAI
                              fallback)
  agent/research_agent.py -- ResearchAgent's internal loop
  agent/planner.py        -- plan creation for complex requests
  agent/deep_reasoning.py -- the deep_reason tool
  tools/computer_use.py   -- computer_locate (screenshot -> coordinates)
  tools/vision.py         -- computer_see (screenshot -> description)
  voice/listen.py         -- transcription
  voice/speak.py          -- TTS

ESTIMATED COST ONLY. The prices in _PRICING below are this project's
best-effort snapshot of each provider's published per-token/per-minute/
per-character rates -- not pulled from a live pricing API (neither
provider exposes one), and not reconciled against an actual invoice.
Treat the dollar figures here as "which requests were expensive
relative to each other," not as authoritative billing. Update _PRICING
directly if a provider's rates change; it's a plain dict, not derived
logic.
"""
import json
import os
import tempfile
import time
import fcntl
from dataclasses import asdict, dataclass, field
from typing import List, Optional

from agent.observability import log_event
from config.settings import settings

USAGE_FILE = os.path.expanduser("~/Library/Application Support/CampusPilot/usage_history.json")

# provider -> model -> {"input": $/1M input tokens, "output": $/1M output tokens}
# for token-priced models, or {"per_minute": $/minute} for transcription, or
# {"per_1k_chars": $/1K characters} for TTS. Unrecognized models fall back
# to _DEFAULT_TOKEN_PRICE so a cost still gets *estimated* (clearly an
# estimate, never silently zero) rather than a KeyError.
_PRICING = {
    "anthropic": {
        "claude-sonnet-5": {"input": 3.00, "output": 15.00},
        "claude-haiku-4-5-20251001": {"input": 0.80, "output": 4.00},
        "claude-opus-5": {"input": 15.00, "output": 75.00},
    },
    "openai": {
        "gpt-5": {"input": 2.50, "output": 10.00},
        "gpt-4o-transcribe": {"per_minute": 0.006},
        "gpt-4o-mini-tts": {"per_1k_chars": 0.015},
    },
}
_DEFAULT_TOKEN_PRICE = {"input": 3.00, "output": 15.00}


@dataclass
class UsageRecord:
    request_id: Optional[str]
    agent: Optional[str]
    provider: str
    model: str
    operation: str
    input_tokens: int = 0
    output_tokens: int = 0
    audio_seconds: Optional[float] = None
    character_count: Optional[int] = None
    estimated_cost_usd: float = 0.0
    duration_seconds: float = 0.0
    timestamp: float = field(default_factory=time.time)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "UsageRecord":
        known_fields = {f for f in cls.__dataclass_fields__}
        return cls(**{k: v for k, v in data.items() if k in known_fields})


def _as_number(value, kind):
    """Coerces `value` to `kind` (int or float), degrading to 0 for
    anything that isn't genuinely numeric -- see record_llm_usage's
    docstring for why this matters more than an ordinary defensive
    cast: a mocked API response's `.usage.input_tokens` in a test is a
    MagicMock, not a missing/None value, so isinstance is what actually
    catches it, not a try/except around the eventual arithmetic."""
    if isinstance(value, bool):
        return kind(value)
    if isinstance(value, (int, float)):
        return kind(value)
    return kind(0)


def _estimate_cost(
    provider: str, model: str, input_tokens: int, output_tokens: int,
    audio_seconds: Optional[float], character_count: Optional[int],
) -> float:
    rates = _PRICING.get(provider, {}).get(model)

    if rates and "per_minute" in rates and audio_seconds is not None:
        return (audio_seconds / 60.0) * rates["per_minute"]
    if rates and "per_1k_chars" in rates and character_count is not None:
        return (character_count / 1000.0) * rates["per_1k_chars"]

    token_rates = rates if (rates and "input" in rates) else _DEFAULT_TOKEN_PRICE
    return (input_tokens / 1_000_000.0) * token_rates["input"] + (output_tokens / 1_000_000.0) * token_rates["output"]


def record_llm_usage(
    provider: str, model: str, operation: str,
    request_id: Optional[str] = None, agent: Optional[str] = None,
    input_tokens: int = 0, output_tokens: int = 0,
    audio_seconds: Optional[float] = None, character_count: Optional[int] = None,
    duration_seconds: float = 0.0,
) -> UsageRecord:
    """The one entry point every call site above uses. Never raises --
    a bug in cost estimation must not be able to break the real request
    that triggered it, the same reasoning agent.execution_state.
    transition_to's own docstring gives for never raising.

    Every numeric input is coerced through _as_number first, not just
    wrapped in a try/except -- a MagicMock (every existing test's mocked
    API response) satisfies attribute access AND arithmetic magic
    methods without ever raising, so a naive try/except around cost
    math would silently let a MagicMock flow all the way into a
    UsageRecord and then fail much later (and much less legibly) when
    something tries to JSON-serialize it for persistence or logging."""
    input_tokens = _as_number(input_tokens, int)
    output_tokens = _as_number(output_tokens, int)
    audio_seconds = _as_number(audio_seconds, float) if audio_seconds is not None else None
    character_count = _as_number(character_count, int) if character_count is not None else None
    duration_seconds = _as_number(duration_seconds, float)

    try:
        cost = _estimate_cost(provider, model, input_tokens, output_tokens, audio_seconds, character_count)
    except Exception:
        cost = 0.0

    record = UsageRecord(
        request_id=request_id, agent=agent, provider=provider, model=model, operation=operation,
        input_tokens=input_tokens, output_tokens=output_tokens,
        audio_seconds=audio_seconds, character_count=character_count,
        estimated_cost_usd=round(cost, 6), duration_seconds=round(duration_seconds, 3),
    )

    try:
        _persist(record)
    except Exception as error:
        log_event(
            "usage_persist_failed", component="usage", level="warning",
            error_type=type(error).__name__,
        )

    log_event(
        "usage_recorded", request_id=request_id, component="usage",
        agent=agent, provider=provider, model=model, operation=operation,
        input_tokens=input_tokens, output_tokens=output_tokens,
        estimated_cost_usd=record.estimated_cost_usd,
    )
    return record


def _load_raw() -> List[dict]:
    if not os.path.exists(USAGE_FILE):
        return []
    with open(USAGE_FILE, "r") as f:
        return json.load(f)


def _save_raw(records: List[dict]) -> None:
    directory = os.path.dirname(USAGE_FILE)
    os.makedirs(directory, exist_ok=True)
    fd, tmp_file = tempfile.mkstemp(prefix="usage-history-", suffix=".tmp", dir=directory)
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(records, f, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_file, USAGE_FILE)
    finally:
        if os.path.exists(tmp_file):
            os.remove(tmp_file)


def _persist(record: UsageRecord) -> None:
    lock_file = f"{USAGE_FILE}.lock"
    os.makedirs(os.path.dirname(USAGE_FILE), exist_ok=True)
    with open(lock_file, "a+") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        records = _load_raw()
        records.append(record.to_dict())

        limit = max(0, settings.usage_history_limit)
        if len(records) > limit:
            records = records[-limit:]

        _save_raw(records)


def get_recent(limit: Optional[int] = None) -> List[UsageRecord]:
    records = [UsageRecord.from_dict(r) for r in _load_raw()]
    records.sort(key=lambda r: r.timestamp, reverse=True)
    if limit is not None:
        records = records[:limit]
    return records


def get_since(cutoff_timestamp: float) -> List[UsageRecord]:
    return [r for r in get_recent() if r.timestamp >= cutoff_timestamp]


def total_cost_for_request(request_id: str) -> float:
    """Used by Phase 8 part 3's per-request cost limit -- how much this
    SPECIFIC request has spent so far across every call site that's
    recorded usage for it."""
    return sum(r.estimated_cost_usd for r in get_recent() if r.request_id == request_id)


def total_tokens_for_request(request_id: str) -> int:
    """Used by Phase 8 part 3's per-request token limit."""
    matching = [r for r in get_recent() if r.request_id == request_id]
    return sum(r.input_tokens + r.output_tokens for r in matching)


def request_count_for_request(request_id: str) -> int:
    """Used by Phase 8 part 3's max_requests_per_execution limit."""
    return sum(1 for r in get_recent() if r.request_id == request_id)


@dataclass
class LimitCheckResult:
    exceeded: bool
    reason: Optional[str] = None


def check_request_limits(request_id: Optional[str]) -> LimitCheckResult:
    """Phase 8 part 3 -- configurable safety limits (max_requests_per_
    execution, max_tokens_per_request, max_cost_per_request_usd), each
    disabled by setting it to None. Meant to be checked once per iteration
    of a model loop (agent/executor.py's two provider loops,
    agent/research_agent.py's loop), at the same point _is_cancelled(state)
    already is checked -- so like that check, this is a between-iterations
    safety net based on usage already recorded, not a preemptive hard cap
    on the call that's about to happen. Deliberately separate from
    agent.autonomy's permission system: this stops a request from running
    away on volume/cost, it doesn't decide whether any individual tool is
    allowed to run."""
    if not request_id:
        return LimitCheckResult(False)

    max_requests = settings.max_requests_per_execution
    if max_requests is not None:
        count = request_count_for_request(request_id)
        if count >= max_requests:
            return LimitCheckResult(
                True,
                f"this request has made {count} provider calls, reaching its "
                f"max_requests_per_execution limit of {max_requests}",
            )

    max_tokens = settings.max_tokens_per_request
    if max_tokens is not None:
        tokens = total_tokens_for_request(request_id)
        if tokens >= max_tokens:
            return LimitCheckResult(
                True,
                f"this request has used {tokens} tokens, reaching its "
                f"max_tokens_per_request limit of {max_tokens}",
            )

    max_cost = settings.max_cost_per_request_usd
    if max_cost is not None:
        cost = total_cost_for_request(request_id)
        if cost >= max_cost:
            return LimitCheckResult(
                True,
                f"this request has an estimated cost of ${cost:.2f}, reaching its "
                f"max_cost_per_request_usd limit of ${max_cost:.2f}",
            )

    return LimitCheckResult(False)
