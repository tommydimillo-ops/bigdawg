"""Runs scheduled tasks (created via the schedule_task tool) at their
appointed time each day. This is a separate, standalone process — nothing
runs on a schedule unless you start this yourself:

    python -m agent.scheduler_daemon

It only ever executes tasks the user explicitly scheduled through a real
conversation; it doesn't invent tasks or decide anything on its own. Every
run goes through the same tool-permission system as normal chat (source=
"scheduled"), which specifically blocks confirm_login from ever firing
unattended.
"""

import subprocess
import time
from datetime import datetime

from agent.executor import execute_task
from agent.scheduled_tasks import list_tasks, mark_run

POLL_INTERVAL_SECONDS = 30


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

        try:
            result = execute_task(task["prompt"], source="scheduled")
        except Exception as error:
            result = f"Error: {error}"

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
