import concurrent.futures
import json
import time

import httpx

import tools.schemas  # noqa: F401 -- populates tools.registry as a side effect
from agent import execution_history, jarvis_state
import agent.agents  # noqa: F401 -- populates agent.agents.manager's registry as a side effect
from agent.agents.router import route as route_agent_task
from agent.audit import log_action
from agent.autonomy import Decision, ExecutionContext as AutonomyContext, is_confirmed, request_confirmation, should_request_confirmation
from agent.brain import build_system_prompt, TOOLS, client as claude_client
from agent.chat import openai_client, xai_client
from agent.execution_state import ExecutionState, ExecutionStatus, register_active, unregister_active
from agent.cancellation import cancellation_requested, clear_cancellation
from agent.delegation import decide as decide_delegation
from agent import history_capture
from agent import provider_health
from agent.model_router import build_fallback_chain, select as select_model
from agent.observability import log_event, preview
from agent.planner import create_plan, is_complex
from agent.request_context import RequestContext, bind_current_request_id, unbind_current_request_id
from agent.retry_policy import retry_delay_seconds, should_retry
from agent.secrets import get_secret
from agent.task_classifier import classify as classify_task
from agent.usage import check_request_limits, record_llm_usage
from agent.verification import verify
from config.settings import settings
from tools import registry

MAX_TOOL_ITERATIONS = settings.max_agent_steps


def _bounded_model_history(history, limit=None):
    """Keep provider context bounded while leaving stored/UI history intact.

    Retains the most recent messages because they contain the current turn
    and any immediately preceding confirmation exchange. A non-positive
    limit intentionally disables truncation for diagnostics.
    """
    messages = list(history or [])
    cap = settings.model_history_limit if limit is None else limit
    if cap <= 0 or len(messages) <= cap:
        return messages
    bounded = messages[-cap:]
    # Stored conversation normally alternates user/assistant and ends with
    # the current user turn. An even-sized tail can therefore begin with an
    # orphan assistant message, which some providers reject and which lacks
    # the question it answered. Advance to the first complete user turn.
    while bounded and bounded[0].get("role") != "user":
        bounded.pop(0)
    return bounded


def _is_cancelled(state):
    """Synchronize a cross-process cancel marker into live request state."""
    if not state:
        return False
    if not state.cancelled and cancellation_requested(state.request_id):
        state.cancel()
    return state.cancelled


class PartialToolExecution(Exception):
    """Raised when a provider fails after already committing a side effect."""


OPENAI_TOOLS = registry.openai_schemas()


def _plan_progress(state):
    """A short display string like "2/4" for agent.jarvis_state, or None
    when no plan is attached (ordinary, non-multi-step requests)."""
    if state is None or state.plan is None:
        return None
    return f"{state.plan.completed_count}/{state.plan.total}"


