import concurrent.futures
import json
import os

from agent.audit import log_action, recent_actions_text
from agent.brain import build_system_prompt, TOOLS, client as claude_client
from agent.chat import openai_client
from agent.deep_reasoning import deep_reason
from agent.patterns import forget_pattern, list_patterns, note_pattern
from agent.lessons import learn_rule, list_rules
from agent.memory_agent import recall, remember
from agent.research_agent import research
from agent.scheduled_tasks import add_task, list_tasks, remove_task
from documents.reader import read_document
from tools.agenda import list_upcoming
from tools.autofill import confirm_login, fill_login
from tools.browser import click_on_page, open_and_read, search_on_page
from tools.calendar import add_calendar_event
from tools.clipboard import get_clipboard, set_clipboard
from tools.computer import open_application
from tools.computer_use import (
    computer_click,
    computer_confirm_action,
    computer_locate,
    computer_press_key,
    computer_see,
    computer_type,
)
from tools.files import open_file, search_files
from tools.messaging import draft_email, send_email
from tools.music import control_music
from tools.notes import create_note
from tools.reminders import add_reminder
from tools.sandbox_python import run_python
from tools.screen import take_screenshot
from tools.system_control import lock_screen, sleep_mac
from tools.system_status import get_system_status
from tools.timer import set_timer
from tools.vision import analyze_image
from tools.weather import get_weather, get_weekly_forecast

MAX_TOOL_ITERATIONS = 8

# Tools with a real-world side effect. If a provider fails partway through a
# loop after already running one of these, restarting the whole request on
# the other provider would risk repeating the same action (e.g. a duplicate
# reminder), so that case is reported instead of silently retried.
SIDE_EFFECT_TOOLS = {
    "remember_fact", "add_reminder", "add_calendar_event", "create_note",
    "learn_rule", "confirm_login", "schedule_task", "cancel_scheduled_task",
    "draft_email", "send_email",
    "computer_click", "computer_type", "computer_press_key", "computer_confirm_action",
}

# Tools verified safe to run concurrently with each other: pure reads with
# no shared mutable state to race on. This is deliberately a hand-checked
# allowlist rather than derived from permission level — permission level
# tracks external-facing risk, not internal thread-safety, and those don't
# always line up (e.g. fill_login is a harmless L0 preview but still
# mutates shared module state, so it's excluded here even though it'd
# qualify by permission level alone). Anything not on this list runs
# sequentially: the browser tools share one live page (concurrent
# navigation would race), and memory/scheduling writes are a
# read-modify-write over a shared file (concurrent writers could silently
# drop each other's update).
PARALLEL_SAFE_TOOLS = {
    "read_document", "list_upcoming", "get_system_status", "search_files",
    "recall_facts", "list_rules", "view_recent_actions", "list_scheduled_tasks",
    "get_weather", "get_weekly_forecast", "get_clipboard",
}


class PartialToolExecution(Exception):
    """Raised when a provider fails after already committing a side effect."""

OPENAI_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": tool["name"],
            "description": tool["description"],
            "parameters": tool["input_schema"],
        },
    }
    for tool in TOOLS
]


# Tools that finalize an already-previewed action a human explicitly
# approved. A scheduled/unattended run has no live person to actually say
# yes, so letting any of these fire from one would silently defeat the
# whole point of the confirmation gate they sit behind. Hard block, not a
# prompt suggestion the model could talk itself out of.
REQUIRES_LIVE_CONFIRMATION = {"confirm_login", "send_email", "send_message"}

# The whole computer_* family is blocked from unattended runs, not just
# the gated confirm step above -- even the "non-consequential" ones
# (clicking around, typing) mean real, unsupervised control of whatever
# happens to be on screen while no one's watching, which is a different
# risk than an ordinary background tool call.
NO_UNATTENDED_EXECUTION = {
    "computer_see", "computer_locate", "computer_click",
    "computer_type", "computer_press_key", "computer_confirm_action",
}


def _run_tool(name, tool_input, source="chat"):
    """Every tool call funnels through here, so this is the one place that
    needs to log — individual tool functions don't need to know about it."""

    if source == "scheduled" and name in REQUIRES_LIVE_CONFIRMATION:
        result = (
            f"Skipped: {name} requires a live conversation with the user "
            "present to confirm — it can't run from a scheduled task."
        )
        log_action(name, tool_input, result)
        return result

    if source == "scheduled" and name in NO_UNATTENDED_EXECUTION:
        result = f"Skipped: {name} can't run unattended from a scheduled task."
        log_action(name, tool_input, result)
        return result

    try:
        result = _dispatch_tool(name, tool_input)
    except Exception as error:
        log_action(name, tool_input, f"ERROR: {error}")
        raise

    log_action(name, tool_input, result)
    return result


