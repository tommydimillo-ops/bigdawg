"""OpenClaw M1/M2 -- the Jarvis tools backed by agent/openclaw_gateway.py
(connection/auth/RPC-allowlist) and, for messaging, agent/
openclaw_messaging.py (channel/recipient allowlist, validation,
idempotency, result normalization). All flow through the ordinary
tools.registry path exactly like every other tool (permission level,
dispatch, audit logging) -- no parallel dispatch path, no separate
OpenClaw "dispatcher".

openclaw_status/openclaw_list_nodes (M1): neither takes a method-
selecting parameter, so neither can ever reach an RPC method outside
agent.openclaw_gateway's _READ_PROFILE allowlist; both are pure reads
with no path to a side effect, matching tools/schemas/system.py's
get_system_status precedent exactly (permission_level=0,
parallel_safe=True).

send_message_via_openclaw (M2): the only Jarvis tool that can reach the
real Gateway `send` RPC, always through the separate _MESSAGE_PROFILE
device identity (operator.write, never the read identity). Takes
exactly channel/target/message -- never a raw RPC method, never a
device/Gateway token, never an OpenClaw session identifier. Deliberately
no account_id/thread_id in this first release: the real SendParams
schema marks both optional (not required for a basic single-account
direct message), and neither is independently allowlisted yet -- adding
them now would let a caller select additional routing without Jarvis's
deterministic channel+target policy actually covering that choice.
Real-world external communication, the same risk class tools/schemas/
logins_and_email.py's send_email already exists for -- permission_level=3,
side_effect=True, requires_live_confirmation=True.
"""
import json

from agent.openclaw_gateway import get_node_list, get_status
from agent.openclaw_messaging import send_message
from tools.registry import ToolSpec, register


def _openclaw_status(tool_input: dict) -> str:
    return json.dumps(get_status())


def _openclaw_list_nodes(tool_input: dict) -> str:
    return json.dumps(get_node_list())


def _send_message_via_openclaw(tool_input: dict) -> str:
    result = send_message(
        channel=tool_input.get("channel"),
        target=tool_input.get("target"),
        message=tool_input.get("message"),
    )
    return json.dumps(result)


register(ToolSpec(
    name="openclaw_status",
    description=(
        "Check whether the optional OpenClaw Gateway bridge is configured "
        "and reachable, and its basic status. Read-only -- never modifies "
        "anything, never used for Jarvis's own model routing or "
        "permission decisions."
    ),
    input_schema={"type": "object", "properties": {}, "required": []},
    permission_level=0,
    handler=_openclaw_status,
    parallel_safe=True,
))

register(ToolSpec(
    name="openclaw_list_nodes",
    description=(
        "List devices/nodes currently known to the optional OpenClaw "
        "Gateway (id, display name, platform, connection state, and "
        "high-level declared capability names only). Read-only -- never "
        "invokes any node capability."
    ),
    input_schema={"type": "object", "properties": {}, "required": []},
    permission_level=0,
    handler=_openclaw_list_nodes,
    parallel_safe=True,
))

register(ToolSpec(
    name="send_message_via_openclaw",
    description=(
        "Send a plain-text outbound message through the optional OpenClaw "
        "Gateway to an EXACT, pre-configured channel and recipient — text "
        "only, no media/attachments. Disabled by default and fails "
        "closed unless the operator has explicitly enabled OpenClaw "
        "messaging and allowlisted both the exact channel and the exact "
        "recipient target in config; there is no fuzzy/name-based "
        "recipient resolution, so 'channel' and 'target' must already be "
        "one of the operator's configured exact values, never a "
        "freeform name like \"Bob\". Real-world external communication — "
        "always confirm the exact channel, recipient, and message text "
        "with the user first, the same as send_email."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "channel": {
                "type": "string",
                "description": "Exact configured/allowlisted channel name, e.g. 'telegram'.",
            },
            "target": {
                "type": "string",
                "description": "Exact configured/allowlisted recipient target ID for that channel.",
            },
            "message": {
                "type": "string",
                "description": "Plain text message body to send.",
            },
        },
        "required": ["channel", "target", "message"],
    },
    permission_level=3,
    handler=_send_message_via_openclaw,
    side_effect=True,
    unattended_allowed=False,
    requires_live_confirmation=True,
    parallel_safe=False,
))