def _run_tool(name, tool_input, source="chat", context=None, state=None):
    """Every tool call funnels through here, so this is the one place that
    needs to log — individual tool functions don't need to know about it.

    Order matters: the pre-existing hard gates (scheduled + requires_live_
    confirmation, scheduled + not unattended_allowed) are checked first and
    are completely unaffected by autonomy_level -- they're not something
    autonomy can loosen. The autonomy check only ever runs for tools that
    already cleared those, and can only ever ADD a soft confirmation step,
    never remove one."""

    request_id = context.request_id if context else None
    autonomy_level = context.autonomy_level if context else settings.autonomy_level

    if _is_cancelled(state):
        result = f"Skipped: {name} was not run because the request was cancelled."
        log_action(name, tool_input, result)
        log_event("tool_skipped", request_id=request_id, component="executor", tool=name, reason="cancelled")
        return result

    if source == "scheduled" and registry.requires_live_confirmation(name):
        result = (
            f"Skipped: {name} requires a live conversation with the user "
            "present to confirm — it can't run from a scheduled task."
        )
        log_action(name, tool_input, result)
        log_event("tool_skipped", request_id=request_id, component="executor", tool=name, reason="requires_live_confirmation")
        return result

    if source == "scheduled" and not registry.unattended_allowed(name):
        result = f"Skipped: {name} can't run unattended from a scheduled task."
        log_action(name, tool_input, result)
        log_event("tool_skipped", request_id=request_id, component="executor", tool=name, reason="unattended_not_allowed")
        return result

    autonomy_ctx = AutonomyContext(source=source)
    decision = should_request_confirmation(name, autonomy_level, autonomy_ctx)

    if decision == Decision.DENY:
        result = f"Skipped: {name} needs confirmation at the current autonomy level, which isn't possible unattended."
        log_action(name, tool_input, result)
        log_event("tool_skipped", request_id=request_id, component="executor", tool=name, reason="autonomy_denied_unattended")
        return result

    if decision == Decision.CONFIRM and not is_confirmed(name, tool_input):
        request_confirmation(name, tool_input)
        if state:
            state.request_confirmation(name)
        jarvis_state.set_status(
            ExecutionStatus.WAITING_FOR_CONFIRMATION, active_request_id=request_id,
            current_task=context.user_input if context else None, current_tool=name,
            confirmation_pending=True, plan_progress=_plan_progress(state),
        )
        result = (
            f"Before I run {name}, I want your OK first (current autonomy "
            "level requires confirmation for this kind of action) — "
            "please confirm and I'll go ahead."
        )
        log_action(name, tool_input, result)
        log_event("tool_confirmation_requested", request_id=request_id, component="executor", tool=name)
        return result

    if state:
        state.clear_confirmation()
        state.transition_to(ExecutionStatus.EXECUTING)
    jarvis_state.set_status(
        ExecutionStatus.EXECUTING, active_request_id=request_id,
        current_task=context.user_input if context else None, current_tool=name,
        plan_progress=_plan_progress(state),
    )

    log_event("tool_started", request_id=request_id, component="executor", tool=name)
    start = time.time()

    attempt = 0
    while True:
        attempt += 1
        try:
            result = registry.dispatch(name, tool_input)
            break
        except Exception as error:
            log_action(name, tool_input, f"ERROR: {error}")
            log_event(
                "tool_failed", request_id=request_id, component="executor", level="error",
                tool=name, duration=time.time() - start, error_type=type(error).__name__, attempt=attempt,
            )
            if _is_cancelled(state):
                result = f"Stopped: {name} failed and won't be retried because the request was cancelled."
                log_action(name, tool_input, result)
                log_event("tool_retry_cancelled", request_id=request_id, component="executor", tool=name, attempt=attempt)
                state.record_tool(name, result_preview=result, ok=False, duration_seconds=time.time() - start)
                return result
            if should_retry(name, error, attempt):
                log_event("tool_retry", request_id=request_id, component="executor", tool=name, attempt=attempt)
                time.sleep(retry_delay_seconds(attempt))
                continue
            if state:
                state.record_tool(name, result_preview=f"ERROR: {error}", ok=False, duration_seconds=time.time() - start)
            raise

    duration = time.time() - start

    verification_note = None
    if name in registry.side_effect_tools():
        if state:
            state.transition_to(ExecutionStatus.VERIFYING)
        jarvis_state.set_status(
            ExecutionStatus.VERIFYING, active_request_id=request_id,
            current_task=context.user_input if context else None, current_tool=name,
            plan_progress=_plan_progress(state),
        )
        verification = verify(name, tool_input, result)
        log_event(
            "tool_verified", request_id=request_id, component="executor",
            tool=name, ok=verification.ok, note=verification.note,
        )
        if not verification.ok:
            verification_note = verification.note
            result = f"{result}\n\n(Verification note: {verification.note} — double-check this actually happened.)"

    log_action(name, tool_input, result)
    log_event(
        "tool_completed", request_id=request_id, component="executor",
        tool=name, duration=duration, result_preview=preview(result),
    )
    if state:
        state.record_tool(name, result_preview=result, ok=verification_note is None, duration_seconds=duration)
    return result


def _text_from(content_blocks):
    return "".join(block.text for block in content_blocks if block.type == "text").strip()


def _run_tool_batch(tool_calls, source="chat", context=None, state=None):
    """Runs every tool_use block from a single model response. When the
    model asks for several independent lookups in one turn (e.g. "check my
    battery and search my files"), running them one at a time is wasted
    wall-clock time — this runs the ones verified safe for concurrency in
    parallel and leaves everything else sequential. Returns a dict of
    tool_use_id -> result, in no particular order (callers match by id)."""

    parallel_safe = registry.parallel_safe_tools()
    parallel_calls = [call for call in tool_calls if call["name"] in parallel_safe]
    sequential_calls = [call for call in tool_calls if call["name"] not in parallel_safe]

    results = {}

    if parallel_calls:
        with concurrent.futures.ThreadPoolExecutor(max_workers=len(parallel_calls)) as executor:
            futures = {
                executor.submit(_run_tool, call["name"], call["input"], source, context, state): call["id"]
                for call in parallel_calls
            }
            for future in concurrent.futures.as_completed(futures):
                results[futures[future]] = future.result()

    for call in sequential_calls:
        if _is_cancelled(state):
            results[call["id"]] = f"Skipped: {call['name']} was not run because the request was cancelled."
            continue
        results[call["id"]] = _run_tool(call["name"], call["input"], source, context, state)

    return results