def _dispatch_tool(name, tool_input):

    if name == "take_screenshot":
        image = take_screenshot()
        try:
            return analyze_image(image)
        finally:
            os.remove(image)

    if name == "open_browser":
        return open_and_read(tool_input["target"])

    if name == "search_on_page":
        return search_on_page(tool_input["query"])

    if name == "click_on_page":
        return click_on_page(tool_input["text"])

    if name == "open_application":
        app_name = tool_input["app_name"]
        result = open_application(app_name)
        if result.startswith("Could not open"):
            return open_and_read(app_name)
        return result

    if name == "remember_fact":
        return remember("notes", tool_input["fact"])

    if name == "recall_facts":
        return recall("notes")

    if name == "read_document":
        return read_document(tool_input["file_path"])

    if name == "fill_login":
        return fill_login(tool_input["site"])

    if name == "confirm_login":
        return confirm_login(tool_input["site"])

    if name == "add_reminder":
        return add_reminder(tool_input["title"], tool_input.get("due_date"))

    if name == "add_calendar_event":
        return add_calendar_event(
            tool_input["title"],
            tool_input["start_date"],
            tool_input.get("end_date"),
        )

    if name == "list_upcoming":
        return list_upcoming(tool_input.get("days", 7))

    if name == "create_note":
        return create_note(tool_input["title"], tool_input.get("body", ""))

    if name == "control_music":
        return control_music(tool_input["action"], tool_input.get("query"))

    if name == "get_system_status":
        return get_system_status()

    if name == "get_weather":
        return get_weather(tool_input.get("location"))

    if name == "get_weekly_forecast":
        return get_weekly_forecast(tool_input.get("location"))

    if name == "search_files":
        return search_files(tool_input["query"])

    if name == "learn_rule":
        return learn_rule(tool_input["rule"])

    if name == "list_rules":
        return list_rules()

    if name == "run_python":
        return run_python(tool_input["code"])

    if name == "view_recent_actions":
        return recent_actions_text(tool_input.get("limit", 20))

    if name == "schedule_task":
        task, error = add_task(tool_input["prompt"], tool_input["time_of_day"])
        if error:
            return f"Could not schedule: {error}"
        return (
            f"Scheduled (id {task['id']}): \"{task['prompt']}\" daily at "
            f"{task['time_of_day']}. This only fires if the scheduler "
            "process is running (`python -m agent.scheduler_daemon`)."
        )

    if name == "list_scheduled_tasks":
        tasks = list_tasks()
        if not tasks:
            return "No scheduled tasks."
        return "\n".join(
            f"[{t['id']}] {t['time_of_day']} daily — \"{t['prompt']}\" "
            f"({'enabled' if t.get('enabled', True) else 'disabled'}, "
            f"last ran: {t.get('last_run_date') or 'never'})"
            for t in tasks
        )

    if name == "cancel_scheduled_task":
        removed = remove_task(tool_input["task_id"])
        return f"Cancelled task {tool_input['task_id']}." if removed else f"No task with id {tool_input['task_id']}."

    if name == "research_agent":
        return research(tool_input["question"])

    if name == "draft_email":
        return draft_email(tool_input["to"], tool_input["subject"], tool_input["body"])

    if name == "send_email":
        return send_email(tool_input["to"])

    if name == "deep_reason":
        return deep_reason(tool_input["question"])

    if name == "note_pattern":
        return note_pattern(tool_input["observation"])

    if name == "list_patterns":
        return list_patterns()

    if name == "forget_pattern":
        return forget_pattern(tool_input["text"])

    if name == "lock_screen":
        return lock_screen()

    if name == "sleep_mac":
        return sleep_mac()

    if name == "get_clipboard":
        return get_clipboard()

    if name == "set_clipboard":
        return set_clipboard(tool_input["text"])

    if name == "set_timer":
        return set_timer(tool_input["minutes"], tool_input.get("label", "Time's up"))

    if name == "open_file":
        return open_file(tool_input["path"])

    if name == "computer_see":
        return computer_see()

    if name == "computer_locate":
        return computer_locate(tool_input["description"])

    if name == "computer_click":
        return computer_click(tool_input["x"], tool_input["y"])

    if name == "computer_type":
        return computer_type(tool_input["text"])

    if name == "computer_press_key":
        return computer_press_key(tool_input["key"])

    if name == "computer_confirm_action":
        return computer_confirm_action(
            tool_input["description"],
            tool_input.get("x"),
            tool_input.get("y"),
            tool_input.get("key"),
        )

    return f"Unknown tool: {name}"


def _text_from(content_blocks):
    return "".join(block.text for block in content_blocks if block.type == "text").strip()


def _run_tool_batch(tool_calls, source="chat"):
    """Runs every tool_use block from a single model response. When the
    model asks for several independent lookups in one turn (e.g. "check my
    battery and search my files"), running them one at a time is wasted
    wall-clock time — this runs the ones verified safe for concurrency in
    parallel and leaves everything else sequential. Returns a dict of
    tool_use_id -> result, in no particular order (callers match by id)."""

    parallel_calls = [call for call in tool_calls if call["name"] in PARALLEL_SAFE_TOOLS]
    sequential_calls = [call for call in tool_calls if call["name"] not in PARALLEL_SAFE_TOOLS]

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

        if any(block.name in SIDE_EFFECT_TOOLS for block in tool_use_blocks):
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

        if any(call["name"] in SIDE_EFFECT_TOOLS for call in tool_calls):
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
