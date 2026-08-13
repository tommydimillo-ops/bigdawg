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
import time
import uuid
from dataclasses import dataclass, field

from config.settings import settings


@dataclass
class RequestContext:
    request_id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    timestamp: float = field(default_factory=time.time)
    user_input: str = ""
    source: str = "chat"
    autonomy_level: int = field(default_factory=lambda: settings.autonomy_level)

    @classmethod
    def create(cls, user_input: str, source: str = "chat") -> "RequestContext":
        return cls(user_input=user_input, source=source)
