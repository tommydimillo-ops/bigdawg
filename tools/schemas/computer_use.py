"""Real mouse/keyboard/screen control. The whole family is blocked from
unattended (scheduled) execution -- see tools/computer_use.py's module
docstring for the full safety model (confirm-gated consequential actions,
never types credentials, audited with screenshots)."""
from tools.computer_use import (
    computer_click,
    computer_confirm_action,
    computer_locate,
    computer_press_key,
    computer_see,
    computer_type,
)
from tools.registry import ToolSpec, register

register(ToolSpec(
    name="computer_see",
    description="Take a screenshot right now and describe what's currently on screen, in any app.",
    input_schema={"type": "object", "properties": {}, "required": []},
    permission_level=0,
    handler=lambda ti: computer_see(),
    unattended_allowed=False,
))

register(ToolSpec(
    name="computer_locate",
    description=(
        "Find the on-screen pixel location of a described UI element "
        "(a button, field, link, icon, menu item) so you can click it "
        "with computer_click or computer_confirm_action."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "description": {"type": "string", "description": "What to find, e.g. \"the Save button\" or \"the search field\"."}
        },
        "required": ["description"],
    },
    permission_level=0,
    handler=lambda ti: computer_locate(ti["description"]),
    unattended_allowed=False,
))

register(ToolSpec(
    name="computer_click",
    description=(
        "Click at a specific screen location (from computer_locate). "
        "For ordinary navigation/interaction only — NEVER for a click "
        "that sends, pays, deletes, or submits something real; use "
        "computer_confirm_action for those instead."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "x": {"type": "integer"},
            "y": {"type": "integer"},
        },
        "required": ["x", "y"],
    },
    permission_level=2,
    handler=lambda ti: computer_click(ti["x"], ti["y"]),
    side_effect=True,
    unattended_allowed=False,
))

register(ToolSpec(
    name="computer_type",
    description=(
        "Type text into whatever field currently has focus (click it "
        "first with computer_click). NEVER use this to type a "
        "password, PIN, or other credential — use fill_login/"
        "confirm_login for real logins instead, which never expose "
        "the password in chat."
    ),
    input_schema={
        "type": "object",
        "properties": {"text": {"type": "string"}},
        "required": ["text"],
    },
    permission_level=2,
    handler=lambda ti: computer_type(ti["text"]),
    side_effect=True,
    unattended_allowed=False,
))

register(ToolSpec(
    name="computer_press_key",
    description=(
        "Press a key or key combo (e.g. 'enter', 'tab', 'escape', "
        "'cmd+a', 'cmd+c'). NEVER for a keypress that itself sends/"
        "submits something (e.g. pressing enter to send a message) — "
        "use computer_confirm_action for that instead."
    ),
    input_schema={
        "type": "object",
        "properties": {"key": {"type": "string"}},
        "required": ["key"],
    },
    permission_level=2,
    handler=lambda ti: computer_press_key(ti["key"]),
    side_effect=True,
    unattended_allowed=False,
))

register(ToolSpec(
    name="computer_confirm_action",
    description=(
        "Executes a click or keypress that sends, pays, deletes, or "
        "submits something real (e.g. clicking 'Buy Now', 'Delete', "
        "'Send', or pressing enter in a compose window). This is a "
        "two-step, human-approved process like confirm_login: first "
        "describe exactly what you're about to do and wait for the "
        "user to explicitly say yes in a later message — never infer "
        "or assume confirmation, and never call this in the same turn "
        "as the description. Provide either x/y (to click) or key (to "
        "press)."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "description": {"type": "string", "description": "Plain description of the action being confirmed, for the audit log."},
            "x": {"type": "integer"},
            "y": {"type": "integer"},
            "key": {"type": "string"},
        },
        "required": ["description"],
    },
    permission_level=5,
    handler=lambda ti: computer_confirm_action(ti["description"], ti.get("x"), ti.get("y"), ti.get("key")),
    # Not requires_live_confirmation -- already fully covered by
    # unattended_allowed=False below (the whole computer_* family is
    # blocked from scheduled runs), and the original code never had this
    # tool in REQUIRES_LIVE_CONFIRMATION either. Adding both would change
    # the skip message text for no behavioral gain.
    side_effect=True,
    unattended_allowed=False,
))
