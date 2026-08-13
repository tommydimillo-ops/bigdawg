"""Recurring scheduled tasks and the action audit log."""
from agent.audit import recent_actions_text
from agent.scheduled_tasks import add_task, list_tasks, remove_task
from tools.registry import ToolSpec, register


def _handle_schedule_task(tool_input):
    task, error = add_task(tool_input["prompt"], tool_input["time_of_day"])
    if error:
        return f"Could not schedule: {error}"
    return (
        f"Scheduled (id {task['id']}): \"{task['prompt']}\" daily at "
        f"{task['time_of_day']}. This only fires if the scheduler "
        "process is running (`python -m agent.scheduler_daemon`)."
    )


register(ToolSpec(
    name="schedule_task",
    description=(
        "Schedule a request to run automatically once a day at a "
        "given time (e.g. 'check what's due every morning'). Only "
        "runs if the separate scheduler process is running — mention "
        "that to the user if they haven't set it up. IMPORTANT: a "
        "scheduled task runs unattended with nobody watching, so "
        "confirm_login will never fire from one even if requested — "
        "don't schedule anything that depends on it."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "prompt": {
                "type": "string",
                "description": "What to do, phrased exactly as you'd say it in chat.",
            },
            "time_of_day": {
                "type": "string",
                "description": "24-hour local time, e.g. '08:00' or '17:30'.",
            },
        },
        "required": ["prompt", "time_of_day"],
    },
    permission_level=1,
    handler=_handle_schedule_task,
    side_effect=True,
))


def _handle_list_scheduled_tasks(tool_input):
    tasks = list_tasks()
    if not tasks:
        return "No scheduled tasks."
    return "\n".join(
        f"[{t['id']}] {t['time_of_day']} daily — \"{t['prompt']}\" "
        f"({'enabled' if t.get('enabled', True) else 'disabled'}, "
        f"last ran: {t.get('last_run_date') or 'never'})"
        for t in tasks
    )


register(ToolSpec(
    name="list_scheduled_tasks",
    description="List every scheduled task, its time, and whether it's enabled.",
    input_schema={"type": "object", "properties": {}, "required": []},
    permission_level=0,
    handler=_handle_list_scheduled_tasks,
    parallel_safe=True,
))


def _handle_cancel_scheduled_task(tool_input):
    removed = remove_task(tool_input["task_id"])
    task_id = tool_input["task_id"]
    return f"Cancelled task {task_id}." if removed else f"No task with id {task_id}."


register(ToolSpec(
    name="cancel_scheduled_task",
    description="Cancel a scheduled task by its id (from list_scheduled_tasks).",
    input_schema={
        "type": "object",
        "properties": {
            "task_id": {"type": "string", "description": "The task's id."}
        },
        "required": ["task_id"],
    },
    permission_level=1,
    handler=_handle_cancel_scheduled_task,
    side_effect=True,
))

register(ToolSpec(
    name="view_recent_actions",
    description=(
        "Show a log of what you've actually done recently — every tool "
        "call, its input, and its result. Use when the user asks what "
        "you've been doing, wants to audit your actions, or asks you "
        "to double-check something you claimed to have done."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "limit": {
                "type": "integer",
                "description": "How many recent actions to show. Defaults to 20.",
            }
        },
        "required": [],
    },
    permission_level=0,
    handler=lambda ti: recent_actions_text(ti.get("limit", 20)),
    parallel_safe=True,
))