def _run_claude_loop_stream(messages, source="chat", context=None, state=None, model_choice=None, task_type=None, fallback_position=0):
    """Yields response text as it's generated (including any text a model
    emits alongside a tool call, e.g. "Let me check that..."), so the UI can
    show it immediately instead of waiting for the whole multi-step loop.

    model_choice defaults to the original static select_model(attempt=0)
    outcome when not given, so any existing caller (or test) that
    doesn't know about Phase 9 Milestone 2's router keeps getting
    exactly the same model it always did."""

    request_id = context.request_id if context else None
    if model_choice is None:
        model_choice = select_model(attempt=0, context=context)
    if state:
        state.record_model(model_choice.provider, model_choice.model)
    log_event(
        "model_selected", request_id=request_id, component="executor",
        provider=model_choice.provider, model=model_choice.model,
    )

    messages = list(messages)
    committed = False
    # Built fresh per request (not cached at import time) so a rule learned
    # in an earlier turn — or via the CLI — takes effect immediately.
    # cache_control marks everything before it (the tools list, ~3.8k
    # tokens, plus this system text) as reusable: Anthropic caches that
    # prefix for a few minutes, so back-to-back turns in a conversation
    # skip reprocessing it from scratch instead of paying for it every call.
    system_prompt = [
        {
            "type": "text",
            "text": build_system_prompt(context.user_input if context else "", request_id=request_id, state=state),
            "cache_control": {"type": "ephemeral"},
        }
    ]

    for _ in range(MAX_TOOL_ITERATIONS):

        if _is_cancelled(state):
            log_event("request_cancelled", request_id=request_id, component="executor")
            yield "Stopped, as requested."
            return

        limit_check = check_request_limits(request_id)
        if limit_check.exceeded:
            log_event(
                "request_limit_exceeded", request_id=request_id, component="executor",
                reason=limit_check.reason,
            )
            yield f"Stopping here — {limit_check.reason}."
            return

        if state:
            state.record_iteration()
            state.transition_to(ExecutionStatus.THINKING)
        jarvis_state.set_status(
            ExecutionStatus.THINKING, active_request_id=request_id,
            current_task=context.user_input if context else None,
            plan_progress=_plan_progress(state),
        )
        log_event(
            "agent_iteration", request_id=request_id, component="executor",
            iteration=state.iteration if state else None, provider="anthropic",
        )

        try:
            _call_started = time.time()
            with claude_client.messages.stream(
                model=model_choice.model,
                # Claude Sonnet 5 does adaptive extended thinking
                # automatically for problems it judges as hard, and
                # thinking tokens count against this same budget — 1024
                # was tight enough that a real request (a multi-constraint
                # scheduling problem) hit stop_reason="max_tokens" with the
                # entire budget consumed by thinking alone, leaving zero
                # room for the actual response. Confirmed via a live
                # repro, not a guess.
                max_tokens=4096,
                system=system_prompt,
                tools=TOOLS,
                messages=messages,
            ) as stream:
                yielded_any = False
                for text in stream.text_stream:
                    yielded_any = True
                    yield text
                response = stream.get_final_message()
            usage = getattr(response, "usage", None)
            if usage is not None:
                record_llm_usage(
                    provider="anthropic", model=model_choice.model, operation="chat",
                    request_id=request_id, agent=state.active_agent if state else None,
                    input_tokens=getattr(usage, "input_tokens", 0), output_tokens=getattr(usage, "output_tokens", 0),
                    duration_seconds=time.time() - _call_started,
                    task_type=task_type, fallback_position=fallback_position,
                )
        except Exception as error:
            log_event(
                "model_call_failed", request_id=request_id, component="executor",
                level="warning", provider="anthropic", error_type=type(error).__name__,
            )
            provider_health.record_failure("anthropic")
            if committed:
                raise PartialToolExecution from error
            raise

        provider_health.clear_failure("anthropic")

        if response.stop_reason != "tool_use":
            if not yielded_any:
                yield "I'm not sure how to respond to that."
            return

        messages.append({"role": "assistant", "content": response.content})

        tool_use_blocks = [block for block in response.content if block.type == "tool_use"]
        for block in tool_use_blocks:
            log_event("tool_selected", request_id=request_id, component="executor", tool=block.name)

        results_by_id = _run_tool_batch(
            [{"id": b.id, "name": b.name, "input": b.input} for b in tool_use_blocks],
            source=source,
            context=context,
            state=state,
        )

        if any(block.name in registry.side_effect_tools() for block in tool_use_blocks):
            committed = True

        tool_results = [
            {"type": "tool_result", "tool_use_id": block.id, "content": str(results_by_id[block.id])}
            for block in tool_use_blocks
        ]

        messages.append({"role": "user", "content": tool_results})

    yield "That took more steps than expected — could you rephrase your request?"


