"""Runs scheduled tasks (created via the schedule_task tool) at their
appointed time each day. This is a separate, standalone process — nothing
runs on a schedule unless you start this yourself:

    python -m agent.scheduler_daemon

It only ever executes tasks the user explicitly scheduled through a real
conversation; it doesn't invent tasks or decide anything on its own. Every
run goes through the same tool-permission system as normal chat (source=
"scheduled"), which specifically blocks confirm_login from ever firing
unattended.

LIFECYCLE NOTE (found during the Phase 2 lifecycle review): ui/menu_bar.py
runs this exact same polling logic itself, in a background thread
(_scheduler_loop / _run_due_scheduled_tasks), as long as the menu-bar app
is running -- which, via the LaunchAgent, is effectively always. Running
this standalone daemon *at the same time* as the menu-bar app means every
scheduled task fires twice at its scheduled time (two independent pollers
both see it's due). This file is kept only as a fallback for running the
scheduler without the menu-bar app at all -- don't run both together.
"""

import subprocess
import time
from datetime import datetime

from agent.executor import execute_task
from agent.observability import log_event
from agent.scheduled_tasks import list_tasks, mark_run
from config.settings import settings

POLL_INTERVAL_SECONDS = settings.scheduler_poll_seconds


def _notify(title, message):
    escaped_title = title.replace("\\", "\\\\").replace('"', '\\"')
    escaped_message = message.replace("\\", "\\\\").replace('"', '\\"')
    script = f'display notification "{escaped_message}" with title "{escaped_title}"'
    subprocess.run(["osascript", "-e", script], capture_output=True)


def _run_due_tasks():
    now = datetime.now()
    today = now.strftime("%Y-%m-%d")
    current_hm = now.strftime("%H:%M")

    for task in list_tasks():
        if not task.get("enabled", True):
            continue
        if task.get("last_run_date") == today:
            continue
        if task["time_of_day"] != current_hm:
            continue

        print(f"[{now.isoformat(timespec='seconds')}] Running scheduled task {task['id']}: {task['prompt']}")
        log_event("scheduled_task_started", component="scheduler_daemon", task_id=task["id"])

        try:
            result = execute_task(task["prompt"], source="scheduled")
        except Exception as error:
            result = f"Error: {error}"
            log_event(
                "scheduled_task_failed", component="scheduler_daemon", level="error",
                task_id=task["id"], error_type=type(error).__name__,
            )

        mark_run(task["id"], today)
        _notify("CampusPilot", result[:200])
        print(f"  -> {result[:200]}")


def run_forever():
    print("CampusPilot scheduler running (checks every 30s). Press Ctrl+C to stop.")
    while True:
        _run_due_tasks()
        time.sleep(POLL_INTERVAL_SECONDS)


if __name__ == "__main__":
    try:
        run_forever()
    except KeyboardInterrupt:
        print("\nScheduler stopped.")
