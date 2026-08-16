"""A specialist sub-agent for multi-step research tasks — the "agents as
tools" pattern: invoked as a single tool call from the main coordinator,
but internally runs its own focused loop with its own system prompt and
tools, so it can chain several searches and page-reads to cross-check
information without burning the coordinator's own MAX_TOOL_ITERATIONS on
each intermediate step. This only adds an extra round of model calls when
the coordinator actually decides a task needs it — simple requests never
touch this module.

Phase 9 Milestone 3: research() now routes through the SAME deterministic
classify()/build_fallback_chain() primitives agent/executor.py uses for
the outer request, instead of always calling Anthropic directly. This
does NOT call execute_task_stream, route_and_execute, or execute_agent --
it's a plain model API call, exactly the same kind of call
agent/executor.py's own loops make, just from inside this module instead
of that one. No agent-to-agent delegation happens here, so
MAX_AGENT_DEPTH is untouched.

Three provider shapes are handled, mirroring agent/executor.py's own
three-way split (_run_claude_loop_stream / _run_openai_compatible_loop /
_run_perplexity_agent_loop) but narrowed to research's own 2-tool set
(open_browser, read_document) and made non-streaming, since this
function's caller (agent/agents/research.py's ResearchAgent) wants one
synthesized string back, not a stream:
  - anthropic: this module's original tool-calling loop, parameterized
    by the router's chosen model instead of a hardcoded one.
  - openai/xai ("OpenAI-API-compatible"): a new loop using the
    OpenAI-function-call tool shape, mirroring
    agent/executor.py's _run_openai_compatible_loop.
  - perplexity: a single grounded Agent API call, no tool loop at all --
    Perplexity already does its own multi-source research server-side,
    and (per this project's standing rule, see agent/executor.py's own
    _run_perplexity_agent_loop docstring) is never handed a tool
    registry, so a loop wouldn't have anything to call anyway.

_call_perplexity_agent/_extract_agent_api_text/_client_for_provider are
deliberately small, LOCAL re-implementations of the same-named functions
in agent/executor.py, not imports from there: agent/executor.py already
imports agent.agents (to populate the coworker-agent registry at import
time), and agent.agents.research imports THIS module -- importing
agent.executor from here would create an import cycle
(executor -> agent.agents -> agent.agents.research -> research_agent ->
executor). Each copy is a few lines and has no shared mutable state, so
duplicating them is safer here than restructuring a working, tested
subsystem just to share them.

On a failure from one provider (a raised exception, before any partial
tool-loop state is discarded), research() tries the next candidate in
the router's fallback chain from a clean slate. This is safe specifically
because research's own tools (open_browser, read_document) are read-only
-- unlike agent/executor.py's PartialToolExecution caution around
side-effecting tools, there's no real-world action here that a retry
could double up on.
"""

import json
import time

import httpx

from agent.audit import log_action
from agent.chat import anthropic_client, openai_client, xai_client
from agent.model_router import build_fallback_chain
from agent.observability import log_event
from agent import provider_health
from agent.request_context import get_current_request_id
from agent.secrets import get_secret
from agent.task_classifier import classify as classify_task
from agent.usage import check_request_limits, record_llm_usage
from config.settings import settings
from documents.reader import read_document
from tools.browser import open_and_read

# Phase 8 part 3: configurable via settings.max_agent_iterations rather
# than a hardcoded constant, so a single safety limit governs every
# coworker agent's own internal loop, not one hand-tuned number per
# agent.
MAX_ITERATIONS = settings.max_agent_iterations

SYSTEM_PROMPT = (
    "You are a focused research assistant. Given a research question, use "
    "open_browser to search and read real pages — open multiple sources, "
    "cross-check facts across them, and don't just report the first result "
    "you find. When you have enough information, write a clear, "
    "well-organized answer that mentions which sources/sites it came from. "
    "Be thorough but efficient — don't open more pages than necessary to "
    "answer confidently."
)

