import json
import os

MEMORY_FILE = "memory.json"


def save_memory(key, value):
    if os.path.exists(MEMORY_FILE):
        with open(MEMORY_FILE, "r") as file:
            memory = json.load(file)
    else:
        memory = {}

    memory[key] = value

    with open(MEMORY_FILE, "w") as file:
        json.dump(memory, file, indent=4)


def get_memory():
    if os.path.exists(MEMORY_FILE):
        with open(MEMORY_FILE, "r") as file:
            return json.load(file)

    return {}