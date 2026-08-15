"""The autonomy decision engine -- deterministic, testable, and
deliberately dumb: it never asks a model anything, and it never touches
the hard safety gates that already exist in tools/registry.py
(requires_live_confirmation, unattended_allowed). Those are permanent,
unconditional, and this module doesn't know or care what autonomy_level
is when deciding them -- they're checked separately, first, in
agent/executor.py's _run_tool, exactly as they were before this phase.

PERMISSION LEVEL (tools/registry.py) and AUTONOMY LEVEL (this module) are
two different questions:
  - Permission level: how dangerous/capable a tool action intrinsically
    is. Fixed per tool, never changes at runtime.
  - Autonomy level: how much initiative Jarvis is allowed to take
    *without asking* -- a per-user, per-session dial. The SAME tool at
    the SAME permission level can be auto-allowed at high autonomy and
    require a soft confirmation at low autonomy.

Autonomy can only ADD a confirmation requirement on top of what's already
allowed; it can never remove one. Concretely: autonomy_level determines
the highest permission_level that gets auto-allowed without asking.
Anything above that threshold requires the same soft two-step
confirmation pattern confirm_login/send_email already use (describe,
wait for an explicit yes, then the SAME tool call succeeds on retry) --
implemented generically here via a small pending-confirmation ledger,
rather than requiring a hand-built preview/confirm tool pair for every
tool the way the hard-gated ones have.

Level definitions (permission_level 0-5 auto-allowed without asking):
  0: confirm literally everything, even reads          (max caution)
  1: read-only (0) auto-allowed
  2: + safe local actions (1) auto-allowed              [DEFAULT would
  3: + modifies files/executes code (2) auto-allowed     be here if not
  4: + external comm & financial (3, 4) auto-allowed     for the note
                                                          below]
Only permission_level 5 (computer_confirm_action -- already hard-gated
regardless) and anything with a hard gate ever require confirmation at
autonomy level 4, which is why Settings.autonomy_level defaults to 4:
every tool that currently runs instantly keeps running instantly by
default (nothing below permission_level 5 changes), and the mechanism is
still fully real and testable by explicitly setting a lower level.
"""
import json
import time
from dataclasses import dataclass
from enum import Enum
from typing import Optional

from tools import registry

CONFIRMATION_WINDOW_SECONDS = 120

# Speech recognition can occasionally turn background conversation (a TV,
# a video, someone else's conversation) into a plausible-sounding
# imperative -- confirmed live: a wake-word listener repeatedly
# transcribed ambient audio as things like "call mom" and "grab me a
# coffee." A reminder is persistent, user-visible state; open_browser
# launches a real browser against a real destination; consult_coworker_
# agent is the entry point into a coworker agent's own tool loop
# (currently ResearchAgent's un-gated internal browsing, and in the
# future CodingAgent). All three need a deterministic confirmation from
# voice specifically, regardless of autonomy level, because voice input
# didn't come from deliberate keystrokes the way typed chat did.
_VOICE_ALWAYS_CONFIRMS = frozenset({"add_reminder", "open_browser", "consult_coworker_agent"})

_AUTONOMY_THRESHOLDS = {
    0: -1,
    1: 0,
    2: 1,
    3: 2,
    4: 4,
}
_MAX_DEFINED_LEVEL = max(_AUTONOMY_THRESHOLDS)


class Decision(str, Enum):
    ALLOW = "allow"
    CONFIRM = "confirm"
    DENY = "deny"


_LEVEL_DESCRIPTIONS = {
    0: "Confirm literally everything, even reads.",
    1: "Read-only actions run automatically; everything else needs confirmation.",
    2: "Read-only and safe local actions run automatically; everything else needs confirmation.",
    3: "Also allows code execution automatically; external communication and above still confirm.",
    4: "Allows everything except destructive/hard-gated actions automatically (default).",
}


def describe_level(autonomy_level: int) -> str:
    """Human-readable description for a given level, for display (e.g.
    the dashboard) -- not used in any decision logic."""
    return _LEVEL_DESCRIPTIONS.get(autonomy_level, _LEVEL_DESCRIPTIONS[_MAX_DEFINED_LEVEL])


@dataclass(frozen=True)
class ExecutionContext:
    """The minimal execution-context slice the decision actually needs --
    not the full RequestContext/ExecutionState, to keep this function's
    dependency surface small and easy to test in isolation."""
    source: str = "chat"


def should_request_confirmation(
    tool_name: str,
    autonomy_level: int,
    execution_context: Optional[ExecutionContext] = None,
) -> Decision:
    """Pure, deterministic. Never consults the model. The caller (agent/
    executor.py's _run_tool) is responsible for actually enforcing this
    verdict -- this function only decides."""

    execution_context = execution_context or ExecutionContext()

    permission_level = registry.permission_level(tool_name)
    if permission_level is None:
        # Unregistered tool -- be conservative, never auto-allow
        # something the permission system doesn't even know about.
        return Decision.CONFIRM

    if execution_context.source == "voice" and tool_name in _VOICE_ALWAYS_CONFIRMS:
        return Decision.CONFIRM

    threshold = _AUTONOMY_THRESHOLDS.get(
        autonomy_level, _AUTONOMY_THRESHOLDS[_MAX_DEFINED_LEVEL]
    )

    if permission_level <= threshold:
        return Decision.ALLOW

    if execution_context.source == "scheduled":
        # No live person to confirm with -- a CONFIRM verdict here could
        # never actually be answered, so it must be a hard no instead of
        # silently hanging or (worse) silently proceeding.
        return Decision.DENY

    return Decision.CONFIRM


# --- Generic pending-confirmation ledger -------------------------------
#
# Mirrors tools/autofill.py's _pending_confirmation pattern (a real check
# against stored state, not a prompt suggestion the model could talk
# itself out of) but generalized to any tool: the model describes the
# action, the user says yes, the model calls the SAME tool with the SAME
# input again, and *that* second call is the one that actually runs.
# One-time use and time-limited, same as the login-confirmation window.

_pending: dict = {}


def _input_key(tool_name: str, tool_input: dict) -> tuple:
    return (tool_name, json.dumps(tool_input, sort_keys=True, default=str))


def request_confirmation(tool_name: str, tool_input: dict) -> None:
    _pending[_input_key(tool_name, tool_input)] = time.time() + CONFIRMATION_WINDOW_SECONDS


def is_confirmed(tool_name: str, tool_input: dict) -> bool:
    """One-time check: True at most once per request_confirmation() call
    (consumes the pending entry either way), so a later, unrelated call
    with the same arguments can't accidentally ride along on an old
    approval."""
    key = _input_key(tool_name, tool_input)
    expires_at = _pending.pop(key, None)
    if expires_at is None:
        return False
    return time.time() <= expires_at
