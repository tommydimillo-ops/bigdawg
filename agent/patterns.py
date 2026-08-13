from database.memory import save_memory, get_memory

PATTERNS_KEY = "patterns"

# Caps unbounded growth — keeps the most recent observations rather than
# letting this silently balloon the system prompt (and cost) forever.
MAX_PATTERNS = 50


def note_pattern(observation):
    memory = get_memory()
    patterns = memory.get(PATTERNS_KEY, [])
    patterns.append(observation)
    patterns = patterns[-MAX_PATTERNS:]
    save_memory(PATTERNS_KEY, patterns)
    return f"Noted: {observation}"


def list_patterns():
    patterns = get_memory().get(PATTERNS_KEY, [])
    if not patterns:
        return "No patterns noticed yet."
    return "\n".join(f"- {p}" for p in patterns)


def forget_pattern(text):
    memory = get_memory()
    patterns = memory.get(PATTERNS_KEY, [])
    matching = [p for p in patterns if text.lower() in p.lower()]

    if not matching:
        return f"No noted pattern matching '{text}'."

    remaining = [p for p in patterns if p not in matching]
    save_memory(PATTERNS_KEY, remaining)
    return f"Forgot {len(matching)} pattern(s) matching '{text}'."


def patterns_as_prompt_text():
    patterns = get_memory().get(PATTERNS_KEY, [])
    if not patterns:
        return ""
    return "\n".join(f"- {p}" for p in patterns)
