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

    # --- Agent loop ---
    max_agent_steps: int = 8

    # --- Autonomy / permissions ---
    # NOT YET WIRED to anything -- the six permission levels and the
    # confirmation/unattended-execution gates in tools/registry.py are
    # entirely unaffected by this value today. It exists as typed,
    # available configuration surface for a future phase to enforce
    # without another config migration; changing it right now has zero
    # effect on what Jarvis will or won't do.
    autonomy_level: int = 2

    # --- Memory ---
    # NOT YET WIRED -- nothing currently checks this before reading/
    # writing memory. Reserved for a future on/off toggle.
    memory_enabled: bool = True

    # --- Voice ---
    # voice_enabled is NOT YET WIRED (nothing currently checks it -- the
    # menu-bar app's voice loop always runs). wake_word and
    # voice_sample_rate ARE real and already load-bearing.
    voice_enabled: bool = True
    wake_word: str = "jarvis"
    voice_sample_rate: int = 16000
    user_name: str = "Tommy"

    # --- Scheduler ---
    scheduler_poll_seconds: int = 30

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
            max_agent_steps=_env_int("MAX_AGENT_STEPS", cls.max_agent_steps),
            autonomy_level=_env_int("AUTONOMY_LEVEL", cls.autonomy_level),
            memory_enabled=_env_bool("MEMORY_ENABLED", cls.memory_enabled),
            voice_enabled=_env_bool("VOICE_ENABLED", cls.voice_enabled),
            wake_word=_env_str("WAKE_WORD", cls.wake_word),
            voice_sample_rate=_env_int("VOICE_SAMPLE_RATE", cls.voice_sample_rate),
            user_name=_env_str("JARVIS_USER_NAME", cls.user_name),
            scheduler_poll_seconds=_env_int("SCHEDULER_POLL_SECONDS", cls.scheduler_poll_seconds),
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
