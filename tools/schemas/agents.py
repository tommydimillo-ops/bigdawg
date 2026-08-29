"""consult_coworker_agent -- the real execution entry point for Phase 7's
coworker agents (agent/agents/), reachable exactly like any other tool
the model chooses to call. This is deliberate: agent.executor.
execute_task_stream's own routing check (agent.agents.router.route(),
called unconditionally on every request) is a PURE, read-only decision
used only for attribution/observability -- it never executes an agent
itself, specifically so that call site can't make a real, unmocked
network/memory-write call from inside a function every existing test
already exercises. Real execution instead goes through this tool, which
means it's automatically just as safely mockable as every other tool in
tests (nothing runs unless the mocked model's response includes a
tool_use block naming it), and flows through the same permission/
autonomy/confirmation gate every other tool call already does -- see
Phase 7 section 13's explicit agent-must-go-through-the-tool-registry
requirement.
"""
from agent.agents.manager import execute_agent, execute_agents_parallel, get as get_agent
from agent.agents.models import AgentTaskRequest
from agent.execution_state import get_active
from agent.request_context import RequestContext, get_current_request_id
from config.settings import settings
from tools.registry import ToolSpec, register

_VALID_AGENTS = ("coding", "research", "qa", "memory")


def _consult_coworker_agent(tool_input: dict) -> str:
    agent_name = (tool_input.get("agent_name") or "").strip().lower()
    task = (tool_input.get("task") or "").strip()

    if agent_name not in _VALID_AGENTS:
        return f"Unknown agent '{agent_name}'. Valid agents: {', '.join(_VALID_AGENTS)}."
    if not task:
        return "A task description is required."

    agent = get_agent(agent_name)
    if agent is None:
        return f"Agent '{agent_name}' is not registered."
    if not agent.metadata.enabled:
        return f"Agent '{agent_name}' is currently disabled."

    # Phase 8 part 5: reuse the OUTER request's id (set by
    # agent.executor.execute_task_stream via a contextvar -- see
    # agent/request_context.py) instead of minting a fresh one, so this
    # agent's own logs correlate back to the request that triggered it.
    # Falls back to a fresh id only if this is somehow invoked outside
    # execute_task_stream's call stack at all (e.g. a direct test call).
    context = RequestContext.create(task, source="agent_tool", request_id=get_current_request_id())
    # Phase 8 part 4: execute_agent runs the agent in its own OS process
    # with a genuine, killable timeout (see agent/agents/manager.py's
    # execute_agent docstring) rather than calling agent.execute()
    # directly in-process, which had no timeout protection at all on this
    # -- the real, live -- execution path.
    result = execute_agent(agent_name, task, context)

    if not result.success:
        return f"{agent_name} agent could not complete this: {result.error or 'unknown error'}"
    if result.metadata.get("deferred_to_executor"):
        return (
            f"The {agent_name} agent doesn't handle this directly yet -- "
            "continue with the ordinary tools available to you."
        )
    return result.result


register(ToolSpec(
    name="consult_coworker_agent",
    description=(
        "Hand a task to a specialist coworker agent: 'research' (multi-step "
        "web research with source cross-checking), 'memory' (remember/recall "
        "a fact or preference), 'qa' (runs this project's own test suite for "
        "a \"do the tests still pass\" request; anything else defers back to "
        "your own ordinary tools), or 'coding' (real file edits plus test "
        "verification against this deployment's own repository -- ONLY on "
        "deployments where the operator has explicitly turned this on; "
        "where it's off, this defers back to your own ordinary tools "
        "instead, same as before -- try it for a genuine source-code change, "
        "it will tell you plainly if it isn't enabled here)."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "agent_name": {
                "type": "string",
                "enum": list(_VALID_AGENTS),
                "description": "Which coworker agent should handle this.",
            },
            "task": {
                "type": "string",
                "description": "The task description to hand to that agent.",
            },
        },
        "required": ["agent_name", "task"],
    },
    permission_level=1,
    side_effect=True,
    handler=_consult_coworker_agent,
))


