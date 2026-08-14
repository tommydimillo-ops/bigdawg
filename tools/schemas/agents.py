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
from agent.agents.manager import execute_agent, get as get_agent
from agent.request_context import RequestContext, get_current_request_id
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
        "web research with source cross-checking), or 'memory' (remember/"
        "recall a fact or preference). 'coding' and 'qa' are registered but "
        "don't execute anything yet this phase -- use the ordinary tools "
        "available to you for coding/testing tasks instead of this one."
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
