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
    fallback_model: str = "gpt-5"
    vision_model: str = "gpt-5"
    vision_fallback_model: str = "claude-haiku-4-5-20251001"
    transcription_model: str = "gpt-4o-transcribe"
    tts_model: str = "gpt-4o-mini-tts"
    # Fast, low-cost decomposition only; the main executor still uses the
    # default model for actual reasoning and tool decisions.
    planner_model: str = "claude-haiku-4-5-20251001"

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

    # --- API behavior: timeouts & retries (both Claude and OpenAI clients) ---
    api_connect_timeout: float = 5.0
    api_read_timeout: float = 25.0
    api_write_timeout: float = 10.0
    api_pool_timeout: float = 5.0
    api_max_retries: int = 3

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
            max_agent_steps=_env_int("MAX_AGENT_STEPS", cls.max_agent_steps),
            model_history_limit=_env_int("MODEL_HISTORY_LIMIT", cls.model_history_limit),
            autonomy_level=_env_int("AUTONOMY_LEVEL", cls.autonomy_level),
            memory_enabled=_env_bool("MEMORY_ENABLED", cls.memory_enabled),
            context_memory_budget=_env_int("CONTEXT_MEMORY_BUDGET", cls.context_memory_budget),
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
            api_connect_timeout=_env_float("API_CONNECT_TIMEOUT", cls.api_connect_timeout),
            api_read_timeout=_env_float("API_READ_TIMEOUT", cls.api_read_timeout),
            api_write_timeout=_env_float("API_WRITE_TIMEOUT", cls.api_write_timeout),
            api_pool_timeout=_env_float("API_POOL_TIMEOUT", cls.api_pool_timeout),
            api_max_retries=_env_int("API_MAX_RETRIES", cls.api_max_retries),
            debug=_env_bool("DEBUG", cls.debug),
        )


# Built once at import time, same as every other module-level client/config
# in this project (agent/chat.py's clients, agent/brain.py's TOOLS).
settings = Settings.load()
