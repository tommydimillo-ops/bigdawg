"""Single source of truth for every tool Jarvis can call.

Previously the same ~45 tools were declared three times over: schemas in
agent/brain.py, permission levels in agent/permissions.py, and dispatch
logic in agent/executor.py's if/elif chain. Nothing enforced that all
three stayed in sync -- a tool could exist in one place and not another
with no error until someone hit it at runtime. Now each tool is a single
ToolSpec, registered once, and everything else (schemas for the model,
permission lookups, dispatch, confirmation/unattended-execution rules)
is derived from that one registration.

tools/schemas/*.py modules call register() at import time; importing the
tools.schemas package (which agent/brain.py does) populates the whole
registry as a side effect.
"""
from dataclasses import dataclass
from typing import Callable, Optional

LEVEL_NAMES = {
    0: "read-only",
    1: "safe local action",
    2: "modifies files/executes code",
    3: "external communication",
    4: "financial",
    5: "destructive/system",
}


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    input_schema: dict
    permission_level: int
    handler: Callable[[dict], str]

    # Blocked entirely from source="scheduled" runs -- finalizes an
    # already-previewed action a live human explicitly approved, and an
    # unattended run has no one present to give that approval.
    requires_live_confirmation: bool = False

    # Also blocked from source="scheduled" runs, for a broader reason than
    # requires_live_confirmation: real unsupervised control of whatever's
    # on screen while no one's watching, not just one gated finalize step.
    unattended_allowed: bool = True

    # Has a real-world side effect. If a provider fails mid-loop after one
    # of these already ran, retrying the whole request on the other
    # provider risks repeating the action -- this is reported to the user
    # instead of silently retried.
    side_effect: bool = False

    # Verified safe to run concurrently with other parallel-safe tools in
    # the same batch (pure reads, no shared mutable state to race on).
    parallel_safe: bool = False


_REGISTRY: dict[str, ToolSpec] = {}

# Audit-log-only permission labels for sub-agent-internal actions that
# aren't real top-level dispatchable tools -- e.g. agent/research_agent.py
# runs its own separate internal tool loop and logs each of its calls
# under a namespaced name like "research_agent:open_browser" so the audit
# trail shows exactly which pages it visited, not just that it ran. These
# have no schema and no handler here, just a permission level so
# agent.audit.log_action can show something other than "UNCLASSIFIED".
_EXTRA_PERMISSION_LABELS = {
    "research_agent:open_browser": 1,
    "research_agent:read_document": 0,
}


def register(spec: ToolSpec) -> None:
    if spec.name in _REGISTRY:
        raise ValueError(f"Tool '{spec.name}' is already registered")
    _REGISTRY[spec.name] = spec


def get(name: str) -> Optional[ToolSpec]:
    return _REGISTRY.get(name)


def all_specs() -> list[ToolSpec]:
    return list(_REGISTRY.values())


def all_names() -> list[str]:
    return list(_REGISTRY.keys())


def anthropic_schemas() -> list[dict]:
    """Tool schemas in Anthropic's messages-API shape."""
    return [
        {"name": spec.name, "description": spec.description, "input_schema": spec.input_schema}
        for spec in _REGISTRY.values()
    ]


def openai_schemas() -> list[dict]:
    """Tool schemas in OpenAI's function-calling shape."""
    return [
        {
            "type": "function",
            "function": {
                "name": spec.name,
                "description": spec.description,
                "parameters": spec.input_schema,
            },
        }
        for spec in _REGISTRY.values()
    ]


def permission_level(name: str) -> Optional[int]:
    spec = _REGISTRY.get(name)
    if spec is not None:
        return spec.permission_level
    return _EXTRA_PERMISSION_LABELS.get(name)


def permission_label(name: str) -> str:
    level = permission_level(name)
    if level is None:
        return "UNCLASSIFIED"
    return f"L{level} ({LEVEL_NAMES[level]})"


def check_full_coverage(names) -> None:
    """Every real tool must be registered -- an unregistered tool is a gap
    in the safety/audit picture, not something to silently allow."""
    missing = [name for name in names if name not in _REGISTRY]
    if missing:
        raise ValueError(f"Tools missing registration in tools/registry.py: {missing}")


def requires_live_confirmation(name: str) -> bool:
    spec = _REGISTRY.get(name)
    return bool(spec and spec.requires_live_confirmation)


def unattended_allowed(name: str) -> bool:
    spec = _REGISTRY.get(name)
    return spec.unattended_allowed if spec is not None else True


def side_effect_tools() -> set[str]:
    return {spec.name for spec in _REGISTRY.values() if spec.side_effect}


def parallel_safe_tools() -> set[str]:
    return {spec.name for spec in _REGISTRY.values() if spec.parallel_safe}


def dispatch(name: str, tool_input: dict) -> str:
    spec = _REGISTRY.get(name)
    if spec is None:
        return f"Unknown tool: {name}"
    return spec.handler(tool_input)