def _client_for_provider(provider):
    """Looks up the shared client by NAME each call (not a dict captured
    once at import time), so a test's @patch("agent.executor.openai_client",
    ...) -- the existing pattern for claude_client -- works identically
    for xai_client too. Perplexity is deliberately absent: its Agent API
    call (_call_perplexity_agent below) never goes through this generic
    chat.completions.create dispatch -- see _run_perplexity_agent_loop."""
    return {"openai": openai_client, "xai": xai_client}.get(provider)


def _run_openai_compatible_loop(model_choice, messages, source="chat", context=None, state=None, task_type=None, fallback_position=1):
    """The shared loop for every OpenAI-API-compatible provider (OpenAI
    itself, xAI, Perplexity -- confirmed against each provider's current
    docs that the request/response shape is identical, just a different
    base_url/api_key, see agent/chat.py) -- one implementation instead of
    three near-identical ones. Only Anthropic gets its own loop
    (_run_claude_loop_stream): a genuinely different SDK and native
    streaming, not just a different provider name."""

    request_id = context.request_id if context else None
    client = _client_for_provider(model_choice.provider)
    if state:
        state.record_model(model_choice.provider, model_choice.model)
    log_event(
        "model_selected", request_id=request_id, component="executor",
        provider=model_choice.provider, model=model_choice.model, fallback_position=fallback_position,
    )

    messages = [{"role": "system", "content": build_system_prompt(context.user_input if context else "", request_id=request_id, state=state)}] + list(messages)
    committed = False

    for _ in range(MAX_TOOL_ITERATIONS):

        if _is_cancelled(state):
            log_event("request_cancelled", request_id=request_id, component="executor")
            return "Stopped, as requested."

        limit_check = check_request_limits(request_id)
        if limit_check.exceeded:
            log_event(
                "request_limit_exceeded", request_id=request_id, component="executor",
                reason=limit_check.reason,
            )
            return f"Stopping here — {limit_check.reason}."

        if state:
            state.record_iteration()
            state.transition_to(ExecutionStatus.THINKING)
        jarvis_state.set_status(
            ExecutionStatus.THINKING, active_request_id=request_id,
            current_task=context.user_input if context else None,
            plan_progress=_plan_progress(state),
        )
        log_event(
            "agent_iteration", request_id=request_id, component="executor",
            iteration=state.iteration if state else None, provider=model_choice.provider,
        )

        try:
            _call_started = time.time()
            response = client.chat.completions.create(
                model=model_choice.model,
                messages=messages,
                tools=OPENAI_TOOLS,
            )
        except Exception as error:
            log_event(
                "model_call_failed", request_id=request_id, component="executor",
                level="warning", provider=model_choice.provider, error_type=type(error).__name__,
            )
            provider_health.record_failure(model_choice.provider)
            if committed:
                raise PartialToolExecution from error
            raise

        provider_health.clear_failure(model_choice.provider)

        usage = getattr(response, "usage", None)
        if usage is not None:
            record_llm_usage(
                provider=model_choice.provider, model=model_choice.model, operation="fallback",
                request_id=request_id, agent=state.active_agent if state else None,
                input_tokens=getattr(usage, "prompt_tokens", 0), output_tokens=getattr(usage, "completion_tokens", 0),
                duration_seconds=time.time() - _call_started,
                task_type=task_type, fallback_position=fallback_position,
            )

        choice = response.choices[0].message

        if not choice.tool_calls:
            return choice.content or "I'm not sure how to respond to that."

        messages.append(choice.model_dump(exclude_unset=True))

        tool_calls = [
            {"id": call.id, "name": call.function.name, "input": json.loads(call.function.arguments or "{}")}
            for call in choice.tool_calls
        ]
        for call in tool_calls:
            log_event("tool_selected", request_id=request_id, component="executor", tool=call["name"])

        results_by_id = _run_tool_batch(tool_calls, source=source, context=context, state=state)

        if any(call["name"] in registry.side_effect_tools() for call in tool_calls):
            committed = True

        for call in tool_calls:
            messages.append({
                "role": "tool",
                "tool_call_id": call["id"],
                "content": str(results_by_id[call["id"]]),
            })

    return "That took more steps than expected — could you rephrase your request?"


