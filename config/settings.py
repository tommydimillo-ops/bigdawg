"""Centralized, typed configuration.

Before this existed, an env-var audit of the repo found only two real
os.getenv() call sites (agent/secrets.py, tools/manage_secrets.py --
both legitimately secret-handling, both left alone). The actual scattering
problem wasn't stray os.getenv() calls; it was hardcoded string/number
literals with no env override at all -- model names duplicated as string
literals across 6+ files (agent/executor.py, deep_reasoning.py,
research_agent.py, tools/computer_use.py, tools/vision.py, brain.py's
CLI block), a poll-interval constant duplicated in two files with the
same value, and a couple of other one-off constants. This module gives
all of that one typed, env-overridable home.

API keys, passwords, and other actual secrets are explicitly NOT here --
those continue to go through agent/secrets.py's Keychain-backed
get_secret(), which this module doesn't duplicate or replace.

Some fields below (autonomy_level, memory_enabled, voice_enabled,
confirmation_pending-style toggles) are present because they're
reasonable configuration surface to have typed and available, but aren't
wired to change any behavior yet -- said explicitly in each docstring so
it's never ambiguous whether a value is load-bearing.
"""
import os
from dataclasses import dataclass
from typing import Optional

from dotenv import load_dotenv

# Self-sufficient regardless of import order -- agent/chat.py also calls
# this, but if something imports config.settings first (plausible now
# that voice/scheduler/etc. can depend on it directly), .env overrides
# for these settings shouldn't silently not apply just because chat.py
# hadn't been imported yet. Idempotent/safe to call more than once.
load_dotenv()


def _env_str(name: str, default: str) -> str:
    raw = os.getenv(name)
    return raw if raw else default


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or raw == "":
        return default
    try:
        return int(raw)
    except ValueError:
        raise ValueError(f"{name}={raw!r} is not a valid integer") from None


