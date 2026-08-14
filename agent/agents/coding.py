"""CodingAgent -- identifies coding/software-engineering requests for
routing/observability purposes. Deliberately does NOT execute anything
itself this phase: no unrestricted shell, no automatic file edits, no
bypass of the existing permission/autonomy/confirmation architecture.
Every coding-classified request still runs through the completely
unmodified agent.executor tool loop (metadata["deferred_to_executor"]),
which already has real tool access (file edits, running scripts) gated
by tools.registry's permission levels and agent.autonomy -- there is no
class of action this phase's CodingAgent could offer that path doesn't
already provide, safely.

Future Claude adapter boundary (Phase 7 section 15): agent.claude_gateway
already exists (Phase 6.5) as exactly this kind of thin, explicit "invoke
Claude with something specific attached" interface -- see its own
docstring. A later phase wiring CodingAgent to a real coding-specialist
path (Claude Code, a Skill, Cowork) would have it call something shaped
like agent.claude_gateway.invoke(task, skill_name="coding") instead of
returning a deferred result, WITHOUT agent.agents.manager, agent.agents.
router, agent.executor, agent.autonomy, or agent.verification needing to
change at all -- this docstring records that boundary; nothing here
calls it yet, since no such coding-specific skill/gateway is registered
today and inventing one prematurely isn't this phase's job.
"""
import time

from agent.agents.base import Agent, AgentMetadata
from agent.agents.models import AgentResult
from agent.request_context import RequestContext


class CodingAgent(Agent):

    @property
    def metadata(self) -> AgentMetadata:
        return AgentMetadata(
            name="coding",
            description=(
                "Identifies coding/software-engineering requests (fixing "
                "errors, adding features, refactoring) for attribution -- "
                "defers actual execution to the ordinary executor, which "
                "already has permission-gated file/tool access."
            ),
            capabilities=["coding", "debugging", "refactoring", "software engineering"],
            supported_task_types=["coding"],
        )

    def execute(self, task: str, context: RequestContext) -> AgentResult:
        start = time.time()
        return AgentResult(
            success=True, agent_name=self.metadata.name, request_id=context.request_id,
            result="", duration_seconds=time.time() - start,
            metadata={"deferred_to_executor": True},
        )
