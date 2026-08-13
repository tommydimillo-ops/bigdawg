LEVEL_NAMES = {
    0: "read-only",
    1: "safe local action",
    2: "modifies files/executes code",
    3: "external communication",
    4: "financial",
    5: "destructive/system",
}

# Every tool in agent/brain.py's TOOLS list must have an entry here — see
# the coverage check below. Kept as a flat, explicit map rather than
# inferred from tool names so a new tool never silently ends up
# unclassified.
TOOL_PERMISSION_LEVELS = {
    "take_screenshot": 0,
    "open_browser": 1,
    "search_on_page": 1,
    "click_on_page": 1,
    "open_application": 1,
    "fill_login": 0,  # preview only, never fills or submits anything
    "confirm_login": 3,  # actually signs in to a real account
    "draft_email": 0,  # preview only, creates an unsent draft
    "send_email": 3,  # actually sends real external communication
    "deep_reason": 0,  # pure reasoning, no side effects
    "note_pattern": 1,
    "list_patterns": 0,
    "forget_pattern": 1,
    "read_document": 0,
    "add_reminder": 1,
    "add_calendar_event": 1,
    "list_upcoming": 0,
    "create_note": 1,
    "control_music": 1,
    "get_system_status": 0,
    "get_weather": 0,
    "get_weekly_forecast": 0,
    "search_files": 0,
    "lock_screen": 1,
    "sleep_mac": 1,
    "get_clipboard": 0,
    "set_clipboard": 1,
    "set_timer": 1,
    "open_file": 1,
    "computer_see": 0,
    "computer_locate": 0,
    "computer_click": 2,
    "computer_type": 2,
    "computer_press_key": 2,
    "computer_confirm_action": 5,  # the one gated, human-approved step -- can send/pay/delete/submit
    "remember_fact": 1,
    "recall_facts": 0,
    "learn_rule": 1,
    "list_rules": 0,
    "run_python": 2,
    "view_recent_actions": 0,
    "schedule_task": 1,
    "list_scheduled_tasks": 0,
    "cancel_scheduled_task": 1,
    "research_agent": 1,
    # research_agent's own internal tool calls, logged separately so the
    # audit trail shows exactly which pages it visited, not just that it
    # ran (see agent/research_agent.py).
    "research_agent:open_browser": 1,
    "research_agent:read_document": 0,
}


def permission_level(tool_name):
    return TOOL_PERMISSION_LEVELS.get(tool_name)


def permission_label(tool_name):
    level = permission_level(tool_name)
    if level is None:
        return "UNCLASSIFIED"
    return f"L{level} ({LEVEL_NAMES[level]})"


def check_full_coverage(tool_names):
    """Every real tool must be classified — an unclassified tool is a gap
    in the safety/audit picture, not something to silently allow."""
    missing = [name for name in tool_names if name not in TOOL_PERMISSION_LEVELS]
    if missing:
        raise ValueError(f"Tools missing a permission level in agent/permissions.py: {missing}")