TOOLS = [
    {
        "name": "open_browser",
        "description": (
            "Open a real browser window and navigate to a destination or "
            "search query, returning the page's visible text."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "target": {"type": "string", "description": "A URL or search query."}
            },
            "required": ["target"],
        },
    },
    {
        "name": "read_document",
        "description": "Read the text content of a local file (PDF, .txt, .md).",
        "input_schema": {
            "type": "object",
            "properties": {
                "file_path": {"type": "string", "description": "Path to the document."}
            },
            "required": ["file_path"],
        },
    },
]

# Same 2 tools as TOOLS above, in the OpenAI/xAI function-call shape
# instead of Anthropic's -- see module docstring for why this is hand-
# written rather than a generic converter (only 2 fixed tools, not worth
# building tools.registry-style conversion machinery for).
OPENAI_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "open_browser",
            "description": (
                "Open a real browser window and navigate to a destination or "
                "search query, returning the page's visible text."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "target": {"type": "string", "description": "A URL or search query."}
                },
                "required": ["target"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_document",
            "description": "Read the text content of a local file (PDF, .txt, .md).",
            "parameters": {
                "type": "object",
                "properties": {
                    "file_path": {"type": "string", "description": "Path to the document."}
                },
                "required": ["file_path"],
            },
        },
    },
]

PERPLEXITY_AGENT_API_URL = "https://api.perplexity.ai/v1/agent"


def _run_tool(name, tool_input):
    if name == "open_browser":
        result = open_and_read(tool_input["target"])
    elif name == "read_document":
        result = read_document(tool_input["file_path"])
    else:
        result = f"Unknown tool: {name}"

    # Log every page this sub-agent visits, not just the fact that it ran
    # — otherwise the audit trail shows "research_agent was called" with
    # no record of what it actually did while it was in there.
    log_action(f"research_agent:{name}", tool_input, result)
    return result


def _client_for_provider(provider):
    return {"openai": openai_client, "xai": xai_client}.get(provider)


def _extract_agent_api_text(data):
    for item in data.get("output", []) or []:
        if item.get("type") == "message":
            for block in item.get("content", []) or []:
                if block.get("type") == "output_text":
                    return block.get("text", "")
    return ""


def _call_perplexity_agent(preset, input_text, instructions):
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


def _run_claude_research_loop(model, question, request_id, task_type, fallback_position):
    messages = [{"role": "user", "content": question}]

    for _ in range(MAX_ITERATIONS):
        limit_check = check_request_limits(request_id)
        if limit_check.exceeded:
            return f"Stopping the research here — {limit_check.reason}."

        _call_started = time.time()
        try:
            response = anthropic_client.messages.create(
                model=model,
                # Same headroom fix as agent/executor.py — adaptive thinking
                # tokens count against this budget, and a low ceiling can get
                # entirely consumed by thinking alone on a hard question.
                max_tokens=4096,
                system=SYSTEM_PROMPT,
                tools=TOOLS,
                messages=messages,
            )
        except Exception:
            provider_health.record_failure("anthropic")
            raise
        provider_health.clear_failure("anthropic")

        usage = getattr(response, "usage", None)
        if usage is not None:
            record_llm_usage(
                provider="anthropic", model=model, operation="research",
                request_id=request_id, agent="research",
                input_tokens=getattr(usage, "input_tokens", 0), output_tokens=getattr(usage, "output_tokens", 0),
                duration_seconds=time.time() - _call_started,
                task_type=task_type, fallback_position=fallback_position,
            )

        if response.stop_reason != "tool_use":
            text = "".join(block.text for block in response.content if block.type == "text").strip()
            return text or "I couldn't find a clear answer to that."

        messages.append({"role": "assistant", "content": response.content})

        tool_results = []
        for block in response.content:
            if block.type == "tool_use":
                result = _run_tool(block.name, block.input)
                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": str(result),
                })

        messages.append({"role": "user", "content": tool_results})

    return (
        "The research took more steps than expected — here's what I found "
        "so far, though it may be incomplete."
    )


