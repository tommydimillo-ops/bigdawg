"""OpenClaw M1 -- the two read-only Jarvis tools backed by
agent/openclaw_gateway.py. Both flow through the ordinary tools.registry
path exactly like every other tool (permission level, dispatch,
audit logging) -- no parallel dispatch path, no separate OpenClaw
"dispatcher". Neither tool takes a method-selecting parameter, so
neither can ever reach an RPC method outside agent.openclaw_gateway's
own fixed allowlist; both are pure reads with no path to a side effect,
matching tools/schemas/system.py's get_system_status precedent exactly
(permission_level=0, parallel_safe=True).
"""
import json

from agent.openclaw_gateway import get_node_list, get_status
from tools.registry import ToolSpec, register


def _openclaw_status(tool_input: dict) -> str:
    return json.dumps(get_status())


def _openclaw_list_nodes(tool_input: dict) -> str:
    return json.dumps(get_node_list())


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
