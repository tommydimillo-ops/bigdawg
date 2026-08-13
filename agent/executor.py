import concurrent.futures
import json
import time

import tools.schemas  # noqa: F401 -- populates tools.registry as a side effect
from agent.audit import log_action
from agent.brain import build_system_prompt, TOOLS, client as claude_client
from agent.chat import openai_client
from agent.execution_state import ExecutionState
from agent.model_router import select as select_model
from agent.observability import log_event, preview
from agent.request_context import RequestContext
from config.settings import settings
from tools import registry

MAX_TOOL_ITERATIONS = settings.max_agent_steps


class PartialToolExecution(Exception):
    """Raised when a provider fails after already committing a side effect."""

OPENAI_TOOLS = registry.openai_schemas()


def _run_tool(name, tool_input, source="chat", context=None, state=None):
    """Every tool call funnels through here, so this is the one place that
    needs to log — individual tool functions don't need to know about it."""

    request_id = context.request_id if context else None

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

    log_event("tool_started", request_id=request_id, component="executor", tool=name)
    start = time.time()

    try:
        result = registry.dispatch(name, tool_input)
    except Exception as error:
        log_action(name, tool_input, f"ERROR: {error}")
        log_event(
            "tool_failed", request_id=request_id, component="executor", level="error",
            tool=name, duration=time.time() - start, error_type=type(error).__name__,
        )
        raise

    log_action(name, tool_input, result)
    log_event(
        "tool_completed", request_id=request_id, component="executor",
        tool=name, duration=time.time() - start, result_preview=preview(result),
    )
    if state:
        state.record_tool(name)
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
        results[call["id"]] = _run_tool(call["name"], call["input"], source, context, state)

    return results


def _run_claude_loop_stream(messages, source="chat", context=None, state=None):
    """Yields response text as it's generated (including any text a model
    emits alongside a tool call, e.g. "Let me check that..."), so the UI can
    show it immediately instead of waiting for the whole multi-step loop."""

    request_id = context.request_id if context else None
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
            "text": build_system_prompt(context.user_input if context else ""),
            "cache_control": {"type": "ephemeral"},
        }
    ]

    for _ in range(MAX_TOOL_ITERATIONS):

        if state:
            state.record_iteration()
        log_event(
            "agent_iteration", request_id=request_id, component="executor",
            iteration=state.iteration if state else None, provider="anthropic",
        )

        try:
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
        except Exception as error:
            log_event(
                "model_call_failed", request_id=request_id, component="executor",
                level="warning", provider="anthropic", error_type=type(error).__name__,
            )
            if committed:
                raise PartialToolExecution from error
            raise

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


def _run_openai_loop(messages, source="chat", context=None, state=None):

    request_id = context.request_id if context else None
    model_choice = select_model(attempt=1, context=context)
    if state:
        state.record_model(model_choice.provider, model_choice.model)
    log_event(
        "model_selected", request_id=request_id, component="executor",
        provider=model_choice.provider, model=model_choice.model,
    )

    messages = [{"role": "system", "content": build_system_prompt(context.user_input if context else "")}] + list(messages)
    committed = False

    for _ in range(MAX_TOOL_ITERATIONS):

        if state:
            state.record_iteration()
        log_event(
            "agent_iteration", request_id=request_id, component="executor",
            iteration=state.iteration if state else None, provider="openai",
        )

        try:
            response = openai_client.chat.completions.create(
                model=model_choice.model,
                messages=messages,
                tools=OPENAI_TOOLS,
            )
        except Exception as error:
            log_event(
                "model_call_failed", request_id=request_id, component="executor",
                level="warning", provider="openai", error_type=type(error).__name__,
            )
            if committed:
                raise PartialToolExecution from error
            raise

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


def _run_openai_loop_stream(messages, source="chat", context=None, state=None):
    # OpenAI is only an emergency fallback (Claude is unreachable), so it's
    # not worth the added complexity of accumulating streamed tool-call
    # deltas here — it just yields its one final answer at once.
    yield _run_openai_loop(messages, source=source, context=context, state=state)


PARTIAL_EXECUTION_MESSAGE = (
    "Something interrupted that request after it had already taken an "
    "action (like saving a reminder or a note) — please check before "
    "repeating it."
)


def execute_task_stream(request, history=None, source="chat"):

    context = RequestContext.create(request, source=source)
    state = ExecutionState(max_iterations=MAX_TOOL_ITERATIONS)
    request_start = time.time()

    log_event(
        "request_started", request_id=context.request_id, component="executor",
        source=source, input_preview=preview(request),
    )

    messages = list(history) if history else [{"role": "user", "content": request}]
    started = False

    try:
        for chunk in _run_claude_loop_stream(messages, source=source, context=context, state=state):
            started = True
            yield chunk
        state.finish(result="ok")
        log_event(
            "request_completed", request_id=context.request_id, component="executor",
            duration=time.time() - request_start, provider="anthropic",
        )
        return
    except PartialToolExecution:
        state.finish(failed=True, error="partial_tool_execution")
        log_event(
            "request_failed", request_id=context.request_id, component="executor", level="error",
            duration=time.time() - request_start, reason="partial_tool_execution",
        )
        yield PARTIAL_EXECUTION_MESSAGE
        return
    except Exception as error:
        if started:
            # Can't retract text already shown, so don't risk splicing a
            # second, unrelated fallback response onto it.
            state.finish(failed=True, error="connection_dropped")
            log_event(
                "request_failed", request_id=context.request_id, component="executor", level="error",
                duration=time.time() - request_start, reason="connection_dropped", error_type=type(error).__name__,
            )
            yield "\n\n[Connection dropped before finishing — please try again.]"
            return
        log_event(
            "primary_provider_failed", request_id=context.request_id, component="executor",
            level="warning", error_type=type(error).__name__,
        )

    try:
        yield from _run_openai_loop_stream(messages, source=source, context=context, state=state)
        state.finish(result="ok")
        log_event(
            "request_completed", request_id=context.request_id, component="executor",
            duration=time.time() - request_start, provider="openai",
        )
    except PartialToolExecution:
        state.finish(failed=True, error="partial_tool_execution")
        log_event(
            "request_failed", request_id=context.request_id, component="executor", level="error",
            duration=time.time() - request_start, reason="partial_tool_execution",
        )
        yield PARTIAL_EXECUTION_MESSAGE
    except Exception as error:
        state.finish(failed=True, error=type(error).__name__)
        log_event(
            "request_failed", request_id=context.request_id, component="executor", level="error",
            duration=time.time() - request_start, error_type=type(error).__name__,
        )
        yield f"Agent error: {error}"


def execute_task(request, history=None, source="chat"):
    return "".join(execute_task_stream(request, history, source=source))


if __name__ == "__main__":

    request = input("What do you need help with? ")

    for chunk in execute_task_stream(request):
        print(chunk, end="", flush=True)
    print()
