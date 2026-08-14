import fcntl
import json
import os
import re
import tempfile
import uuid
from contextlib import contextmanager

TASKS_FILE = os.path.expanduser("~/Library/Application Support/CampusPilot/scheduled_tasks.json")

TIME_PATTERN = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)$")


@contextmanager
def _locked(exclusive=False):
    os.makedirs(os.path.dirname(TASKS_FILE), exist_ok=True)
    with open(f"{TASKS_FILE}.lock", "a+") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH)
        yield


def _load():
    try:
        with open(TASKS_FILE, "r") as file:
            tasks = json.load(file)
        return tasks if isinstance(tasks, list) else []
    except (FileNotFoundError, OSError, ValueError):
        return []


def _save(tasks):
    descriptor, tmp_file = tempfile.mkstemp(
        prefix="scheduled-tasks-", suffix=".tmp", dir=os.path.dirname(TASKS_FILE),
    )
    try:
        with os.fdopen(descriptor, "w") as file:
            json.dump(tasks, file, indent=2)
        os.replace(tmp_file, TASKS_FILE)
    finally:
        if os.path.exists(tmp_file):
            os.remove(tmp_file)


def add_task(prompt, time_of_day):
    """time_of_day is 24-hour local time, e.g. '08:00'. Runs once per day
    at that time, as long as the scheduler daemon is running."""

    if not TIME_PATTERN.match(time_of_day.strip()):
        return None, "time_of_day must be 24-hour HH:MM, e.g. '08:00' or '17:30'."

    with _locked(exclusive=True):
        tasks = _load()
        task = {
            "id": uuid.uuid4().hex[:8],
            "prompt": prompt,
            "time_of_day": time_of_day.strip(),
            "enabled": True,
            "last_run_date": None,
        }
        tasks.append(task)
        _save(tasks)
    return task, None


def list_tasks():
    with _locked():
        return _load()


def remove_task(task_id):
    with _locked(exclusive=True):
        tasks = _load()
        remaining = [task for task in tasks if task["id"] != task_id]
        if len(remaining) == len(tasks):
            return False
        _save(remaining)
        return True


def mark_run(task_id, date_str):
    with _locked(exclusive=True):
        tasks = _load()
        for task in tasks:
            if task["id"] == task_id:
                task["last_run_date"] = date_str
        _save(tasks)
