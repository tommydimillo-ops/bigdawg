"""A dedicated high-effort reasoning pathway for hard or ambiguous requests
— invoked as a tool from the main coordinator, not a default mode, so
normal fast requests aren't slowed down by it. Uses Claude's adaptive
extended-thinking mode at maximum effort, verified against a real API call
to noticeably improve reasoning quality on a problem with a tempting wrong
answer (correctly worked past the "17 sheep" red herring rather than
computing 17-9)."""

from agent.chat import anthropic_client as client
from config.settings import settings

SYSTEM_PROMPT = (
    "Think through this as carefully and thoroughly as the problem "
    "actually requires. If the request is ambiguous, consider the most "
    "plausible interpretations before answering. If it's a reasoning, "
    "planning, or logic problem, work through it step by step rather than "
    "pattern-matching to a quick answer. Give a clear, direct final answer."
)


def deep_reason(question):
    response = client.messages.create(
        model=settings.default_model,
        # "max" effort thinking can use substantially more tokens than
        # the coordinator's automatic thinking does — this needs the most
        # headroom of any call site, since running out mid-thought here
        # would defeat the entire point of this tool.
        max_tokens=8192,
        system=SYSTEM_PROMPT,
        thinking={"type": "adaptive", "display": "summarized"},
        output_config={"effort": "max"},
        messages=[{"role": "user", "content": question}],
    )

    text = "".join(block.text for block in response.content if block.type == "text").strip()
    return text or "I wasn't able to reach a clear conclusion on that."


if __name__ == "__main__":
    import sys
    print(deep_reason(sys.argv[1] if len(sys.argv) > 1 else "What is the Python GIL?"))
