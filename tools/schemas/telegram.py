"""The Jarvis tool for outbound Telegram messages, backed by
agent/telegram_bridge.py. Flows through tools.registry exactly like every
other tool -- no separate dispatch path.

send_telegram_message takes ONLY the message text: the recipient is
always the single configured owner chat (agent/telegram_bridge.py has no
arbitrary-recipient path), so there is no "sent to the wrong person" or
"leaked to a stranger" failure mode that a live confirmation would guard
against. That is why -- unlike send_email / send_message_via_openclaw --
this is not requires_live_confirmation and IS unattended_allowed: its
main job is proactive "ping me when X finishes" notifications, including
from scheduled tasks. It is still permission_level=3 (external
communication) so autonomy still gates it at lower autonomy levels.
"""
import json

from agent.telegram_bridge import send_message
from tools.registry import ToolSpec, register


def _send_telegram_message(tool_input: dict) -> str:
    return json.dumps(send_message(text=tool_input.get("message")))


register(ToolSpec(
    name="send_telegram_message",
    description=(
        "Send yourself a plain-text Telegram message via the optional "
        "Telegram bridge. The recipient is always the user's own "
        "pre-configured Telegram chat -- there is no way to send to "
        "anyone else, and no recipient parameter. Use this to proactively "
        "notify the user (e.g. when a long task or scheduled job "
        "finishes, or to reach them when they're away from the Mac). "
        "Fails closed with a clear message if the bridge isn't configured."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "message": {
                "type": "string",
                "description": "Plain text to send to the user's Telegram.",
            },
        },
        "required": ["message"],
    },
    permission_level=3,
    handler=_send_telegram_message,
    side_effect=True,
    unattended_allowed=True,
    requires_live_confirmation=False,
    parallel_safe=False,
))
