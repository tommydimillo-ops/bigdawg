"""Visibility into, and control over, Jarvis's own execution -- distinct
from every other tool here in that these act on Jarvis's request-handling
itself, not the world outside it. view_task_history answers "what have
you been doing" (active requests plus recent history); cancel_request
lets a live request stop a DIFFERENT still-running one (e.g. a scheduled
task in progress) -- it can never affect a request in another Jarvis
process/session, since agent.cancellation only ever looks at the current
process's active-execution registry.
"""
from datetime import datetime

from agent.cancellation import request_cancel
from agent.execution_history import get_active, get_recent
from tools.registry import ToolSpec, register


def _format_active(states) -> str:
    if not states:
        return "No requests are currently active."
    lines = []
    for state in states:
        tools = ", ".join(state.tools_executed) or "none yet"
        lines.append(
            f"- {state.request_id}: {state.status.value} "
            f"(iteration {state.iteration}/{state.max_iterations}, tools used: {tools})"
        )
    return "\n".join(lines)


def _format_recent(records) -> str:
    if not records:
        return "No past executions recorded yet."
    lines = []
    for record in records:
        when = datetime.fromtimestamp(record.timestamp).strftime("%Y-%m-%d %H:%M")
        duration = f"{record.duration_seconds:.1f}s" if record.duration_seconds is not None else "unknown duration"
        tools = f" ({', '.join(record.tools_used)})" if record.tools_used else ""
        lines.append(
            f'- [{when}] "{record.request_summary}" -- {record.status}, {duration}, '
            f"{record.tool_count} tool call(s){tools}"
        )
    return "\n".join(lines)


def _view_task_history(tool_input: dict) -> str:
    limit = tool_input.get("limit") or 10
    try:
        limit = max(1, min(int(limit), 50))
    except (TypeError, ValueError):
        limit = 10
    return (
        "Currently active:\n" + _format_active(get_active())
        + "\n\nRecent executions:\n" + _format_recent(get_recent(limit=limit))
    )


def _cancel_request(tool_input: dict) -> str:
    request_id = (tool_input.get("request_id") or "").strip()
    if not request_id:
        return "A request_id is required -- call view_task_history first to see active requests and their IDs."
    if request_cancel(request_id):
        return (
            f"Cancellation requested for {request_id}. It will stop at the next safe "
            "checkpoint -- anything already in progress (like a tool call already "
            "running) is allowed to finish first, not interrupted mid-action."
        )
    return (
        f"No active request found with id {request_id} in this process -- it may "
        "have already finished, never existed, or belongs to a different Jarvis session."
    )


register(ToolSpec(
    name="view_task_history",
    description=(
        "See what Jarvis is currently doing (any active requests, with their "
        "request_id and progress) and a short history of recently completed, "
        "failed, or cancelled requests. Use when the user asks what you're "
        "working on, what you just did, or wants to cancel something and "
        "needs its request_id first."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "limit": {
                "type": "integer",
                "description": "Max number of recent (non-active) executions to include. Defaults to 10.",
            }
        },
        "required": [],
    },
    permission_level=0,
    handler=_view_task_history,
    parallel_safe=True,
))

register(ToolSpec(
    name="cancel_request",
    description=(
        "Stop a currently active request by its request_id (from "
        "view_task_history). Only ever affects requests running in this "
        "same Jarvis process -- never an arbitrary process or another "
        "device/session. Cancellation is cooperative: it stops the request "
        "at the next safe checkpoint rather than interrupting an "
        "in-progress action."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "request_id": {"type": "string", "description": "The request_id to cancel, from view_task_history."}
        },
        "required": ["request_id"],
    },
    permission_level=1,
    handler=_cancel_request,
))
