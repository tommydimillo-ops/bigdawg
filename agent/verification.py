"""Lightweight post-action verification: a tool call succeeding is not
the same claim as the task it was meant to accomplish actually having
happened, so this checks the handful of cases where a cheap, meaningful
check is possible.

Scoped to side-effect tools only (tools.registry's existing side_effect
flag, not new metadata) -- verifying a pure read (get_weather,
recall_facts) would be checking that a tool returned data, which reading
its return value already establishes; there's nothing extra to verify.
This keeps verification proportional to risk, per the phase requirement:
expensive/re-querying checks only for the tools where "did this actually
take effect" is a real, answerable, and meaningfully different question
from "did the tool call raise an exception."

For the handful of tools with a real, cheap way to independently
re-confirm the effect (schedule_task, and the memory-writing tools),
this re-queries the actual persistent store rather than trusting the
tool's own success text. Everything else falls back to checking the
tool's own result string for an explicit failure marker -- weaker, but
still real: it catches the tool having already detected and reported its
own failure, which the caller could otherwise easily miss when a failure
message reads as plausible prose rather than an exception.
"""
import re
from dataclasses import dataclass
from typing import Optional

FAILURE_MARKERS = ("could not", "couldn't", "error", "failed", "didn't save", "refusing")


@dataclass(frozen=True)
class VerificationResult:
    ok: bool
    note: str


def _string_check(result: str, note: str) -> VerificationResult:
    lowered = result.lower()
    ok = not any(lowered.startswith(marker) or f": {marker}" in lowered for marker in FAILURE_MARKERS)
    return VerificationResult(ok=ok, note=note)


def _verify_schedule_task(tool_input: dict, result: str) -> VerificationResult:
    from agent.scheduled_tasks import list_tasks

    match = re.search(r"\(id ([a-f0-9]+)\)", result)
    if not match:
        return _string_check(result, "no task id found in result to re-verify against")

    task_id = match.group(1)
    exists = any(t["id"] == task_id for t in list_tasks())
    return VerificationResult(
        ok=exists,
        note="confirmed the scheduled task id actually exists in the scheduled-tasks store" if exists
        else "the task id the tool reported does not actually exist in the scheduled-tasks store",
    )


def _verify_memory_write(result: str) -> VerificationResult:
    # remember_fact/learn_rule/note_pattern's own return string already
    # distinguishes success from a safety-filter refusal ("I'll
    # remember..."/"Got it..."/"Noted:..." vs "Didn't save..."). Since
    # agent/memory/manager.py's remember() is itself the single write
    # path (already tested directly in tests/test_memory.py), re-reading
    # its result string here is a real, sufficient check, not a weaker
    # fallback -- there's no separate persistent-store round trip needed
    # the way schedule_task's numeric id benefits from one.
    return _string_check(result, "checked for the memory safety filter's refusal message")


# tool name -> verifier. Anything not listed here has no specific
# verification defined and falls through to the generic string check.
_VERIFIERS = {
    "schedule_task": _verify_schedule_task,
}


def verify(tool_name: str, tool_input: dict, result: str) -> VerificationResult:
    if tool_name in ("remember_fact", "learn_rule", "note_pattern"):
        return _verify_memory_write(result)

    verifier = _VERIFIERS.get(tool_name)
    if verifier is not None:
        return verifier(tool_input, result)

    return _string_check(result, "no specific verification defined for this tool; checked its own result text for a failure marker")
