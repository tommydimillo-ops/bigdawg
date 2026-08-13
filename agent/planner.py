"""A lightweight planner for genuinely multi-step requests -- separate
from execution on purpose:

  Planner:    "What should happen?"    (produces structured steps)
  Executor:   "Actually do it."        (agent/executor.py's existing,
                                         unmodified tool loop)
  Autonomy:   "Is it allowed?"         (agent/autonomy.py)

The planner never executes a tool itself -- it has no access to
tools.registry.dispatch and never imports agent.executor. It only
produces a Plan; agent/executor.py decides whether to create one at all
(via is_complex()) and, if so, includes it as guidance for the model,
which then runs through the normal, unchanged tool-calling loop.

Step-completion tracking is a deliberate approximation, not a parallel
step-execution engine: each tool call made while a plan is active
advances the plan's current step by one, capped at the step count. This
won't always line up perfectly with the plan's semantic steps, but it's
simple, deterministic, requires no new model-facing tool, and gives a
real, inspectable progress signal ("N of M done") without needing the
model to self-report -- a fuller "did step 3 specifically succeed"
tracking would need a parallel executor, which this phase explicitly
avoids building.
"""
import json
import re
from dataclasses import dataclass, field
from enum import Enum
from typing import List, Optional

from agent.chat import anthropic_client
from config.settings import settings

MAX_STEPS = 8


class StepStatus(str, Enum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"


@dataclass
class PlanStep:
    step_id: int
    description: str
    required_tools: List[str] = field(default_factory=list)
    dependencies: List[int] = field(default_factory=list)
    status: StepStatus = StepStatus.PENDING
    result: Optional[str] = None
    retry_count: int = 0


@dataclass
class Plan:
    request: str
    steps: List[PlanStep] = field(default_factory=list)

    @property
    def total(self) -> int:
        return len(self.steps)

    @property
    def completed_count(self) -> int:
        return sum(1 for s in self.steps if s.status == StepStatus.COMPLETED)

    @property
    def failed_count(self) -> int:
        return sum(1 for s in self.steps if s.status == StepStatus.FAILED)

    @property
    def skipped_count(self) -> int:
        return sum(1 for s in self.steps if s.status == StepStatus.SKIPPED)

    @property
    def current_step(self) -> Optional[PlanStep]:
        return next(
            (s for s in self.steps if s.status in (StepStatus.PENDING, StepStatus.IN_PROGRESS)),
            None,
        )

    @property
    def is_finished(self) -> bool:
        return self.current_step is None

    def advance(self, failed: bool = False) -> None:
        """Marks the current step completed (or failed) and the next one
        in_progress. No-op if the plan is already finished."""
        step = self.current_step
        if step is None:
            return
        step.status = StepStatus.FAILED if failed else StepStatus.COMPLETED
        next_step = self.current_step
        if next_step is not None:
            next_step.status = StepStatus.IN_PROGRESS

    def as_prompt_text(self) -> str:
        lines = [f"{i + 1}. {step.description}" for i, step in enumerate(self.steps)]
        return "\n".join(lines)

    def progress_text(self) -> str:
        symbols = {
            StepStatus.COMPLETED: "✓",
            StepStatus.IN_PROGRESS: "→",
            StepStatus.PENDING: "○",
            StepStatus.FAILED: "✗",
            StepStatus.SKIPPED: "⊘",
        }
        return "\n".join(f"{symbols[s.status]} {s.description}" for s in self.steps)


# Cheap, no model call -- only requests that look genuinely multi-part
# get the extra planning model call at all, so ordinary conversation
# stays on the fast path.
_SEQUENCING_SIGNALS = re.compile(
    r"\band then\b|\bafter that\b|\bcompare\b|\band also\b|\bstep by step\b",
    re.IGNORECASE,
)


def is_complex(request: str) -> bool:
    if not request or not request.strip():
        return False
    if len(request.split()) > 30:
        return True
    if len(re.findall(r"\band\b", request, re.IGNORECASE)) >= 2:
        return True
    return bool(_SEQUENCING_SIGNALS.search(request))


def _planner_system_prompt() -> str:
    import tools.schemas  # noqa: F401 -- populates tools.registry as a side effect
    from tools.registry import all_names

    tool_names = ", ".join(sorted(all_names()))

    return (
        "Break the user's request into a short, ordered list of concrete "
        "steps needed to fully satisfy it. Reply with ONLY a JSON array, "
        "no other text, where each item is "
        '{"description": "...", "required_tools": ["tool_name", ...]}. '
        f"required_tools must only name tools from this real list (or be "
        f"empty if none clearly apply): {tool_names}. "
        f"Use at most {MAX_STEPS} steps -- combine related work into one "
        "step rather than over-splitting. If the request is actually "
        "simple enough to need only one step, return a single-item array."
    )


def create_plan(request: str) -> Plan:
    """One extra lightweight model call to decompose the request into
    structured steps. Falls back to a single-step plan (not an
    exception) if the model doesn't return parseable JSON -- a
    malformed plan should degrade to "treat it as one step", not break
    the request."""

    try:
        response = anthropic_client.messages.create(
            model=settings.default_model,
            max_tokens=1024,
            system=_planner_system_prompt(),
            messages=[{"role": "user", "content": request}],
        )
        text = "".join(block.text for block in response.content if block.type == "text").strip()
        text = re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.MULTILINE).strip()
        raw_steps = json.loads(text)

        steps = [
            PlanStep(
                step_id=i + 1,
                description=str(item.get("description", "")).strip() or f"Step {i + 1}",
                required_tools=list(item.get("required_tools", []) or []),
            )
            for i, item in enumerate(raw_steps[:MAX_STEPS])
        ]
        if not steps:
            raise ValueError("empty plan")

    except Exception:
        steps = [PlanStep(step_id=1, description=request)]

    if steps:
        steps[0].status = StepStatus.IN_PROGRESS

    return Plan(request=request, steps=steps)