def _format_batch_result(batch) -> str:
    lines = [f"Batch result: {batch.status.value} -- {batch.note}"]
    for item in batch.items:
        if item.result.cancelled:
            outcome = "CANCELLED"
        elif item.result.success:
            outcome = "OK"
        else:
            outcome = f"FAILED ({item.result.error or 'unknown error'})"
        retried_note = " [retried]" if item.retried else ""
        required_note = "" if item.required else " (optional)"
        lines.append(f"- {item.agent_name}{required_note} -- {item.task_preview}: {outcome}{retried_note}")
        if item.result.success and item.result.result:
            lines.append(f"  -> {item.result.result}")
    if batch.cost_usd is not None:
        lines.append(f"(batch cost: ${batch.cost_usd:.4f})")
    return "\n".join(lines)


def _delegate_parallel_tasks(tool_input: dict) -> str:
    raw_tasks = tool_input.get("tasks")
    if not isinstance(raw_tasks, list) or len(raw_tasks) < 2:
        return (
            "At least two independent subtasks are required for a parallel batch "
            "-- use consult_coworker_agent for a single task."
        )
    if len(raw_tasks) > settings.max_parallel_agents:
        return (
            f"Rejected: {len(raw_tasks)} subtasks requested, exceeds the "
            f"configured limit of {settings.max_parallel_agents}. Split this "
            "into smaller batches, or run the extra ones after this batch completes."
        )

    tasks = []
    for raw in raw_tasks:
        agent_name = (raw.get("agent_name") or "").strip().lower()
        task = (raw.get("task") or "").strip()
        if agent_name not in _VALID_AGENTS:
            return f"Unknown agent '{agent_name}'. Valid agents: {', '.join(_VALID_AGENTS)}."
        if not task:
            return "Every subtask needs a task description."
        tasks.append(AgentTaskRequest(agent_name=agent_name, task=task, required=bool(raw.get("required", True))))

    context = RequestContext.create(
        f"parallel batch of {len(tasks)} subtasks", source="agent_tool",
        request_id=get_current_request_id(),
    )

    # Phase 9 Milestone 3: mirrors consult_coworker_agent's own contextvar-
    # recovery pattern (get_current_request_id) to reach the live
    # ExecutionState without widening this handler's Callable[[dict], str]
    # signature -- see CLAUDE.md's note on this exact convention.
    state = get_active(context.request_id) if context.request_id else None
    if state is not None:
        state.record_agent_batch_started([t.agent_name for t in tasks])

    batch = execute_agents_parallel(tasks, context)

    if state is not None:
        completed = [item.agent_name for item in batch.items if item.result.success]
        failed = [item.agent_name for item in batch.items if not item.result.success]
        state.record_agent_batch_finished(completed, failed, batch.status.value)

    return _format_batch_result(batch)


register(ToolSpec(
    name="delegate_parallel_tasks",
    description=(
        "Run 2 or more coworker-agent subtasks CONCURRENTLY, for genuinely "
        "independent work only (e.g. researching two unrelated topics, or "
        "research + a memory lookup) -- never for subtasks where one "
        "depends on another's outcome (e.g. edit-then-test, or two agents "
        "writing to the same thing), which must stay sequential "
        f"consult_coworker_agent calls instead. Bounded to at most "
        f"{settings.max_parallel_agents} subtasks per batch; each subtask "
        "still runs in its own isolated process with its own timeout. "
        "Returns one combined result showing which subtasks succeeded, "
        "which failed, and the batch's overall status."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "tasks": {
                "type": "array",
                "minItems": 2,
                "maxItems": settings.max_parallel_agents,
                "items": {
                    "type": "object",
                    "properties": {
                        "agent_name": {
                            "type": "string",
                            "enum": list(_VALID_AGENTS),
                            "description": "Which coworker agent should handle this subtask.",
                        },
                        "task": {
                            "type": "string",
                            "description": "The task description for this subtask.",
                        },
                        "required": {
                            "type": "boolean",
                            "description": (
                                "Whether the whole batch should be treated as failed if "
                                "this specific subtask fails. Defaults to true."
                            ),
                        },
                    },
                    "required": ["agent_name", "task"],
                },
                "description": "The independent subtasks to run concurrently.",
            },
        },
        "required": ["tasks"],
    },
    permission_level=1,
    side_effect=True,
    # Deliberately NOT parallel_safe: this tool already bounds its OWN
    # internal concurrency to max_parallel_agents. Marking it parallel_safe
    # at the registry level would let the model call it more than once in
    # the same turn and have tools.registry's own concurrent-tool-call
    # mechanism (agent/executor.py's _run_tool_batch) run several such
    # batches at once -- multiplying the real concurrent-subprocess count
    # past the configured ceiling this whole milestone exists to enforce.
    handler=_delegate_parallel_tasks,
))