def _run_openai_compatible_research_loop(provider, model, question, request_id, task_type, fallback_position):
    client = _client_for_provider(provider)
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": question},
    ]

    for _ in range(MAX_ITERATIONS):
        limit_check = check_request_limits(request_id)
        if limit_check.exceeded:
            return f"Stopping the research here — {limit_check.reason}."

        _call_started = time.time()
        try:
            response = client.chat.completions.create(model=model, messages=messages, tools=OPENAI_TOOLS)
        except Exception:
            provider_health.record_failure(provider)
            raise
        provider_health.clear_failure(provider)

        usage = getattr(response, "usage", None)
        if usage is not None:
            record_llm_usage(
                provider=provider, model=model, operation="research",
                request_id=request_id, agent="research",
                input_tokens=getattr(usage, "prompt_tokens", 0), output_tokens=getattr(usage, "completion_tokens", 0),
                duration_seconds=time.time() - _call_started,
                task_type=task_type, fallback_position=fallback_position,
            )

        choice = response.choices[0].message
        if not choice.tool_calls:
            return choice.content or "I couldn't find a clear answer to that."

        messages.append(choice.model_dump(exclude_unset=True))

        for call in choice.tool_calls:
            result = _run_tool(call.function.name, json.loads(call.function.arguments or "{}"))
            messages.append({"role": "tool", "tool_call_id": call.id, "content": str(result)})

    return (
        "The research took more steps than expected — here's what I found "
        "so far, though it may be incomplete."
    )


def _run_perplexity_research(model, question, request_id, task_type, fallback_position):
    limit_check = check_request_limits(request_id)
    if limit_check.exceeded:
        return f"Stopping the research here — {limit_check.reason}."

    _call_started = time.time()
    try:
        data = _call_perplexity_agent(model, question, SYSTEM_PROMPT)
    except Exception:
        provider_health.record_failure("perplexity")
        raise
    provider_health.clear_failure("perplexity")

    usage = data.get("usage") or {}
    if usage:
        record_llm_usage(
            provider="perplexity", model=model, operation="research",
            request_id=request_id, agent="research",
            input_tokens=usage.get("input_tokens", 0), output_tokens=usage.get("output_tokens", 0),
            duration_seconds=time.time() - _call_started,
            task_type=task_type, fallback_position=fallback_position,
        )

    return _extract_agent_api_text(data) or "I couldn't find a grounded answer to that."


def research(question):
    """Runs a self-contained research loop and returns a synthesized
    answer as plain text — not streamed, since this is a sub-call made
    from inside the main agent's own tool loop. Which provider answers
    is chosen the same deterministic way the outer request's provider is
    (agent/task_classifier.py + agent/model_router.py's
    build_fallback_chain) -- capability/health/budget-filtered, cost-
    ranked, one provider called at a time, falling through to the next
    candidate only on failure. With only Anthropic configured (this
    project's original, still-default state), the chain is just
    [anthropic], so behavior is unchanged from before this milestone in
    that common case."""

    request_id = get_current_request_id()
    requirements = classify_task(question, source="agent")
    fallback_chain = build_fallback_chain(requirements)

    last_error = None
    for position, model_choice in enumerate(fallback_chain):
        if position > 0:
            log_event(
                "model_fallback_started", request_id=request_id, component="research_agent",
                provider=model_choice.provider, model=model_choice.model, fallback_position=position,
            )
        log_event(
            "model_selected", request_id=request_id, component="research_agent",
            provider=model_choice.provider, model=model_choice.model, fallback_position=position,
        )
        try:
            if model_choice.provider == "anthropic":
                return _run_claude_research_loop(
                    model_choice.model, question, request_id, requirements.task_type.value, position,
                )
            elif model_choice.provider == "perplexity":
                return _run_perplexity_research(
                    model_choice.model, question, request_id, requirements.task_type.value, position,
                )
            else:
                return _run_openai_compatible_research_loop(
                    model_choice.provider, model_choice.model, question, request_id,
                    requirements.task_type.value, position,
                )
        except Exception as error:
            last_error = error
            log_event(
                "model_call_failed", request_id=request_id, component="research_agent",
                level="warning", provider=model_choice.provider, error_type=type(error).__name__,
            )
            continue

    return (
        f"I couldn't complete this research right now "
        f"({type(last_error).__name__ if last_error else 'no provider available'})."
    )


if __name__ == "__main__":
    import sys
    print(research(sys.argv[1] if len(sys.argv) > 1 else "What is the Python GIL?"))
