"""A small, explicit bundle of per-request information, created once at
the start of execute_task_stream and threaded through the agent execution
pipeline -- NOT a global. Each request gets its own RequestContext, mainly
so every log line and audit entry for that request can be correlated by
request_id.

Deliberately excludes a few things the field list might suggest belong
here, to avoid becoming the "giant state object" this is explicitly meant
not to be:
- The live conversation/message list stays as the existing `messages`
  parameter threaded through executor.py's loop functions -- mirroring it
  into the context would create two sources of truth for the same mutable
  data.
- Available tools are global, static, and already trivially available via
  tools.registry.all_names() from anywhere -- not meaningfully "per
  request" information worth duplicating here.
- Relevant memory isn't a real, filtered concept in this codebase yet
  (recall_facts just returns everything) -- there's nothing to attach
  until that (a later phase) exists.
- Selected model belongs on ExecutionState (agent/execution_state.py),
  since it's determined *during* execution, not known when the request
  starts.
"""
import contextvars
import time
import uuid
from dataclasses import dataclass, field
from typing import Optional

from config.settings import settings


@dataclass
class RequestContext:
    request_id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    timestamp: float = field(default_factory=time.time)
    user_input: str = ""
    source: str = "chat"
    autonomy_level: int = field(default_factory=lambda: settings.autonomy_level)

    @classmethod
    def create(cls, user_input: str, source: str = "chat", request_id: Optional[str] = None) -> "RequestContext":
        """`request_id`, when given, reuses an ALREADY-existing request's
        id instead of minting a new one -- see bind_current_request_id
        below for why a tool handler needs this (Phase 8 part 5)."""
        if request_id:
            return cls(request_id=request_id, user_input=user_input, source=source)
        return cls(user_input=user_input, source=source)


# --- Cross-call-site request correlation (Phase 8 part 5) --------------
#
# tools.registry.ToolSpec.handler is Callable[[dict], str] -- deliberately
# narrow (see tools/registry.py's own docstring), so a tool handler (e.g.
# consult_coworker_agent) has no parameter through which the OUTER
# request's RequestContext could be passed. Before this, that handler had
# to mint a brand-new RequestContext with its own random id, so an
# agent's logs never correlated back to the request that triggered them.
# A contextvar solves this without touching ToolSpec's signature or
# tools.registry.dispatch() at all: agent/executor.py binds the current
# request's id once, near the top of execute_task_stream, and anything
# running later in that same call stack (including deep inside a tool
# handler) can read it back. This is synchronous-call-stack-scoped, not
# global -- a concurrent request in another thread gets its own copy
# (contextvars.Context is per-thread by default), so this can't leak one
# request's id into an unrelated one running at the same time.
_current_request_id: "contextvars.ContextVar[Optional[str]]" = contextvars.ContextVar(
    "current_request_id", default=None,
)


def bind_current_request_id(request_id: str) -> contextvars.Token:
    return _current_request_id.set(request_id)


def unbind_current_request_id(token: contextvars.Token) -> None:
    _current_request_id.reset(token)


def get_current_request_id() -> Optional[str]:
    return _current_request_id.get()
