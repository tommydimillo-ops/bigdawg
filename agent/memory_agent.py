from database.memory import save_memory, get_memory


def remember(key, value):

    memory = get_memory()
    existing = memory.get(key)

    if isinstance(existing, list):
        facts = existing + [value]
    elif existing is None:
        facts = [value]
    else:
        facts = [existing, value]

    save_memory(key, facts)

    return f"I'll remember that {value}"


def recall(key):

    memory = get_memory()

    if key in memory and memory[key]:
        facts = memory[key]
        return "; ".join(facts) if isinstance(facts, list) else facts

    return "I don't remember that yet."


if __name__ == "__main__":

    print(
        remember("favorite_subject", "biology")
    )

    print(
        "Memory:",
        recall("favorite_subject")
    )