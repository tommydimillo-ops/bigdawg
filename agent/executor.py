import concurrent.futures
import json

import tools.schemas  # noqa: F401 -- populates tools.registry as a side effect
from agent.audit import log_action
from agent.brain import build_system_prompt, TOOLS, client as claude_client
from agent.chat import openai_client
from tools import registry

MAX_TOOL_ITERATIONS = 8


class PartialToolExecution(Exception):
    """Raised when a provider fails after already committing a side effect."""

OPENAI_TOOLS = registry.openai_schemas()


def _run_tool(name, tool_input, source="chat"):
    """Every tool call funnels through here, so this is the one place that
    needs to log — individual tool functions don't need to know about it."""

    if source == "scheduled" and registry.requires_live_confirmation(name):
        result = (
            f"Skipped: {name} requires a live conversation with the user "
            "present to confirm — it can't run from a scheduled task."
        )
        log_action(name, tool_input, result)
        return result

    if source == "scheduled" and not registry.unattended_allowed(name):
        result = f"Skipped: {name} can't run unattended from a scheduled task."
        log_action(name, tool_input, result)
        return result

    try:
        result = registry.dispatch(name, tool_input)
    except Exception as error:
        log_action(name, tool_input, f"ERROR: {error}")
        raise

    log_action(name, tool_input, result)
    return result


def _text_from(content_blocks):
    return "".join(block.text for block in content_blocks if block.type == "text").strip()


def _run_tool_batch(tool_calls, source="chat"):
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
                executor.submit(_run_tool, call["name"], call["input"], source): call["id"]
                for call in parallel_calls
            }
            for future in concurrent.futures.as_completed(futures):
                results[futures[future]] = future.result()

    for call in sequential_calls:
        results[call["id"]] = _run_tool(call["name"], call["input"], source)

    return results


def _run_claude_loop_stream(messages, source="chat"):
    """Yields response text as it's generated (including any text a model
    emits alongside a tool call, e.g. "Let me check that..."), so the UI can
    show it immediately instead of waiting for the whole multi-step loop."""

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
            "text": build_system_prompt(),
            "cache_control": {"type": "ephemeral"},
        }
    ]

    for _ in range(MAX_TOOL_ITERATIONS):

        try:
            with claude_client.messages.stream(
                model="claude-sonnet-5",
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
            if committed:
                raise PartialToolExecution from error
            raise

        if response.stop_reason != "tool_use":
            if not yielded_any:
                yield "I'm not sure how to respond to that."
            return

        messages.append({"role": "assistant", "content": response.content})

        tool_use_blocks = [block for block in response.content if block.type == "tool_use"]
        results_by_id = _run_tool_batch(
            [{"id": b.id, "name": b.name, "input": b.input} for b in tool_use_blocks],
            source=source,
        )

        if any(block.name in registry.side_effect_tools() for block in tool_use_blocks):
            committed = True

        tool_results = [
            {"type": "tool_result", "tool_use_id": block.id, "content": str(results_by_id[block.id])}
            for block in tool_use_blocks
        ]

        messages.append({"role": "user", "content": tool_results})

    yield "That took more steps than expected — could you rephrase your request?"


def _run_openai_loop(messages, source="chat"):

    messages = [{"role": "system", "content": build_system_prompt()}] + list(messages)
    committed = False

    for _ in range(MAX_TOOL_ITERATIONS):

        try:
            response = openai_client.chat.completions.create(
                model="gpt-5",
                messages=messages,
                tools=OPENAI_TOOLS,
            )
        except Exception as error:
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
        results_by_id = _run_tool_batch(tool_calls, source=source)

        if any(call["name"] in registry.side_effect_tools() for call in tool_calls):
            committed = True

        for call in tool_calls:
            messages.append({
                "role": "tool",
                "tool_call_id": call["id"],
                "content": str(results_by_id[call["id"]]),
            })

    return "That took more steps than expected — could you rephrase your request?"


def _run_openai_loop_stream(messages, source="chat"):
    # OpenAI is only an emergency fallback (Claude is unreachable), so it's
    # not worth the added complexity of accumulating streamed tool-call
    # deltas here — it just yields its one final answer at once.
    yield _run_openai_loop(messages, source=source)


PARTIAL_EXECUTION_MESSAGE = (
    "Something interrupted that request after it had already taken an "
    "action (like saving a reminder or a note) — please check before "
    "repeating it."
)


def execute_task_stream(request, history=None, source="chat"):

    messages = list(history) if history else [{"role": "user", "content": request}]
    started = False

    try:
        for chunk in _run_claude_loop_stream(messages, source=source):
            started = True
            yield chunk
        return
    except PartialToolExecution:
        yield PARTIAL_EXECUTION_MESSAGE
        return
    except Exception:
        if started:
            # Can't retract text already shown, so don't risk splicing a
            # second, unrelated fallback response onto it.
            yield "\n\n[Connection dropped before finishing — please try again.]"
            return

    try:
        yield from _run_openai_loop_stream(messages, source=source)
    except PartialToolExecution:
        yield PARTIAL_EXECUTION_MESSAGE
    except Exception as error:
        yield f"Agent error: {error}"


def execute_task(request, history=None, source="chat"):
    return "".join(execute_task_stream(request, history, source=source))


if __name__ == "__main__":

    request = input("What do you need help with? ")

    for chunk in execute_task_stream(request):
        print(chunk, end="", flush=True)
    print()
