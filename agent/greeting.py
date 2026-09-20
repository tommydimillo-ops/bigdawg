"""Deterministic detection of a bare wake-up greeting, plus the
system-prompt block that carries its server-side-prefetched time/weather.

Why this exists: agent/brain.py's greeting instruction tells the model to
call get_system_status + get_weather first, then reply with a fixed
template. In practice the routed model narrates a lead-in sentence ("I'll
get the time and weather for you") in its first completion, *before* the
tool calls -- so the user sees two separate messages and the turn costs
two provider round-trips. Two live-tested prompt-only attempts did not
reliably suppress that (see ROADMAP.md's "Say hi" entry). Running the two
read-only tools here, before the first model completion, and putting
their results in the prompt collapses the turn to a single completion
with the data already in hand.

Detection is pure string matching, never a model call -- same principle
as agent/autonomy.py and agent/delegation.py. It is deliberately strict
(exact match against a known phrase set, after light normalization): a
false positive pre-runs get_weather for a message that only looked like a
greeting, so "hey what's the weather" must NOT match here even though it
starts with "hey".
"""
from __future__ import annotations

from typing import List

# Mirrors the greeting phrases agent/brain.py's system prompt already
# enumerates ("hi", "hello", "hey", "wake up", "daddy's home", ...), plus
# the obvious close variants. Kept as data, not a regex, so it stays
# greppable and trivial to extend.
_GREETING_PHRASES = frozenset({
    # plain hellos
    "hi", "hii", "hiii", "hiya", "hello", "helloo", "hellooo",
    "hey", "heyy", "heyyy", "heya", "hey there", "hi there",
    "hello there", "yo", "howdy", "ahoy",
    "hows it going", "how's it going", "hey hey",
    # "what's up" family -- as a whole message these are unambiguously a
    # greeting, not a real question
    "sup", "whats up", "what's up", "wassup", "whatsup",
    # time-of-day
    "good morning", "good afternoon", "good evening",
    "morning", "mornin", "gm", "good day",
    # wake-ups
    "wake up", "wakey wakey", "wakey", "rise and shine",
    "you up", "you there", "you awake", "are you there",
    "are you up", "are you awake",
    # arrivals (brain.py lists "daddy's home", "dad's home")
    "daddy's home", "daddys home", "dad's home", "dads home",
    "i'm home", "im home", "i'm back", "im back",
    "honey i'm home", "honey im home",
    "knock knock",
})

# "jarvis" on either end is the user addressing the assistant, not part of
# the greeting -- brain.py's prompt makes the same point. Voice input
# strips it before the executor sees it; typed input and the plain mic
# button do not.
_ADDRESS_TOKENS = frozenset({"jarvis", "jarvo"})

_STRIP_CHARS = " \t\n\r.!?,;:~-\"'`"

# get_weather() catches its own failures and returns a plain string
# ("Couldn't fetch weather: ...", "Got a weather response but couldn't
# parse it: ..."); every real result carries a "Location:" line. If the
# prefetch got a failure string back, the caller falls through to the
# ordinary path (let the model fetch weather itself) rather than tell it
# not to.
_WEATHER_SUCCESS_MARKER = "Location:"


def _normalize(text: str) -> List[str]:
    cleaned = (text or "").lower().replace("’", "'")
    tokens = [token.strip(_STRIP_CHARS) for token in cleaned.split()]
    tokens = [token for token in tokens if token]
    while tokens and tokens[0] in _ADDRESS_TOKENS:
        tokens.pop(0)
    while tokens and tokens[-1] in _ADDRESS_TOKENS:
        tokens.pop()
    return tokens


def is_bare_greeting(text: str) -> bool:
    """True only for a message that is *just* a wake-up greeting -- see the
    module docstring on why this is intentionally strict."""
    return " ".join(_normalize(text)) in _GREETING_PHRASES


def weather_result_looks_usable(weather_result: str) -> bool:
    """Whether a get_weather() result is a real forecast rather than one of
    that function's own caught-error strings."""
    return bool(weather_result) and _WEATHER_SUCCESS_MARKER in weather_result


def format_greeting_context(status_result: str, weather_result: str) -> str:
    """The system-prompt block carrying the prefetched data. Returned with
    its own header baked in, so agent/brain.py just appends it after a
    separator -- same convention as agent/history_context.py's
    prompt_text."""
    return (
        "GREETING — the user greeted you. The system status and weather your "
        "greeting reply needs were retrieved for you just now, before this "
        "turn, and are below. Reply exactly once, immediately, with the "
        "greeting template from the guidance above filled in from this "
        "data — no lead-in sentence, no \"let me check\", no second "
        "message. Do not call get_system_status again. Do not call "
        "get_weather again unless you have a stored location for the user "
        "that differs from the one it resolved to below. If the status "
        "contains a line starting with \"WARNING:\", add one short sentence "
        "relaying it after the weather, inside that same single reply; "
        "otherwise mention nothing about it.\n\n"
        f"get_system_status:\n{status_result.strip()}\n\n"
        f"get_weather:\n{weather_result.strip()}"
    )