def _run_openai_compatible_loop_stream(model_choice, messages, source="chat", context=None, state=None, task_type=None, fallback_position=1):
    # None of OpenAI/xAI get real incremental streaming in this project
    # (see ModelChoice.CAPABILITIES' supports_streaming note) -- not
    # worth the added complexity of accumulating streamed tool-call
    # deltas for what's always a fallback tier, so this just yields the
    # one final answer at once.
    yield _run_openai_compatible_loop(
        model_choice, messages, source=source, context=context, state=state,
        task_type=task_type, fallback_position=fallback_position,
    )


PERPLEXITY_AGENT_API_URL = "https://api.perplexity.ai/v1/agent"


def _extract_agent_api_text(data):
    """Walks the Agent API's output[] array (a step-per-item list, not a
    single choices[0].message.content the way OpenAI-style responses
    are) for the "message" item holding the final answer text. See
    config.settings.Settings.perplexity_model's docstring for why this
    provider's response shape genuinely differs from every other one
    here."""
    for item in data.get("output", []) or []:
        if item.get("type") == "message":
            for block in item.get("content", []) or []:
                if block.get("type") == "output_text":
                    return block.get("text", "")
    return ""


def _call_perplexity_agent(preset, input_text, instructions):
    """One direct, non-streaming call to Perplexity's Agent API (POST
    /v1/agent) -- NOT routed through _client_for_provider/the openai
    package, because the request/response shape genuinely differs from
    every OpenAI-compatible provider (input/output, not messages/
    choices; see agent/chat.py's comment on perplexity_client for why).
    `preset` is the Agent API preset string (config.settings.Settings.
    perplexity_model's actual value, e.g. "low"), not a flat-rate model
    ID -- see that field's own docstring."""
    key = get_secret("PERPLEXITY_API_KEY")
    body = {"input": input_text, "preset": preset}
    if instructions:
        body["instructions"] = instructions
    response = httpx.post(
        PERPLEXITY_AGENT_API_URL,
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        json=body,
        timeout=httpx.Timeout(
            connect=settings.api_connect_timeout, read=settings.api_read_timeout,
            write=settings.api_write_timeout, pool=settings.api_pool_timeout,
        ),
    )
    response.raise_for_status()
    return response.json()


def _run_perplexity_agent_loop(model_choice, messages, source="chat", context=None, state=None, task_type=None, fallback_position=1):
    """Perplexity is used narrowly and single-shot: one grounded research
    query in, one grounded answer back -- Jarvis stays the only
    orchestrator (Task -> route -> Perplexity -> grounded research
    result -> Jarvis), never a multi-step tool-calling loop the way
    OpenAI/xAI get via _run_openai_compatible_loop against Jarvis's own
    53-tool registry. This deliberately means a request routed to
    Perplexity can't also perform a Jarvis tool action (e.g. saving a
    reminder) in the same turn -- an accepted scope limit of a research-
    only provider, not an oversight: Perplexity never receives Jarvis's
    tool schemas, so it can never be handed the ability to call back into
    them either."""

    request_id = context.request_id if context else None
    if state:
        state.record_model(model_choice.provider, model_choice.model)
    log_event(
        "model_selected", request_id=request_id, component="executor",
        provider=model_choice.provider, model=model_choice.model, fallback_position=fallback_position,
    )

    if _is_cancelled(state):
        log_event("request_cancelled", request_id=request_id, component="executor")
        return "Stopped, as requested."

    limit_check = check_request_limits(request_id)
    if limit_check.exceeded:
        log_event(
            "request_limit_exceeded", request_id=request_id, component="executor",
            reason=limit_check.reason,
        )
        return f"Stopping here — {limit_check.reason}."

    if state:
        state.record_iteration()
        state.transition_to(ExecutionStatus.THINKING)
    jarvis_state.set_status(
        ExecutionStatus.THINKING, active_request_id=request_id,
        current_task=context.user_input if context else None,
        plan_progress=_plan_progress(state),
    )
    log_event(
        "agent_iteration", request_id=request_id, component="executor",
        iteration=state.iteration if state else None, provider="perplexity",
    )

    latest_user_text = next(
        (m.get("content") for m in reversed(messages) if m.get("role") == "user" and isinstance(m.get("content"), str)),
        context.user_input if context else "",
    )
    instructions = build_system_prompt(context.user_input if context else "", request_id=request_id, state=state)

    try:
        _call_started = time.time()
        data = _call_perplexity_agent(model_choice.model, latest_user_text, instructions)
    except Exception as error:
        log_event(
            "model_call_failed", request_id=request_id, component="executor",
            level="warning", provider="perplexity", error_type=type(error).__name__,
        )
        provider_health.record_failure("perplexity")
        raise

    provider_health.clear_failure("perplexity")

    usage = data.get("usage") or {}
    if usage:
        record_llm_usage(
            provider="perplexity", model=model_choice.model, operation="fallback",
            request_id=request_id, agent=state.active_agent if state else None,
            input_tokens=usage.get("input_tokens", 0), output_tokens=usage.get("output_tokens", 0),
            duration_seconds=time.time() - _call_started,
            task_type=task_type, fallback_position=fallback_position,
        )

    return _extract_agent_api_text(data) or "I couldn't find a grounded answer to that."


