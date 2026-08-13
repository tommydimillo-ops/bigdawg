"""A specialist sub-agent for multi-step research tasks — the "agents as
tools" pattern: invoked as a single tool call from the main coordinator,
but internally runs its own focused loop with its own system prompt and
tools, so it can chain several searches and page-reads to cross-check
information without burning the coordinator's own MAX_TOOL_ITERATIONS on
each intermediate step. This only adds an extra round of model calls when
the coordinator actually decides a task needs it — simple requests never
touch this module.
"""

from agent.audit import log_action
from agent.chat import anthropic_client as client
from config.settings import settings
from documents.reader import read_document
from tools.browser import open_and_read

MAX_ITERATIONS = 6

SYSTEM_PROMPT = (
    "You are a focused research assistant. Given a research question, use "
    "open_browser to search and read real pages — open multiple sources, "
    "cross-check facts across them, and don't just report the first result "
    "you find. When you have enough information, write a clear, "
    "well-organized answer that mentions which sources/sites it came from. "
    "Be thorough but efficient — don't open more pages than necessary to "
    "answer confidently."
)

TOOLS = [
    {
        "name": "open_browser",
        "description": (
            "Open a real browser window and navigate to a destination or "
            "search query, returning the page's visible text."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "target": {"type": "string", "description": "A URL or search query."}
            },
            "required": ["target"],
        },
    },
    {
        "name": "read_document",
        "description": "Read the text content of a local file (PDF, .txt, .md).",
        "input_schema": {
            "type": "object",
            "properties": {
                "file_path": {"type": "string", "description": "Path to the document."}
            },
            "required": ["file_path"],
        },
    },
]


def _run_tool(name, tool_input):
    if name == "open_browser":
        result = open_and_read(tool_input["target"])
    elif name == "read_document":
        result = read_document(tool_input["file_path"])
    else:
        result = f"Unknown tool: {name}"

    # Log every page this sub-agent visits, not just the fact that it ran
    # — otherwise the audit trail shows "research_agent was called" with
    # no record of what it actually did while it was in there.
    log_action(f"research_agent:{name}", tool_input, result)
    return result


def research(question):
    """Runs a self-contained research loop and returns a synthesized
    answer as plain text — not streamed, since this is a sub-call made
    from inside the main agent's own tool loop."""

    messages = [{"role": "user", "content": question}]

    for _ in range(MAX_ITERATIONS):
        response = client.messages.create(
            model=settings.default_model,
            # Same headroom fix as agent/executor.py — adaptive thinking
            # tokens count against this budget, and a low ceiling can get
            # entirely consumed by thinking alone on a hard question.
            max_tokens=4096,
            system=SYSTEM_PROMPT,
            tools=TOOLS,
            messages=messages,
        )

        if response.stop_reason != "tool_use":
            text = "".join(block.text for block in response.content if block.type == "text").strip()
            return text or "I couldn't find a clear answer to that."

        messages.append({"role": "assistant", "content": response.content})

        tool_results = []
        for block in response.content:
            if block.type == "tool_use":
                result = _run_tool(block.name, block.input)
                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": str(result),
                })

        messages.append({"role": "user", "content": tool_results})

    return (
        "The research took more steps than expected — here's what I found "
        "so far, though it may be incomplete."
    )


if __name__ == "__main__":
    import sys
    print(research(sys.argv[1] if len(sys.argv) > 1 else "What is the Python GIL?"))
