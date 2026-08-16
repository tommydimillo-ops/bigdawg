import fcntl
import json
import os
import threading
from datetime import datetime

from agent.permissions import permission_label

LOG_DIR = os.path.expanduser("~/Library/Application Support/CampusPilot")
LOG_FILE = os.path.join(LOG_DIR, "audit.log")

MAX_FIELD_LENGTH = 500

# Read-only tools can now run concurrently within a single response (see
# executor.py's PARALLEL_SAFE_TOOLS), so multiple threads in THIS process
# can call log_action at nearly the same instant -- this keeps each
# append atomic against those.
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

    # Phase 9 Milestone 3: bounded-parallel coworker delegation means
    # several genuinely separate OS processes (each execute_agent
    # subprocess, itself possibly calling log_action -- see
    # agent/research_agent.py's own log_action call) can now append here
    # at nearly the same instant, not just several threads inside one
    # process. _LOG_LOCK above only ever protected the latter. A single
    # small write() to a file opened with O_APPEND is atomic on POSIX in
    # practice, but this project's own stated convention for a file
    # multiple PROCESSES touch is an explicit fcntl.flock (see
    # agent/usage.py, agent/scheduler_lock.py, agent/browser_lock.py) --
    # applying that same convention here rather than relying on an
    # unstated OS guarantee.
    with _LOG_LOCK:
        with open(LOG_FILE, "a") as file:
            fcntl.flock(file.fileno(), fcntl.LOCK_EX)
            try:
                file.write(json.dumps(entry) + "\n")
            finally:
                fcntl.flock(file.fileno(), fcntl.LOCK_UN)


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