def _run_perplexity_agent_loop_stream(model_choice, messages, source="chat", context=None, state=None, task_type=None, fallback_position=1):
    # Single-shot, same as _run_openai_compatible_loop_stream -- yields
    # the one final answer at once, no incremental streaming.
    yield _run_perplexity_agent_loop(
        model_choice, messages, source=source, context=context, state=state,
        task_type=task_type, fallback_position=fallback_position,
    )


PARTIAL_EXECUTION_MESSAGE = (
    "Something interrupted that request after it had already taken an "
    "action (like saving a reminder or a note) — please check before "
    "repeating it."
)


def execute_task_stream(request, history=None, source="chat", on_state_created=None):
    """Execute one request and stream response text.

    ``on_state_created`` is an optional observer for interfaces that need
    the exact request-scoped :class:`ExecutionState` (currently voice). It
    does not participate in execution or permission decisions, and existing
    callers can continue using the original three-argument API.
    """

    context = RequestContext.create(request, source=source)
    state = ExecutionState(max_iterations=MAX_TOOL_ITERATIONS)
    request_start = time.time()
    clear_cancellation(context.request_id)

    # Phase 9 M4.2: record the user's ask as early as a real request_id
    # exists and before any model/provider call is attempted (including
    # is_complex()'s own planning-model call below) -- so if Jarvis
    # crashes or is cancelled immediately after, the fact the user asked
    # this still survives. Deterministic, non-model-controlled: this call
    # always happens, regardless of what any model later decides to do.
    # Best-effort and non-fatal by construction (agent.history_capture
    # never raises) -- a history failure must never block the real task.
    history_capture.capture_user_turn(source, context.request_id, request)

    # Phase 8 part 5: bind this request's id so anything deeper in the
    # call stack -- including a tool handler with no context parameter
    # of its own, e.g. consult_coworker_agent -- can correlate back to
    # it. Reset in the same finally block that already handles this
    # request's other end-of-life cleanup, regardless of how execution
    # ends (completed, failed, cancelled, or an exception escapes).
    _request_id_token = bind_current_request_id(context.request_id)

    # Delegation (Phase 6.5): decide whether a skill's instructions
    # should be attached to this request -- a plain keyword-overlap
    # match against agent.skills.registry, no model call. Only ever
    # *attaches extra system-prompt context*; it doesn't change what
    # tools are allowed, dispatch anything itself, or affect any
    # permission/confirmation decision later in this same request.
    decision = decide_delegation(request)
    state.selected_skill = decision.skill
    state.delegation_destination = decision.destination.value
    log_event(
        "delegation_decided", request_id=context.request_id, component="delegation",
        destination=decision.destination.value, skill=decision.skill,
        confidence=round(decision.confidence, 2), reason=decision.reason,
    )

    # Agent Manager (Phase 7): a PURE routing decision only -- which
    # coworker agent (see agent/agents/) this request looks like it's
    # for, purely for attribution/observability (dashboard, execution
    # history). Deliberately does NOT execute an agent here: the actual
    # agents (agent/agents/coding.py etc.) reuse real infrastructure
    # (network calls for ResearchAgent, a real memory write for
    # MemoryAgent), and this call site runs unconditionally on every
    # request through the one shared execute_task_stream every existing
    # test already exercises -- invoking an agent for real from here
    # would mean any request whose text happens to match a routing
    # keyword makes a real, unmocked, potentially paid call the moment
    # execute_task_stream runs, including from tests that were never
    # written expecting that. Real agent execution instead happens
    # through the same tool-registry/permission/confirmation path every
    # other capability in this project already uses -- see
    # tools/schemas/agents.py's consult_coworker_agent tool, which is
    # reachable exactly like any other tool the model chooses to call,
    # and therefore just as safely mockable in tests.
    agent_decision = route_agent_task(request)
    if agent_decision.destination.value != "direct":
        state.record_agent(agent_decision.agent_name, request, "routed")
    log_event(
        "agent_routing_decided", request_id=context.request_id, component="agents",
        destination=agent_decision.destination.value, confidence=round(agent_decision.confidence, 2),
        reason=agent_decision.reason,
    )

    # Only genuinely multi-part requests pay for the extra planning model
    # call -- ordinary conversation stays on the fast path with no plan
    # attached at all (state.plan stays None, exactly like before this
    # phase existed).
    if is_complex(request):
        planning_started = time.perf_counter()
        state.transition_to(ExecutionStatus.PLANNING)
        jarvis_state.set_status(
            ExecutionStatus.PLANNING, active_request_id=context.request_id, current_task=preview(request),
        )
        state.plan = create_plan(request)
        log_event(
            "plan_created", request_id=context.request_id, component="planner",
            total_steps=state.plan.total, request_preview=preview(request),
            duration_ms=round((time.perf_counter() - planning_started) * 1000, 1),
        )

    register_active(context.request_id, state)
    if on_state_created is not None:
        try:
            on_state_created(state)
        except Exception as error:
            # An interface observer must never be able to break the agent
            # core after its request has already been registered.
            log_event(
                "state_observer_failed", request_id=context.request_id,
                component="executor", level="warning",
                error_type=type(error).__name__,
            )
    execution_history.record_started(context.request_id, request)

    log_event(
        "request_started", request_id=context.request_id, component="executor",
        source=source, input_preview=preview(request),
    )

    state.transition_to(ExecutionStatus.THINKING)
    jarvis_state.set_status(
        ExecutionStatus.THINKING, active_request_id=context.request_id,
        current_task=preview(request), plan_progress=_plan_progress(state),
    )

    messages = (
        _bounded_model_history(history)
        if history else [{"role": "user", "content": request}]
    )
    started = False

    # Phase 9 M4.2: a secondary copy of exactly the text chunks actually
    # yielded to the caller -- appended to in the same statement pair as
    # each real `yield` below, never buffering/delaying the real stream.
    # Because a generator can only ever be suspended (and therefore only
    # ever abandoned via GeneratorExit) at a yield point, and every
    # append happens immediately before its paired yield, this list holds
    # exactly the chunks already delivered to the caller at any moment
    # this generator might stop running -- normal completion, a handled
    # failure/cancellation/PartialToolExecution, or the caller closing/
    # abandoning the generator early. See _finalize_history_capture below
    # for where this gets persisted.
    captured_chunks = []

    # Phase 9 M4.2: exactly one finalize call site, run unconditionally
    # from the `finally:` block below -- deliberately NOT one call
    # scattered into each of the four terminal branches. A `finally`
    # attached to the `try:` around the whole fallback loop runs on every
    # way this generator can stop, not just an explicit `return`: a
    # normal return, an exception propagating out (including one no
    # `except` clause here catches), and GeneratorExit thrown in when the
    # caller closes/abandons the generator before any terminal branch
    # ever runs -- none of the `except PartialToolExecution`/`except
    # Exception` clauses below catch GeneratorExit (it's a BaseException,
    # not an Exception), so without this, an abandoned generator would
    # silently drop whatever was already yielded and leak its
    # request_id -> session_id bookkeeping in agent.history_capture
    # forever. `_history_finalized` guards strictly against a
    # theoretical double-call, since capture_assistant_turn() is the sole
    # place responsible for cleaning up that bookkeeping and must do so
    # exactly once per request.
    _history_finalized = False

    def _finalize_history_capture():
        nonlocal _history_finalized
        if _history_finalized:
            return
        _history_finalized = True
        history_capture.capture_assistant_turn(source, context.request_id, "".join(captured_chunks))

    # Phase 9 Milestone 2: task-aware, ordered, N-provider candidate list
    # replaces the original hardcoded two-tier Claude-then-OpenAI cascade
    # below -- same "try this candidate, on failure (without partial
    # output) move to the next" shape as before, just generalized from
    # exactly 2 hardcoded blocks to a loop over however many candidates
    # the router actually returned. With only Anthropic/OpenAI configured
    # (this project's original, and still the default, state),
    # build_fallback_chain() returns exactly [anthropic, openai] in that
    # order, so this loop's observable behavior is unchanged from before.
    task_requirements = classify_task(request, source=source)
    fallback_chain = build_fallback_chain(task_requirements)
    log_event(
        "model_route_selected", request_id=context.request_id, component="executor",
        task_type=task_requirements.task_type.value,
        chain=[f"{c.provider}:{c.model}" for c in fallback_chain],
    )

    try:
        for position, model_choice in enumerate(fallback_chain):
            is_last_candidate = position == len(fallback_chain) - 1

            if position > 0:
                log_event(
                    "model_fallback_started", request_id=context.request_id, component="executor",
                    provider=model_choice.provider, model=model_choice.model, fallback_position=position,
                )

            try:
                if model_choice.provider == "anthropic":
                    loop_iter = _run_claude_loop_stream(
                        messages, source=source, context=context, state=state,
                        model_choice=model_choice, task_type=task_requirements.task_type.value,
                        fallback_position=position,
                    )
                elif model_choice.provider == "perplexity":
                    loop_iter = _run_perplexity_agent_loop_stream(
                        model_choice, messages, source=source, context=context, state=state,
                        task_type=task_requirements.task_type.value, fallback_position=position,
                    )
                else:
                    loop_iter = _run_openai_compatible_loop_stream(
                        model_choice, messages, source=source, context=context, state=state,
                        task_type=task_requirements.task_type.value, fallback_position=position,
                    )

                for chunk in loop_iter:
                    started = True
                    captured_chunks.append(chunk)
                    yield chunk

                if position > 0:
                    log_event(
                        "model_fallback_succeeded", request_id=context.request_id, component="executor",
                        provider=model_choice.provider, fallback_position=position,
                    )

                if _is_cancelled(state):
                    state.finish(result="cancelled")
                    execution_history.record_cancelled(context.request_id, request, state, autonomy_level=context.autonomy_level)
                    log_event(
                        "request_cancelled", request_id=context.request_id, component="executor",
                        duration=time.time() - request_start,
                    )
                else:
                    state.finish(result="ok")
                    execution_history.record_completed(context.request_id, request, state, autonomy_level=context.autonomy_level)
                    log_event(
                        "request_completed", request_id=context.request_id, component="executor",
                        duration=time.time() - request_start, provider=model_choice.provider,
                    )
                return

            except PartialToolExecution:
                state.finish(failed=True, error="partial_tool_execution")
                execution_history.record_failed(context.request_id, request, state, autonomy_level=context.autonomy_level)
                log_event(
                    "request_failed", request_id=context.request_id, component="executor", level="error",
                    duration=time.time() - request_start, reason="partial_tool_execution",
                )
                captured_chunks.append(PARTIAL_EXECUTION_MESSAGE)
                yield PARTIAL_EXECUTION_MESSAGE
                return

            except Exception as error:
                if started:
                    # Can't retract text already shown, so don't risk
                    # splicing a second, unrelated fallback response onto it.
                    state.finish(failed=True, error="connection_dropped")
                    execution_history.record_failed(context.request_id, request, state, autonomy_level=context.autonomy_level)
                    log_event(
                        "request_failed", request_id=context.request_id, component="executor", level="error",
                        duration=time.time() - request_start, reason="connection_dropped", error_type=type(error).__name__,
                    )
                    connection_dropped_message = "\n\n[Connection dropped before finishing — please try again.]"
                    captured_chunks.append(connection_dropped_message)
                    yield connection_dropped_message
                    return

                log_event(
                    "primary_provider_failed" if position == 0 else "model_provider_failed",
                    request_id=context.request_id, component="executor",
                    level="warning", provider=model_choice.provider, error_type=type(error).__name__,
                )

                if is_last_candidate:
                    state.finish(failed=True, error=type(error).__name__)
                    execution_history.record_failed(context.request_id, request, state, autonomy_level=context.autonomy_level)
                    log_event(
                        "request_failed", request_id=context.request_id, component="executor", level="error",
                        duration=time.time() - request_start, error_type=type(error).__name__,
                    )
                    agent_error_message = f"Agent error: {error}"
                    captured_chunks.append(agent_error_message)
                    yield agent_error_message
                    return
                # else: fall through the for loop to the next candidate
    finally:
        _finalize_history_capture()
        unregister_active(context.request_id)
        clear_cancellation(context.request_id)
        jarvis_state.reset_to_idle()
        unbind_current_request_id(_request_id_token)


def execute_task(request, history=None, source="chat"):
    return "".join(execute_task_stream(request, history, source=source))


if __name__ == "__main__":

    request = input("What do you need help with? ")

    for chunk in execute_task_stream(request):
        print(chunk, end="", flush=True)
    print()
