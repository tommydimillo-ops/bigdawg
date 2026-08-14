"""A thin, explicit interface for invoking Claude with a skill's
instructions attached -- not a second way to call the model. Reuses
agent.executor.execute_task_stream (the one tool-calling loop, retry
policy, permission enforcement, verification, and history recording),
agent.chat.anthropic_client (the one shared client, constructed once),
and agent.model_router (the one provider-selection policy). No
connection pool, retry logic, or model router is duplicated here.

Normal requests (chat, voice, scheduled tasks) never need to call this
directly -- execute_task_stream already runs agent.delegation.decide()
internally and attaches a matched skill's instructions on its own (see
agent/executor.py and agent/brain.py's build_system_prompt). This module
exists for explicit/manual invocation with a specific skill already
chosen -- e.g. a future "run this skill" tool, the dashboard, or tests --
without needing to duplicate that wiring.
"""
from typing import Callable, Optional

from agent.execution_state import ExecutionState
from agent.executor import execute_task_stream


def invoke(
    request: str,
    history=None,
    source: str = "chat",
    skill_name: Optional[str] = None,
    on_state_created: Optional[Callable[[ExecutionState], None]] = None,
):
    """Runs `request` through the existing Jarvis core. If `skill_name`
    is given, that skill's instructions are attached to this request's
    system prompt (agent.brain.build_system_prompt reads
    state.selected_skill) -- everything else about execution (tool loop,
    permissions, retries, verification, history) is unchanged from any
    other request.

    Returns the same generator execute_task_stream does (streamed
    response text)."""

    def _observer(state: ExecutionState) -> None:
        if skill_name:
            state.selected_skill = skill_name
            state.delegation_destination = "claude_skill"
        if on_state_created is not None:
            on_state_created(state)

    return execute_task_stream(
        request, history=history, source=source, on_state_created=_observer,
    )
