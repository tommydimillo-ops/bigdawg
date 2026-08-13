import json
import os
import threading
from datetime import datetime

from agent.permissions import permission_label

LOG_DIR = os.path.expanduser("~/Library/Application Support/CampusPilot")
LOG_FILE = os.path.join(LOG_DIR, "audit.log")

MAX_FIELD_LENGTH = 500

# Read-only tools can now run concurrently within a single response (see
# executor.py's PARALLEL_SAFE_TOOLS), so multiple threads can call
# log_action at nearly the same instant — this keeps each append atomic
# instead of risking interleaved/corrupted lines.
_LOG_LOCK = threading.Lock()


def _truncate(value):
    text = str(value)
    return text if len(text) <= MAX_FIELD_LENGTH else text[:MAX_FIELD_LENGTH] + "…"


def log_action(tool_name, tool_input, result):
    os.makedirs(LOG_DIR, exist_ok=True)

    entry = {
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "tool": tool_name,
        "permission": permission_label(tool_name),
        "input": _truncate(tool_input),
        "result": _truncate(result),
    }

    with _LOG_LOCK:
        with open(LOG_FILE, "a") as file:
            file.write(json.dumps(entry) + "\n")


def recent_actions(limit=20):
    if not os.path.exists(LOG_FILE):
        return []

    with open(LOG_FILE, "r") as file:
        lines = file.readlines()[-limit:]

    return [json.loads(line) for line in lines if line.strip()]


def recent_actions_text(limit=20):
    entries = recent_actions(limit)

    if not entries:
        return "No actions logged yet."

    lines = []
    for entry in entries:
        permission = entry.get("permission", "")
        lines.append(
            f"[{entry['timestamp']}] {entry['tool']} {permission}"
            f"({entry['input']}) -> {entry['result']}"
        )

    return "\n".join(lines)
