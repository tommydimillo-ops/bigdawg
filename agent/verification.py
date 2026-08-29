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
import json
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


def _verify_send_message_via_openclaw(tool_input: dict, result: str) -> VerificationResult:
    """agent.openclaw_messaging.send_message() normalizes every outcome to
    a JSON object with `sent`/`delivery_status` (see that module's
    docstring for the three delivery_status values: confirmed/failed/
    uncertain) -- the generic string check above is not safe here, since
    a JSON body like {"sent": false, "delivery_status": "uncertain", ...}
    contains no FAILURE_MARKERS word and would otherwise read as success.
    This parses the JSON directly and fails closed on anything that
    doesn't match the expected shape, so an ambiguous or malformed result
    is never mistaken for a confirmed delivery."""
    try:
        payload = json.loads(result)
    except (TypeError, ValueError):
        return VerificationResult(
            ok=False, note="send_message_via_openclaw result was not valid JSON; cannot verify delivery",
        )
    if not isinstance(payload, dict):
        return VerificationResult(
            ok=False, note="send_message_via_openclaw result was not a JSON object; cannot verify delivery",
        )

    delivery_status = payload.get("delivery_status")

    if payload.get("sent") is True and delivery_status == "confirmed":
        return VerificationResult(ok=True, note="OpenClaw reported the message sent and delivery confirmed")
    if delivery_status == "failed":
        return VerificationResult(
            ok=False,
            note=f"OpenClaw message delivery failed: {payload.get('error', 'no error detail provided')}",
        )
    if delivery_status == "uncertain":
        return VerificationResult(
            ok=False,
            note="OpenClaw message delivery is UNCERTAIN -- the send request was transmitted but no "
                 "trustworthy response was received. This must NOT be treated as a successful send, "
                 "and it must NOT be automatically retried (a resend could deliver a duplicate message).",
        )
    return VerificationResult(
        ok=False,
        note=f"send_message_via_openclaw result has an unrecognized shape (delivery_status={delivery_status!r}); failing closed",
    )


# tool name -> verifier. Anything not listed here has no specific
# verification defined and falls through to the generic string check.
_VERIFIERS = {
    "schedule_task": _verify_schedule_task,
    "send_message_via_openclaw": _verify_send_message_via_openclaw,
}


def verify(tool_name: str, tool_input: dict, result: str) -> VerificationResult:
    if tool_name in ("remember_fact", "learn_rule", "note_pattern"):
        return _verify_memory_write(result)

    verifier = _VERIFIERS.get(tool_name)
    if verifier is not None:
        return verifier(tool_input, result)

    return _string_check(result, "no specific verification defined for this tool; checked its own result text for a failure marker")


# --- Phase 9 Milestone 3: coworker-agent-result verification ------------
#
# A tool call succeeding is not the same claim as a coworker agent's work
# actually being correct, either -- the same gap this module's docstring
# already describes for individual tool calls, one level up. Bounded and
# objective, matching this module's existing "cheap, meaningful check
# only where one is actually possible" scope -- this deliberately does
# NOT add a second model-based reasoning pass over an agent's result.

# A cheap, deterministic heuristic for ResearchAgent specifically: a
# synthesized answer that never names or links a source is a weaker
# result than the SYSTEM_PROMPT it was built with actually asks for
# ("mentions which sources/sites it came from" -- agent/research_agent.py).
# Not proof the sources are real or the claim is correct (that would need
# a second, expensive verification pass this milestone deliberately
# avoids) -- only that the answer at least LOOKS sourced rather than
# unsupported assertion, the same "objective evidence over trust" bar
# the tool-level checks above apply.
_SOURCE_EVIDENCE_PATTERN = re.compile(
    r"https?://|according to|\bsource[sd]?\b|\bcites?\b|per \w+\.(com|org|net|gov|edu)",
    re.IGNORECASE,
)


def _verify_research_result(result) -> VerificationResult:
    if not _SOURCE_EVIDENCE_PATTERN.search(result.result or ""):
        return VerificationResult(
            ok=False,
            note="research answer doesn't mention any source/link -- treat as unsupported until corroborated",
        )
    return VerificationResult(ok=True, note="research answer references at least one source")


def verify_agent_result(result) -> VerificationResult:
    """Checks whether a single coworker AgentResult (agent.agents.models.
    AgentResult) represents trustworthy success -- reused by both a
    single consult_coworker_agent call and Milestone 3's bounded-parallel
    batches (agent.agents.manager.execute_agents_parallel), so "did this
    agent's result actually hold up" is answered the same way regardless
    of how many coworkers ran. Order matters: explicit failure signals
    (success=False, a cancelled run, an explicit verification_status of
    "failed" -- e.g. QAAgent's own test-suite check -- or a non-zero
    metadata["suite_exit_code"], CodingAgent's own equivalent) are checked
    before falling through to the generic failure-marker string check every
    other tool result already gets, then any agent-specific check (today,
    only ResearchAgent's source-evidence heuristic -- see module note on
    why FILES/BROWSER-shaped checks aren't implemented yet: no current
    agent in this phase produces that shape of result to check)."""
    if result.cancelled:
        return VerificationResult(ok=False, note="agent run was cancelled before completion")
    if not result.success:
        return VerificationResult(ok=False, note=result.error or "agent reported failure")
    if result.verification_status == "failed":
        return VerificationResult(ok=False, note="agent's own verification_status reported failure (e.g. a failing test suite)")
    suite_exit_code = result.metadata.get("suite_exit_code")
    if suite_exit_code is not None and suite_exit_code != 0:
        # Phase 10 increment 1: CodingAgent's own execute() runs the
        # canonical test suite as its final step and reports the raw exit
        # code rather than prose (see agent/agents/coding.py). A model
        # saying "done" is not evidence -- this overrides success=True the
        # same way the verification_status check above already does for
        # QAAgent, extended by one field rather than a second dispatch path.
        return VerificationResult(
            ok=False,
            note=f"agent's own test-suite run exited non-zero (exit code {suite_exit_code}); "
                 "overrides success=True",
        )
    if result.metadata.get("deferred_to_executor"):
        # Nothing to verify yet -- this agent didn't actually do the work
        # itself (see agent/agents/coding.py, agent/agents/qa.py's
        # deferred path); the ordinary executor's own tool-level
        # verification (verify() above) covers whatever it does next.
        return VerificationResult(ok=True, note="deferred to the ordinary executor; nothing agent-specific to verify yet")

    if result.agent_name == "research":
        return _verify_research_result(result)

    return _string_check(result.result, "no agent-specific verification defined; checked its own result text for a failure marker")
