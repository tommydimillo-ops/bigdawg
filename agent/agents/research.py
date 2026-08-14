"""ResearchAgent -- a thin Agent wrapper around the EXISTING research
engine (agent/research_agent.py's research()), not a second one. That
function already runs its own safe, bounded, self-contained loop (its
own small read-only tool set: open_browser, read_document; its own
MAX_ITERATIONS cap) using agent/chat.py's shared Anthropic client -- this
module adds nothing to that except the typed Agent interface and
AgentResult shape agent.agents.manager needs.
"""
import time

from agent.agents.base import Agent, AgentMetadata
from agent.agents.models import AgentResult
from agent.request_context import RequestContext
from agent.research_agent import research


class ResearchAgent(Agent):

    @property
    def metadata(self) -> AgentMetadata:
        return AgentMetadata(
            name="research",
            description=(
                "Multi-step research: searches and reads real web pages, "
                "cross-checks facts across sources, and synthesizes an "
                "answer -- reuses agent.research_agent's existing engine."
            ),
            capabilities=["research", "web search", "fact-checking", "comparison"],
            supported_task_types=["research"],
        )

    def execute(self, task: str, context: RequestContext) -> AgentResult:
        start = time.time()
        try:
            answer = research(task)
        except Exception as error:
            return AgentResult(
                success=False, agent_name=self.metadata.name, request_id=context.request_id,
                result="", error=f"{type(error).__name__}: {error}",
                duration_seconds=time.time() - start,
            )
        return AgentResult(
            success=True, agent_name=self.metadata.name, request_id=context.request_id,
            result=answer, duration_seconds=time.time() - start,
            provider_used="anthropic",
        )
