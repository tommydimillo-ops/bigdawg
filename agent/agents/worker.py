"""Phase 8 part 4 -- runs a single coworker-agent execution in its own OS
process. Invoked as `python -m agent.agents.worker` by agent/agents/
manager.py's execute_agent(), which is what tools/schemas/agents.py's
consult_coworker_agent tool actually calls for every real agent
invocation.

This exists so the timeout guarding agent execution can be a genuine,
OS-level kill: subprocess.run(..., timeout=...) sends SIGKILL to the
child if it doesn't finish in time, and the child (this whole process,
including anything it's doing -- network calls, browser automation,
eventually real computer/code work) dies with it. The ThreadPoolExecutor-
based timeout this replaces could only stop the CALLER from waiting on a
future -- the underlying thread kept running in the background
regardless, indefinitely, which is not acceptable once a coworker agent
does real computer/code work.

Input is a single JSON object read from stdin (not argv), so a task's
free text -- arbitrary characters, quotes, newlines -- never has to
survive shell/argv quoting:
    {"agent_name": ..., "task": ..., "request_id": ..., "autonomy_level": ...}
Output is a single JSON object written to stdout: either the executing
agent's AgentResult (as a dict), or {"error": "..."} if the agent name is
unknown -- both cases the parent (manager.execute_agent) turns into a
failed AgentResult rather than crashing. Nothing else in this project
writes to stdout during agent execution (structured logging goes to
stderr -- see agent/observability.py), so stdout stays exclusively this
one JSON line.
"""
import dataclasses
import json
import sys


def main() -> None:
    payload = json.loads(sys.stdin.read())

    # Imported here, not at module load -- this file is invoked as a
    # standalone subprocess purely to run one agent, so nothing above
    # should pay for this project's much larger import graph before the
    # payload itself is even known to be valid JSON.
    import agent.agents  # noqa: F401 -- populates the registry as a side effect
    from agent.agents import manager
    from agent.request_context import RequestContext, bind_current_request_id

    agent_name = payload["agent_name"]
    task = payload["task"]
    request_id = payload.get("request_id")

    if request_id:
        bind_current_request_id(request_id)

    context = RequestContext.create(
        task, source="agent_worker", request_id=request_id,
    )
    if payload.get("autonomy_level") is not None:
        context.autonomy_level = payload["autonomy_level"]

    coworker = manager.get(agent_name)
    if coworker is None:
        sys.stdout.write(json.dumps({"error": f"Agent '{agent_name}' is not registered."}))
        return

    result = coworker.execute(task, context)
    sys.stdout.write(json.dumps(dataclasses.asdict(result)))


if __name__ == "__main__":
    main()
