from database.memory import save_memory, get_memory

LESSONS_KEY = "lessons"


def learn_rule(rule):
    memory = get_memory()
    lessons = memory.get(LESSONS_KEY, [])
    lessons.append(rule)
    save_memory(LESSONS_KEY, lessons)
    return f"Got it — from now on: {rule}"


def list_rules():
    lessons = get_memory().get(LESSONS_KEY, [])
    if not lessons:
        return "No standing rules learned yet."
    return "\n".join(f"- {rule}" for rule in lessons)


def lessons_as_prompt_text():
    """Blank string when there are none, so the base prompt is unchanged
    until the user actually teaches Jarvis something."""
    lessons = get_memory().get(LESSONS_KEY, [])
    if not lessons:
        return ""
    return "\n".join(f"- {rule}" for rule in lessons)
