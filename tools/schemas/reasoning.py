"""Sandboxed computation, escalated reasoning, and the research sub-agent."""
from agent.deep_reasoning import deep_reason
from agent.research_agent import research
from tools.registry import ToolSpec, register
from tools.sandbox_python import run_python

register(ToolSpec(
    name="run_python",
    description=(
        "Run Python code to compute something, test a snippet, or "
        "process data. Executes in an isolated sandbox: no network "
        "access, and no file writes outside a disposable sandbox "
        "directory — so it's safe to use for real computation, but it "
        "can't reach the internet or save results anywhere permanent. "
        "Use this instead of trying to do arithmetic or logic in your "
        "head when it's non-trivial."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "code": {
                "type": "string",
                "description": "The Python code to run. Use print() to produce output.",
            }
        },
        "required": ["code"],
    },
    permission_level=2,
    handler=lambda ti: run_python(ti["code"]),
))

register(ToolSpec(
    name="deep_reason",
    description=(
        "Escalate a genuinely hard or ambiguous question to a slower, "
        "much more thorough reasoning pass — real logic/math/planning "
        "problems, or requests where you're not confident a fast "
        "answer would actually be right. Costs real extra time, so "
        "don't use it for ordinary requests."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "question": {
                "type": "string",
                "description": "The question or problem, with enough context to be self-contained.",
            }
        },
        "required": ["question"],
    },
    permission_level=0,
    handler=lambda ti: deep_reason(ti["question"]),
))

register(ToolSpec(
    name="research_agent",
    description=(
        "Hand off a research question to a specialist agent that runs "
        "its own multi-step browsing loop — opens several sources, "
        "cross-checks them, and returns one synthesized answer. Use "
        "for genuinely multi-source questions (comparisons, 'best X "
        "for Y', anything needing more than one page to answer well); "
        "use open_browser directly for a single simple lookup instead."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "question": {
                "type": "string",
                "description": "The research question, as a clear standalone question.",
            }
        },
        "required": ["question"],
    },
    permission_level=1,
    handler=lambda ti: research(ti["question"]),
))