def _env_float(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None or raw == "":
        return default
    try:
        return float(raw)
    except ValueError:
        raise ValueError(f"{name}={raw!r} is not a valid float") from None


def _env_optional_str(name: str) -> Optional[str]:
    raw = os.getenv(name)
    return raw if raw else None


_TRUE_VALUES = {"1", "true", "yes", "on"}
_FALSE_VALUES = {"0", "false", "no", "off"}


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None or raw == "":
        return default
    lowered = raw.strip().lower()
    if lowered in _TRUE_VALUES:
        return True
    if lowered in _FALSE_VALUES:
        return False
    raise ValueError(
        f"{name}={raw!r} is not a valid boolean (use true/false, 1/0, yes/no, or on/off)"
    )


@dataclass(frozen=True)
class Settings:
    # --- AI provider / model selection ---
    # default_model is the primary (Claude) model; fallback_model is only
    # ever used if a live call to the primary provider fails mid-request.
    default_model: str = "claude-sonnet-5"
    # fallback_model is the OpenAI "balanced" tier -- the original static
    # fallback_choice()/select() interface's OpenAI model, and the
    # task-aware router's default OpenAI candidate when a task is neither
    # cost- nor quality-priority. Currency verified 2026-08-15 against
    # OpenAI's official model/pricing pages: the GPT-5 family was
    # superseded by GPT-5.6 (Sol/Terra/Luna) -- gpt-5 itself is stale.
    fallback_model: str = "gpt-5.6-terra"
    # Hardcoded per-call-site assignment, not part of task-aware routing
    # (see agent/model_router.py's candidates) -- was left on the stale
    # gpt-5 default when the router tiers were currency-reviewed
    # 2026-08-15; caught and fixed separately since gpt-5 is the same
    # superseded family as fallback_model above. gpt-5.6-terra (the
    # balanced tier) supports image input per OpenAI's official docs and
    # is a stronger general default for screenshot/document/UI analysis
    # than defaulting to the cheapest tier.
    vision_model: str = "gpt-5.6-terra"
    vision_fallback_model: str = "claude-haiku-4-5-20251001"
    transcription_model: str = "gpt-4o-transcribe"
    tts_model: str = "gpt-4o-mini-tts"
    # Fast, low-cost decomposition only; the main executor still uses the
    # default model for actual reasoning and tool decisions.
    planner_model: str = "claude-haiku-4-5-20251001"

    # --- Phase 9 Milestone 2: task-aware routing, optional providers ---
    # OpenAI's cost-priority and quality-priority tiers (agent/model_router.
    # py's _model_for()) -- fallback_model above remains the "balanced"
    # middle tier. All three currency-verified 2026-08-15 against OpenAI's
    # official pricing page; still Chat-Completions-compatible, no
    # Responses API migration required for these model IDs.
    openai_economy_model: str = "gpt-5.6-luna"
    openai_quality_model: str = "gpt-5.6-sol"
    # xai_economy_model/xai_quality_model are only ever used if XAI_API_KEY
    # is actually configured (agent/chat.py's xai_client). Currency
    # verified 2026-08-15 against docs.x.ai/docs/models: grok-4.3 is
    # general-purpose and materially cheaper than the grok-4.6 flagship,
    # so routing shouldn't pay flagship prices for every xAI call -- only
    # requirements.quality_priority tasks get grok-4.6.
    xai_economy_model: str = "grok-4.3"
    xai_quality_model: str = "grok-4.6"
    # perplexity_model is only ever used if PERPLEXITY_API_KEY is actually
    # configured (agent/chat.py's perplexity_client). UNLIKE every other
    # *_model field above, this is a Perplexity Agent API *preset* string
    # (sent as the request's "preset" field), not a flat per-token-priced
    # model ID -- Perplexity deprecated Sonar Chat Completions in 2026-07
    # (support ends 2026-09-27) in favor of the Agent API, whose
    # request/response shape genuinely differs from OpenAI-style chat.
    # completions (input/output, not messages/choices -- see agent/
    # executor.py's _call_perplexity_agent). "low" is Perplexity's own
    # documented replacement for the old sonar-pro model (multi-step
    # reasoning + web search), confirmed against docs.perplexity.ai's
    # migration guide 2026-08-15.
    perplexity_model: str = "low"
    # IS wired (agent/model_router.py): the whole task-aware, multi-
    # provider candidate-ranking path can be switched off in one place,
    # falling straight back to the original static Claude-then-OpenAI
    # behavior -- a deliberate, cheap rollback lever for a change this
    # central, not a "maybe someday" toggle.
    task_aware_routing_enabled: bool = True
    # IS wired (agent/provider_health.py): how long a provider that just
    # failed a live call is deprioritized before the router will
    # consider it again -- soft, in-memory, reset on process restart.
    provider_failure_cooldown_seconds: float = 120.0

    # --- Phase 9 Milestone 2: budget controls ---
    # Minimal foundation only (see ROADMAP.md/CHANGELOG.md for what's
    # deferred): a same-day spend ceiling checked against
    # agent/usage.py's existing cost_today()/cost_since() aggregation --
    # no new persistent accounting store. None disables the
    # corresponding check, the same convention Phase 8's
    # max_cost_per_request_usd already uses.
    daily_budget_usd: Optional[float] = None
    # A single ceiling applied uniformly per provider (not four separate
    # dollar settings) -- simpler, and still satisfies "a provider budget
    # exists" without a scattered per-provider config surface.
    provider_daily_budget_usd: Optional[float] = None
    # Fraction of the active budget at which routing starts preferring
    # cheaper candidates (a soft nudge), before the hard stop at 1.0
    # actually removes a provider from consideration.
    budget_warning_threshold: float = 0.8

    # --- Agent loop ---
    max_agent_steps: int = 8
    # Only this many recent chat messages are sent to a provider. The full
    # conversation remains on disk/UI, but unbounded old turns no longer
    # inflate every request's latency and token count.
    model_history_limit: int = 24

    # --- Autonomy / permissions ---
    # IS wired (agent/autonomy.py): controls which tools run instantly vs.
    # require an extra "yes, go ahead" confirmation before they run --
    # never which tools are allowed at all (permission levels) or which
    # ones have a hard, unconditional confirmation gate (confirm_login,
    # send_email, computer_confirm_action, and unattended-execution
    # restrictions) -- those are completely unaffected by this value at
    # every level. Defaults to 4 (the highest defined level) deliberately:
    # at level 4, only permission_level 5 (computer_confirm_action, which
    # was already hard-gated separately anyway) ever asks for extra
    # confirmation, so nothing that currently runs instantly changes
    # behavior with no explicit opt-in. Lower this (0-3) for progressively
    # more confirmation on lower-risk tools too -- see agent/autonomy.py's
    # module docstring for the exact level-by-level breakdown.
    autonomy_level: int = 4

    # --- Memory ---
    # NOT YET WIRED -- nothing currently checks this before reading/
    # writing memory. Reserved for a future on/off toggle.
    memory_enabled: bool = True
    # IS wired (agent/context.py): max number of relevance-ranked PATTERN
    # memories injected into a given prompt, instead of dumping all of
    # them into every single request.
    context_memory_budget: int = 6

    # --- Obsidian vault (optional human-readable knowledge layer) ---
    # IS wired (agent/obsidian_vault.py). None means "not explicitly
    # configured" -- that module still falls back to a project-relative
    # ./JarvisVault convenience default before treating the integration
    # as unavailable, so this deliberately isn't a hardcoded personal
    # absolute path here. Distinct from agent/memory/ (Jarvis's own
    # structured store, untouched by this) -- this is a separate,
    # optional, human-readable layer the user can browse/edit directly.
    obsidian_vault_path: Optional[str] = None

    # --- Execution history ---
    # IS wired (agent/execution_history.py): bounded retention -- oldest
    # entries are dropped once this many completed executions are stored.
    # Distinct from memory_enabled/context_memory_budget above: this is
    # about past REQUESTS' metadata, not personal facts/preferences.
    execution_history_limit: int = 20

    # --- Voice ---
    # IS wired (Phase 6, ui/menu_bar.py): if False, the menu-bar app never
    # starts its microphone listener thread at all.
    voice_enabled: bool = True
    wake_word: str = "jarvis"
    voice_sample_rate: int = 16000
    # Native microphone VAD guardrails. A single click/bump must not be
    # treated as speech and sent to transcription.
    voice_min_signal_level: float = 100.0
    voice_trigger_chunks: int = 2
    user_name: str = "Tommy"
    # IS wired (voice/speak.py): if False, speak_natural() is a no-op --
    # useful for a silent/text-only run without touching every call site.
    tts_enabled: bool = True
    # IS wired (voice/listen.py): max seconds a single recorded utterance
    # is allowed to run before it's cut off, win or lose.
    voice_listen_timeout: float = 15.0
    # IS wired (agent/voice_session.py): how long to wait for a yes/no
    # after Jarvis asks for confirmation before giving up and treating it
    # as "no response."
    voice_confirmation_timeout: float = 20.0
    # IS wired (ui/menu_bar.py): if False, the wake word is heard but
    # ignored while Jarvis is already speaking or executing, instead of
    # interrupting it.
    voice_interruption_enabled: bool = True
    # IS wired (voice/listen.py): SFSpeechRecognitionTask.cancel() does
    # not actually stop macOS's on-device recognition work (confirmed
    # live via CPU profiling -- a real profile showed a dozen-plus
    # abandoned dispatch queues still consuming hundreds of percent CPU
    # minutes after the in-process calls that spawned them returned).
    # voice/local_transcribe.py now runs the recognition in a separate
    # OS process specifically so a timeout can kill it outright instead
    # of relying on that broken cancel() API -- with that fixed, this
    # flag is back on by default. Kept flippable in case a future
    # regression in that isolation needs a fast way to fall back to
    # silence rather than a resource leak.
    local_transcription_fallback_enabled: bool = True

    # --- Scheduler ---
    scheduler_poll_seconds: int = 30

    # --- Agent Manager (Phase 7) ---
    # IS wired (agent/agents/manager.py): bounds how long a single
    # coworker-agent invocation (ResearchAgent/MemoryAgent's direct
    # execution path) is allowed to run before the manager gives up and
    # reports a timeout, rather than a runaway background agent silently
    # never returning. CodingAgent/QAAgent don't run their own loop this
    # phase (they defer to the ordinary executor, which already has its
    # own bounds via max_agent_steps), so this specifically covers the
    # two agents the manager calls directly.
    agent_timeout_seconds: float = 60.0
    # Phase 9 Milestone 3: bounds concurrent coworker-agent subprocesses
    # launched from ONE delegate_parallel_tasks batch (agent/agents/
    # manager.py's execute_agents_parallel). Deliberately small and fixed
    # rather than scaling with request size -- each unit is a real OS
    # subprocess plus a real, potentially paid model call, so this is a
    # cost/resource ceiling, not just a performance knob. Independent of
    # tools.registry's parallel_safe mechanism (execution_control.py
    # etc.), which bounds concurrent READ-ONLY tool calls within a single
    # response and was never meant to gate paid, side-effecting work.
    max_parallel_agents: int = 3
    # How many times a single failed (non-cancelled) subtask within a
    # batch is retried before being reported as a permanent failure --
    # small and fixed, mirroring agent/retry_policy.py's MAX_RETRIES
    # philosophy at the agent level instead of the tool-call level.
    # Deliberately does not distinguish retryable/non-retryable failure
    # types the way retry_policy.py does for tool errors -- an agent
    # subprocess failure is coarser-grained (exit code, timeout, bad
    # JSON) than a typed Python exception, so there's no equivalent
    # signal to branch on yet; retrying once covers the common transient
    # case (a flaky network call inside the subprocess) without risking a
    # repair storm.
    max_agent_batch_retries: int = 1

    # --- Usage tracking & limits (Phase 8) ---
    # IS wired (agent/usage.py): how many past API-call usage records to
    # keep -- same bounded-JSON-file pattern as execution_history_limit
    # above, sized larger since a single request can span several calls
    # (research alone can be 7+ per invocation -- see Phase 7's usage
    # investigation), not one record per request the way execution
    # history is.
    usage_history_limit: int = 5000
    # IS wired (agent/usage.py's check_request_limits, called from
    # agent/executor.py's two loops and agent/research_agent.py's loop):
    # safety ceilings a single request's total usage can't exceed before
    # later calls within that SAME request are refused -- a circuit
    # breaker, not a permission check. Already-made calls can't be
    # undone, but nothing further compounds. None disables the
    # corresponding check.
    max_requests_per_execution: Optional[int] = 25
    max_agent_iterations: int = 6  # ResearchAgent's own internal loop cap
    max_tokens_per_request: Optional[int] = 200_000
    max_cost_per_request_usd: Optional[float] = 1.00

    # --- API behavior: timeouts & retries (both Claude and OpenAI clients) ---
    api_connect_timeout: float = 5.0
    api_read_timeout: float = 25.0
    api_write_timeout: float = 10.0
    api_pool_timeout: float = 5.0
    api_max_retries: int = 3

    # --- OpenClaw bridge (optional, read-only, Phase 9 OpenClaw M1) ---
    # IS wired (agent/openclaw_gateway.py). OpenClaw is a separate,
    # optional, subordinate local service Jarvis can query -- never a
    # second orchestrator, never a source of model-routing/permission
    # authority (see ARCHITECTURE.md's OpenClaw section). Disabled by
    # default: a fresh install with no OpenClaw Gateway running must
    # start and run identically to one that never heard of OpenClaw.
    openclaw_enabled: bool = False
    # Loopback only, matching OpenClaw's own documented default bind
    # mode -- Jarvis is a CLIENT of a Gateway already running on this
    # same machine, never a remote caller. Plain ws://, not wss://: the
    # documented local default is an authenticated loopback WebSocket,
    # not TLS (loopback traffic never leaves the machine, so there's
    # nothing on the wire for TLS to protect against here).
    openclaw_gateway_url: str = "ws://127.0.0.1:18789"
    # Bounds the ENTIRE one-shot call (connect + handshake + one RPC +
    # close) -- deliberately tighter than the Gateway protocol's own
    # documented 30s per-RPC default, since every method M1 actually
    # calls (health/status/node.list) is meant to be a fast, read-only
    # query, not a long-running operation.
    openclaw_timeout_seconds: float = 10.0

    # --- Debug / development mode ---
    # Wired to agent/observability.py's log level (DEBUG vs INFO).
    debug: bool = False

    @classmethod
    def load(cls) -> "Settings":
        """Builds Settings from environment variables, falling back to
        the defaults above for anything unset. Raises ValueError with a
        clear message for a set-but-invalid value (e.g. MAX_AGENT_STEPS
        that isn't a real integer) rather than silently ignoring it --
        a misconfigured value should fail loudly at startup, not produce
        confusing behavior later."""

        return cls(
            default_model=_env_str("DEFAULT_MODEL", cls.default_model),
            fallback_model=_env_str("FALLBACK_MODEL", cls.fallback_model),
            vision_model=_env_str("VISION_MODEL", cls.vision_model),
            vision_fallback_model=_env_str("VISION_FALLBACK_MODEL", cls.vision_fallback_model),
            transcription_model=_env_str("TRANSCRIPTION_MODEL", cls.transcription_model),
            tts_model=_env_str("TTS_MODEL", cls.tts_model),
            planner_model=_env_str("PLANNER_MODEL", cls.planner_model),
            openai_economy_model=_env_str("OPENAI_ECONOMY_MODEL", cls.openai_economy_model),
            openai_quality_model=_env_str("OPENAI_QUALITY_MODEL", cls.openai_quality_model),
            xai_economy_model=_env_str("XAI_ECONOMY_MODEL", cls.xai_economy_model),
            xai_quality_model=_env_str("XAI_QUALITY_MODEL", cls.xai_quality_model),
            perplexity_model=_env_str("PERPLEXITY_MODEL", cls.perplexity_model),
            task_aware_routing_enabled=_env_bool("TASK_AWARE_ROUTING_ENABLED", cls.task_aware_routing_enabled),
            provider_failure_cooldown_seconds=_env_float(
                "PROVIDER_FAILURE_COOLDOWN_SECONDS", cls.provider_failure_cooldown_seconds,
            ),
            daily_budget_usd=_env_float("DAILY_BUDGET_USD", cls.daily_budget_usd),
            provider_daily_budget_usd=_env_float("PROVIDER_DAILY_BUDGET_USD", cls.provider_daily_budget_usd),
            budget_warning_threshold=_env_float("BUDGET_WARNING_THRESHOLD", cls.budget_warning_threshold),
            max_agent_steps=_env_int("MAX_AGENT_STEPS", cls.max_agent_steps),
            model_history_limit=_env_int("MODEL_HISTORY_LIMIT", cls.model_history_limit),
            autonomy_level=_env_int("AUTONOMY_LEVEL", cls.autonomy_level),
            memory_enabled=_env_bool("MEMORY_ENABLED", cls.memory_enabled),
            context_memory_budget=_env_int("CONTEXT_MEMORY_BUDGET", cls.context_memory_budget),
            obsidian_vault_path=_env_optional_str("OBSIDIAN_VAULT_PATH"),
            execution_history_limit=_env_int("EXECUTION_HISTORY_LIMIT", cls.execution_history_limit),
            voice_enabled=_env_bool("VOICE_ENABLED", cls.voice_enabled),
            wake_word=_env_str("WAKE_WORD", cls.wake_word),
            voice_sample_rate=_env_int("VOICE_SAMPLE_RATE", cls.voice_sample_rate),
            voice_min_signal_level=_env_float("VOICE_MIN_SIGNAL_LEVEL", cls.voice_min_signal_level),
            voice_trigger_chunks=_env_int("VOICE_TRIGGER_CHUNKS", cls.voice_trigger_chunks),
            user_name=_env_str("JARVIS_USER_NAME", cls.user_name),
            tts_enabled=_env_bool("TTS_ENABLED", cls.tts_enabled),
            voice_listen_timeout=_env_float("LISTEN_TIMEOUT", cls.voice_listen_timeout),
            voice_confirmation_timeout=_env_float("CONFIRMATION_TIMEOUT", cls.voice_confirmation_timeout),
            voice_interruption_enabled=_env_bool("VOICE_INTERRUPTION_ENABLED", cls.voice_interruption_enabled),
            local_transcription_fallback_enabled=_env_bool(
                "LOCAL_TRANSCRIPTION_FALLBACK_ENABLED", cls.local_transcription_fallback_enabled,
            ),
            scheduler_poll_seconds=_env_int("SCHEDULER_POLL_SECONDS", cls.scheduler_poll_seconds),
            agent_timeout_seconds=_env_float("AGENT_TIMEOUT_SECONDS", cls.agent_timeout_seconds),
            max_parallel_agents=_env_int("MAX_PARALLEL_AGENTS", cls.max_parallel_agents),
            max_agent_batch_retries=_env_int("MAX_AGENT_BATCH_RETRIES", cls.max_agent_batch_retries),
            usage_history_limit=_env_int("USAGE_HISTORY_LIMIT", cls.usage_history_limit),
            max_requests_per_execution=_env_int("MAX_REQUESTS_PER_EXECUTION", cls.max_requests_per_execution),
            max_agent_iterations=_env_int("MAX_AGENT_ITERATIONS", cls.max_agent_iterations),
            max_tokens_per_request=_env_int("MAX_TOKENS_PER_REQUEST", cls.max_tokens_per_request),
            max_cost_per_request_usd=_env_float("MAX_COST_PER_REQUEST_USD", cls.max_cost_per_request_usd),
            api_connect_timeout=_env_float("API_CONNECT_TIMEOUT", cls.api_connect_timeout),
            api_read_timeout=_env_float("API_READ_TIMEOUT", cls.api_read_timeout),
            api_write_timeout=_env_float("API_WRITE_TIMEOUT", cls.api_write_timeout),
            api_pool_timeout=_env_float("API_POOL_TIMEOUT", cls.api_pool_timeout),
            api_max_retries=_env_int("API_MAX_RETRIES", cls.api_max_retries),
            openclaw_enabled=_env_bool("OPENCLAW_ENABLED", cls.openclaw_enabled),
            openclaw_gateway_url=_env_str("OPENCLAW_GATEWAY_URL", cls.openclaw_gateway_url),
            openclaw_timeout_seconds=_env_float("OPENCLAW_TIMEOUT_SECONDS", cls.openclaw_timeout_seconds),
            debug=_env_bool("DEBUG", cls.debug),
        )


# Built once at import time, same as every other module-level client/config
# in this project (agent/chat.py's clients, agent/brain.py's TOOLS).
settings = Settings.load()
