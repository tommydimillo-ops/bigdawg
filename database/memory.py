import json
import os

MEMORY_FILE = "memory.json"


def save_memory(key, value):
    memory = get_memory()
    memory[key] = value

    tmp_file = f"{MEMORY_FILE}.tmp"
    with open(tmp_file, "w") as file:
        json.dump(memory, file, indent=4)

    os.replace(tmp_file, MEMORY_FILE)


def get_memory():
    if os.path.exists(MEMORY_FILE):
        with open(MEMORY_FILE, "r") as file:
            return json.load(file)

    return {}