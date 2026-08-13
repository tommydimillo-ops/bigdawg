import json
import os

# Absolute, XDG-style path -- matches every other store in this project
# (scheduled_tasks.json, audit.log, etc). The old relative "memory.json"
# meant memory location silently depended on the process's working
# directory, so the Streamlit app, the menu-bar app, and a plain `python3`
# invocation could each end up reading/writing a different file.
MEMORY_FILE = os.path.expanduser("~/Library/Application Support/CampusPilot/memory.json")


def save_memory(key, value):
    memory = get_memory()
    memory[key] = value

    os.makedirs(os.path.dirname(MEMORY_FILE), exist_ok=True)
    tmp_file = f"{MEMORY_FILE}.tmp"
    with open(tmp_file, "w") as file:
        json.dump(memory, file, indent=4)

    os.replace(tmp_file, MEMORY_FILE)


def get_memory():
    if os.path.exists(MEMORY_FILE):
        with open(MEMORY_FILE, "r") as file:
            return json.load(file)

    return {}