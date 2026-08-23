# Jarvis — Architecture

This documents the system **as it actually exists in code today**. Anything
not implemented is explicitly marked `PLANNED` / `NOT IMPLEMENTED` — nothing
in this file describes aspirational architecture as if it were real.

Supersedes `docs/ARCHITECTURE.md` (Phase 2-era, kept for history). See
`docs/ADDING_A_TOOL.md` for the step-by-step guide to adding a tool — still
accurate, not duplicated here.

---

## 1. System architecture

Jarvis is a single Python backend (`agent/`, `tools/`, `voice/`, `config/`,
`database/`) driven by three separate front-end processes that all funnel
into the same core loop. There is no client/server split, no external
database, and no message queue — everything is local processes talking to
local JSON files plus outbound HTTPS calls to Anthropic/OpenAI/xAI/
Perplexity/Apple frameworks (xAI/Perplexity optional, Phase 9 Milestone 2
— see §5).

```
┌─────────────┐   ┌──────────────────┐   ┌───────────────────────┐
│ app.py       │   │ ui/menu_bar.py   │   │ agent/scheduler_daemon│
│ (Streamlit   │   │ (native macOS    │   │ .py (standalone cron, │
│  chat UI)    │   │  menu-bar app,   │   │  redundant with menu- │
│              │   │  voice-first)    │   │  bar's own scheduler) │
└──────┬───────┘   └────────┬─────────┘   └───────────┬───────────┘
       │                    │                          │
       └────────────────────┼──────────────────────────┘
                             ▼
              agent/executor.py: execute_task_stream()
                  (the one orchestrator, all entry
                   points call this and nothing else)
                             │
        ┌────────────────────┼────────────────────────┐
        ▼                    ▼                         ▼
  agent/model_router   tools/registry.py         agent/agents/
  (task-aware, cost-   (64 tools, permission     manager.py
   ranked, 4 providers: levels, dispatch)         (coworker agents,
   Anthropic/OpenAI/                               subprocess-isolated)
   xAI/Perplexity)
                             │
        ┌────────────────────┼────────────────────────┐
        ▼                    ▼                         ▼
  agent/memory/        agent/skills/              agent/usage.py
  (structured,          (SKILL.md files,           (per-call token/
   file-backed)          prompt-context only)        cost accounting)
```

Everything persists to flat JSON files under
`~/Library/Application Support/CampusPilot/` (one file per concern —
memory, execution history, usage history, scheduled tasks, quiet-mode
state, conversation store, audit log). Writes use a
tmp-file-then-`os.replace` pattern, several with `fcntl` locks, for
crash-safety and cross-process concurrency — there is **no database
server**, this is deliberate (see `docs/ARCHITECTURE.md`'s original
note: "no vector database or external database server").

`agent/scheduler_daemon.py` and `ui/menu_bar.py`'s built-in poller both
run the same scheduled-task logic independently (the diagram's
"redundant with menu-bar's own scheduler" label describes the mechanism,
not a bug) — each used to execute every due task if both processes ran
at once, a real, previously-documented lifecycle risk. `agent/
scheduler_lock.py` now arbitrates: a non-blocking, kernel-managed
`fcntl.flock` on a dedicated lock file, re-attempted every poll tick, so
only the tick's lock-winner executes due tasks; the other skips that
tick entirely (no execution, no `mark_run`, no UI/voice-state touch) and
logs a `scheduler_lock_deferred` diagnostic. Both deployment modes
(menu-bar always-on, `scheduler_daemon.py` headless fallback) still
exist and may now run together safely.

## 2. Main application flow

All four entry points converge on one function:

```
execute_task_stream(request, history, source)
  1. RequestContext.create()          -- request_id, timestamp, autonomy_level
     bind_current_request_id()        -- contextvar, so anything deeper in the
                                          call stack (a tool handler, a
                                          subprocess-isolated agent) can
                                          recover this request's id without
                                          it being threaded through every
                                          function signature
  2. agent.delegation.decide()        -- does a Skill apply? (prompt-context
                                          only, never executes anything)
  3. agent.agents.router.route()      -- PURE routing signal for attribution
                                          only (dashboard/history) -- does
                                          NOT execute a coworker agent
  4. agent.planner.is_complex()       -- genuinely multi-step? create a Plan
  5. classify_task() + build_fallback_chain()   -- Phase 9 Milestone 2:
     deterministic task classification, then an ordered N-provider
     candidate list (agent/model_router.py, see §5) -- with only
     Anthropic/OpenAI configured (the original, still-default state)
     this returns exactly [anthropic, openai], so behavior is unchanged
     from the pre-Milestone-2 hardcoded cascade in that common case
  6. for each candidate in the chain, in order (never simultaneously):
       anthropic  -> _run_claude_loop_stream()
       perplexity -> _run_perplexity_agent_loop_stream() (single-shot,
                     no tool registry -- see §5)
       otherwise  -> _run_openai_compatible_loop_stream() (OpenAI, xAI)
     each loop (max_agent_steps, default 8):
       check cancellation, check usage limits (agent.usage.
         check_request_limits)
       call the provider with the full tool list + system prompt
         (Perplexity: no tool list -- see §5)
       if tool_use: _run_tool_batch -> _run_tool (per tool)
         -> permission/confirmation checks (agent.autonomy)
         -> tools.registry.dispatch(name, input)
         -> agent.audit.log_action (security record)
         -> agent.observability.log_event (diagnostics)
         -> agent.usage.record_llm_usage (if the tool itself made an
            LLM/audio call, e.g. computer_locate, computer_see)
       else: return the model's text answer
     on a candidate's failure (nothing streamed yet for this request):
       advance to the next candidate in the chain; on the last
       candidate's failure, return an error instead
  7. record_llm_usage() after every provider call that returns a real
     usage object
  8. execution_history.record_completed/failed/cancelled()
  9. unbind_current_request_id()
```

Both the streaming path (used by every live UI) and the non-streaming
`execute_task()` (used by the scheduler) share this exact code — there is
one agent loop, not two.

## 3. Orchestrator

**`agent/executor.py`** is the orchestrator. It owns: the provider
try/fallback sequence, the tool-call loop, cancellation checkpoints, usage-
limit checkpoints, permission/confirmation enforcement (delegated to
`agent/autonomy.py`), and execution-history/state recording. It does not
itself decide *what* a request needs — that's spread across smaller,
single-purpose deciders it calls into (delegation, agent routing, planner,
autonomy), each pure/deterministic where the decision doesn't require the
model's own judgment. Since Phase 9 M4.2, it's also the single point that
calls into `agent/history_capture.py` (§12b) — a user-turn capture near
the top of `execute_task_stream()`, an assistant-turn capture at every
terminal path — deterministic application infrastructure, not something
any tool call or model decision controls.

**`agent/brain.py`** builds the system prompt (`BASE_SYSTEM_PROMPT` — a
large, hand-tuned block of tool-usage guidance, tone rules, and behavioral
requirements) and derives the `TOOLS` list from `tools/registry.py` at
import time. Also assembles per-request context: user profile
(`agent/context.py`'s `build_profile_context`), relevance-ranked pattern
memories, always-included standing rules (`agent/lessons.py`), and an
attached skill's instructions if one matched.

## 4. Agents (coworker agents — Phase 7/8)

**Two different meanings of "agent" in this codebase — don't conflate
them:**
- The **main loop** (Claude/GPT running the tool loop in
  `agent/executor.py`) is itself "the agent" in the everyday sense.
- **Coworker agents** (`agent/agents/`) are separate, specialized workers
  the main loop can *hand a task to* via one tool call
  (`consult_coworker_agent`) — a delegation mechanism, not a second
  copy of the main loop.

| Agent | File | Real capability today |
|---|---|---|
| `research` | `agent/agents/research.py` | Wraps `agent/research_agent.py` — real multi-step web research (its own small tool loop: `open_browser`, `read_document`), returns a synthesized answer. The **only** coworker that directly makes an LLM call — see "ResearchAgent's M2 routing" below |
| `memory` | `agent/agents/memory.py` | Wraps `agent/memory_agent.py`'s `remember`/`recall` — real memory read/write, no model call |
| `coding` | `agent/agents/coding.py` | **Stub.** Returns `metadata={"deferred_to_executor": True}` — no real code-editing capability, no model call. Do not treat this as functional; see `ROADMAP.md`'s Phase 10 |
| `qa` | `agent/agents/qa.py` | Real, but narrow: runs this project's own test suite read-only (`python -m unittest discover`) when the task text matches test-running phrasing, no model call; everything else defers |

**Execution model (Phase 8 Part 4, hardened in Phase 9 Milestone 3):**
real execution goes through `agent/agents/manager.py`'s `execute_agent()`,
which spawns `python -m agent.agents.worker` as a genuine OS subprocess.
Timeout enforcement (`settings.agent_timeout_seconds`) and cancellation
now share one mechanism, `_run_agent_subprocess()` — a `Popen`-based
poll loop (not `subprocess.run`) that wakes every 0.5s to check either
condition:
- **Timeout**: `SIGKILL` immediately — the same real, unweakened
  guarantee as before (verified live: a subprocess mid-way through a
  real ~5-7s test-suite run was killed at 1.01s with the process
  confirmed gone, not orphaned).
- **Cooperative cancellation** (the parent request was cancelled while
  this subprocess was already running): `SIGTERM` first, a bounded
  ~3s grace period to exit cleanly, `SIGKILL` only if it doesn't — this
  project's general "graceful before forced" preference. Every exit path
  (normal completion, timeout, cancellation, or an unexpected exception)
  reaps the child via `communicate()`/`wait()` before returning, so no
  orphan/zombie can result. Verified against a real, separate OS process
  (not a mock) in `tests/test_agents_manager.py`.

`agent/agents/manager.py` also has a second function, `route_and_execute()`
— pure-routing-then-in-process-dispatch, used only by
`agent/executor.py`'s attribution-only routing call and by its own test
suite (which depends on registering fake, in-process Agent instances a
subprocess couldn't see). **The real, live tool (`consult_coworker_agent`)
calls `execute_agent()`, not `route_and_execute()`.**

`MAX_AGENT_DEPTH = 1` — an agent's own `execute()` must never itself
trigger another agent consultation; enforced structurally in
`execute_agent()`, not by convention. Phase 9 Milestone 3's bounded
parallel delegation (below) doesn't touch this guard at all: every
subtask in a batch is still a direct, depth-unchanged call from Jarvis
through `execute_agent()`, never an agent calling another agent.

### Bounded parallel coworker delegation (Phase 9 Milestone 3)

```
Jarvis (model) judges 2+ subtasks genuinely independent
        ↓
delegate_parallel_tasks tool (new; permission_level=1, side_effect=True;
                               deliberately NOT parallel_safe -- see below)
        ↓
execute_agents_parallel() -- agent/agents/manager.py
  ├─ MAX_AGENT_DEPTH guard
  ├─ batch-size guard: batch > settings.max_parallel_agents (3) is
  │  REJECTED whole -- no subprocess spawned, never silently truncated
  ├─ cooperative-cancellation check
  ├─ global-budget pre-flight check (agent/provider_budget.py)
  ↓
bounded ThreadPoolExecutor(max_workers=len(tasks)) -- tasks<=3 by the
  guard above, so this is never actually unbounded
  ↓
each subtask -> execute_agent() UNCHANGED -- same subprocess isolation,
  same per-task depth/registered/enabled/cancellation checks; no second
  dispatch path exists alongside this one
  ↓
failed (non-cancelled) subtasks get one bounded retry
  (settings.max_agent_batch_retries = 1)
  ↓
agent.verification.verify_agent_result() per subtask
  ↓
BatchStatus: ALL_SUCCEEDED / PARTIAL (only optional subtasks failed
  verification) / FAILED (a required subtask did)
  ↓
AgentBatchResult -> ExecutionState (active_agents/completed_agents/
  failed_agents/verification_status, additive alongside the pre-existing
  singular active_agent/agent_task/agent_status/agents_used fields) ->
  formatted report back to Jarvis
```

`delegate_parallel_tasks` is deliberately **not** marked `parallel_safe`
in `tools/registry.py`: that flag already exists for a different purpose
(`agent/executor.py`'s `_run_tool_batch` running several *read-only*
tool calls concurrently within one model turn — every existing
`parallel_safe` tool is side-effect-free). If this tool were also
`parallel_safe`, the model could call it more than once in one turn and
`_run_tool_batch`'s own concurrency mechanism would multiply the real
concurrent-subprocess count past the ceiling this milestone exists to
enforce. The single-task `consult_coworker_agent` tool is completely
unmodified by any of this — a caller wanting strictly sequential
delegation simply keeps using it; dependent, order-sensitive subtasks
were never routed through the parallel path in the first place (deciding
*whether* subtasks are independent enough to batch is the model's
judgment, constrained by `delegate_parallel_tasks`'s own tool
description; deciding *how many* can actually run at once, and whether
any of it is safe to start at all, is code-enforced, never model-decided
— the same "model picks what, code decides whether it's allowed"
separation this project already applies to permissions and routing).

**Verification** (`agent/verification.py`'s new `verify_agent_result()`):
evaluates a coworker's *actual* result rather than trusting
`success=True` at face value — checked in order: cancellation, explicit
`success=False`, an agent-reported `verification_status == "failed"`
(e.g. QAAgent's own test-suite check reporting failing tests even though
the QA *run itself* didn't crash), then the same generic failure-marker
string check `agent/verification.py`'s tool-level `verify()` already
uses, plus one agent-specific heuristic today: ResearchAgent's answer is
flagged unverified if it contains no source/URL evidence at all (cheap
regex, no extra model call — not proof the sources are real, only that
the answer at least looks sourced). **Deliberately not extended to
FILES/BROWSER-shaped checks** ("does the expected file exist," "does the
resulting page show X") this milestone — no current coworker agent
produces that shape of result to check yet (CodingAgent and QAAgent's
non-test-suite path both still fully defer to the ordinary executor); see
`ROADMAP.md`'s "QAAgent expansion" entry.

**Cross-process audit-log safety**: `agent/audit.py`'s `log_action` — the
security/action log every coworker subprocess also writes to (e.g.
`agent/research_agent.py`'s `_run_tool` logs every page it visits) —
gained an `fcntl.flock` around its append write. Before Milestone 3, only
one coworker subprocess ever ran at a time in practice, so the
pre-existing in-process `threading.Lock` was sufficient; bounded parallel
dispatch makes genuinely concurrent OS-process writers to the same file a
real scenario, so the same cross-process-lock convention already used
elsewhere (`agent/usage.py`, `agent/scheduler_lock.py`,
`agent/browser_lock.py`) was applied here too.

**ResearchAgent's M2 routing**: `agent/research_agent.py` — the only
coworker that directly calls a model — now calls
`agent/task_classifier.py`'s `classify()` and
`agent/model_router.py`'s `build_fallback_chain()` (the same primitives
`agent/executor.py` uses for the outer request) instead of always calling
Anthropic's default model directly. It therefore inherits capability/
health/budget filtering, cost-aware tiering, and cross-provider fallback
exactly like an ordinary request does. Dispatch is shaped per provider
(Anthropic's own tool-calling loop; a new OpenAI-compatible-shaped loop
for OpenAI/xAI; a single grounded Agent API call, no tool loop, for
Perplexity) and falls through to the next candidate only on a raised
exception — safe to restart from scratch specifically because
ResearchAgent's own tools (`open_browser`, `read_document`) are
read-only, unlike the main executor's `PartialToolExecution` caution
around side-effecting tools. **Technical-debt note, intentional and
documented, not scheduled for this pass**: `_call_perplexity_agent`/
`_client_for_provider`/`_extract_agent_api_text` are small, local
re-implementations inside `agent/research_agent.py`, not imports from
`agent/executor.py`'s same-named functions — `agent/executor.py` already
imports `agent.agents` (to populate the coworker registry), and
`agent.agents.research` imports `agent.research_agent`, so importing
`agent.executor` from `agent/research_agent.py` would create a real
import cycle. Each duplicated function is a few lines with no shared
mutable state; extracting a shared module is a legitimate future
cleanup, not required for correctness, and was deliberately not done
during this finalization pass.

## 5. Model router

**`agent/model_router.py`** implements Phase 9 Milestone 2's task-aware,
multi-provider routing. The original static Phase 1 interface
(`primary_choice()`/`fallback_choice()`/`select()`) still exists and is
still used verbatim as the fallback behavior when task-aware routing is
off or every candidate gets filtered out — but the real entry point
`agent/executor.py` calls today is `build_fallback_chain()`, which
returns an ordered list of `ModelChoice`s to try in sequence:

```
Task
 ↓
Deterministic task classification (agent/task_classifier.py)
 ↓
TaskRequirements (task type + vision/current-web/large-context/tool
                  needs + latency/quality/cost priority flags)
 ↓
Capability filter   — can this provider even do the task?
 ↓
Configured/health filter — is it configured, and not in a post-failure
                            cooldown (agent/provider_health.py)?
 ↓
Budget filter        — is it within its daily spend ceiling
                        (agent/provider_budget.py)?
 ↓
Cost/quality ranking — cheapest reliable candidate that still satisfies
                        the task's priority flags
 ↓
Primary provider (call one, not several — never call multiple providers
                   simultaneously to compare answers)
 ↓
Layered fallback chain — on failure, try the next candidate in order
```

This filter order is fixed and load-bearing, never reordered: capability
→ configured/healthy → budget → cost/quality ranking. Routing can't
discover mid-call that the cheapest candidate simply can't do the task,
because incapable candidates are removed first. Task classification
itself is deterministic (keyword/pattern matching, no model call) —
consistent with this project's standing "never let a model decide a
routing or permission outcome" rule (`agent/autonomy.py`,
`agent/delegation.py`, `agent/skills/router.py`, `agent/agents/router.py`
all follow the same rule).

**Providers**: Anthropic and OpenAI are required (always configured);
xAI and Perplexity are optional, degrading to `None`/filtered-out rather
than raising when their API key is absent. Current default model tiers
(`config/settings.py`, currency-reviewed against each provider's official
documentation immediately before the Milestone 2 commit):

| Provider | Tiers |
|---|---|
| Anthropic | `claude-sonnet-5` (primary), `claude-haiku-4-5-20251001` (planner/vision-fallback) |
| OpenAI | `gpt-5.6-luna` (economy), `gpt-5.6-terra` (balanced/fallback), `gpt-5.6-sol` (quality) |
| xAI | `grok-4.3` (economy/default), `grok-4.6` (quality, only for `quality_priority` tasks) |
| Perplexity | Agent API preset `"low"` — not a flat-rate model ID; see below |

**Perplexity is architecturally distinct from the other three providers**
and this is deliberate, not a gap: its Agent API
(`agent/executor.py`'s `_call_perplexity_agent`) has a genuinely
different request/response shape (`input`/`output`, not
`messages`/`choices`) and is called directly via `httpx.post`, not
through `_run_openai_compatible_loop`. Perplexity is used narrowly and
single-shot — one grounded research query in, one grounded answer back —
and is **never handed Jarvis's tool registry**, so it can never become a
second orchestrator; a request routed to Perplexity cannot also perform a
Jarvis tool action in the same turn. This is the same
"providers/skills/agents never bypass `tools.registry`/`agent.autonomy`"
rule this file states elsewhere, applied to a provider whose own API
happens to have its own (irrelevant, unused) tool-calling mechanism.

**What routing does NOT change**: tool permission levels
(`tools/registry.py`) and the confirmation-required decision
(`agent/autonomy.py`) are completely independent of which provider
answers a request — routing picks a model, never a permission. Every
provider is independently replaceable (swapping a model ID or adding a
fifth provider doesn't touch `tools.registry`/`agent.autonomy`). Jarvis
itself remains the only orchestrator regardless of which provider is
currently answering.

**Cost controls** (`agent/usage.py`/`agent/provider_budget.py`): a small,
centrally maintained, dated pricing catalogue (not a live pricing API —
would add a network dependency to routing) with a conservative
`_DEFAULT_TOKEN_PRICE` fallback for unpriced/unrecognized models, never a
silent zero. `provider_budget.py` reuses `agent/usage.py`'s existing
`cost_since()`/`cost_today()` aggregation for same-day per-provider and
global spend ceilings rather than a new store.

**Explicitly NOT part of this router**: `vision_model`,
`vision_fallback_model`, `transcription_model`, `tts_model`, and
`planner_model` remain separate, hardcoded, purpose-specific per-call-site
assignments (`config/settings.py`) — never routed candidates. Of these,
`vision_model` (`gpt-5.6-terra`) was currency-reviewed alongside the
router's own tiers (same stale-`gpt-5` problem, same fix, since it's the
same OpenAI chat-model family), but that review didn't change its
hardcoded-assignment status — `tools/vision.py` still reads
`settings.vision_model` directly, with no capability/health/budget
filtering. `transcription_model`/`tts_model` are audio-specific models in
a different product line entirely and were not touched by the Milestone 2
currency review.

## 6. Memory

**`agent/memory/`** is the single structured memory store — one `Memory`
dataclass (`models.py`) for every kind of thing Jarvis remembers, typed by
`MemoryType` (profile/preference/fact/lesson/pattern/conversation/task/
event), with `Confidence` (how the memory came to exist — user-explicit vs.
model-inferred, so an inference is never silently treated as something the
user actually said) and `Importance`. `manager.py` is the only public
interface (`remember`, `recall`, `search`, `search_scored`, `update`,
`forget`, `list_all`, `summarize`); `store.py` persists through
`database/memory.py`'s existing JSON file, `safety.py` filters unsafe
writes (credential-shaped content, prompt-injection-shaped content) before
anything is stored.

**Supersession**: a new memory about the same subject as an existing
active one of a supersedable type (preference/fact/lesson/profile —
deliberately excludes pattern) marks the old one inactive and links it,
instead of both persisting as silently contradictory. Same-subject
detection is a cheap word-overlap heuristic (`_same_subject`), not
embeddings.

**Retrieval**: `agent/context.py` does deterministic, relevance-ranked,
budget-limited retrieval (`config.settings.context_memory_budget`, default
6) of PATTERN-type memories for the current request — keyword overlap +
importance + recency, no vector search, no embeddings. LESSON-type
memories (standing rules) are always included in full regardless of
budget, since they're hard requirements, not contextual background.
FACT/PREFERENCE memories are only retrieved on demand (the
`recall_facts` tool), never auto-injected.

**Backward-compatible wrappers** (thin, preserve old call signatures so
nothing that already imports them needed to change): `agent/lessons.py`
(standing rules), `agent/patterns.py` (inferred communication patterns),
`agent/memory_agent.py` (generic key-based fact storage, used by
`remember_fact`/`recall_facts`).

**Separate, unrelated systems that are NOT part of `agent/memory/`**:
- `agent/personal_context.py` — privacy-preserving local file/Photos
  *aggregate* inventory (counts and categories, never file contents,
  never raw filenames, never image pixels/face data). Never calls an AI
  provider.
- `agent/conversation_store.py` — live chat message history for the
  Streamlit multi-device UI, not long-term memory.
- `agent/execution_history.py` — bounded metadata about past *requests*
  (status, duration, tools used), not personal facts.
- `agent/history_store.py` (Phase 9 M4.1) / `agent/history_capture.py`
  (Phase 9 M4.2) — durable, searchable *evidence* of what was actually
  said, when; never superseded the way a memory is. See §12a/§12b for
  the full HISTORY-vs-MEMORY boundary and each module's own scope.
  Neither module imports from or writes to `agent/memory/`, and the
  reverse is expected to remain true.

The real, live memory store is the absolute,
`~/Library/Application Support/CampusPilot/`-based path in
`database/memory.py`; a dead repo-root `memory.json` fossil from before
that path fix was removed in a repository cleanup pass (see
`CHANGELOG.md`).

## 7. Tools

**`tools/registry.py`** is the single source of truth for every tool: name,
description, JSON schema, `permission_level` (0-5), and four gating flags
(`requires_live_confirmation`, `unattended_allowed`, `side_effect`,
`parallel_safe`). 64 tools as of Graphify G1 (re-verify with
`len(tools.registry.all_names())` rather than trusting this number
blindly — it drifts every time a tool is added), grouped by theme into
`tools/schemas/*.py` (`agents`, `browsing`, `computer_use`,
`execution_control`, `graphify`, `logins_and_email`,
`memory_and_learning`, `obsidian`, `openclaw`, `productivity`,
`reasoning`, `scheduling`, `skills`, `system`) — each calls
`register(ToolSpec(...))` at import time.
`agent/brain.py`'s `TOOLS` list, `agent/permissions.py`'s lookups, and
`agent/executor.py`'s dispatch are all *derived* from this registry, not
separately maintained. See `docs/ADDING_A_TOOL.md`.

Permission levels: `0` read-only, `1` safe local action, `2` modifies
files/executes code, `3` external communication, `4` financial (reserved,
none yet), `5` destructive/system.

**Notable individual tool implementations** (`tools/*.py`, one file per
capability area): `browser.py` (Playwright-driven, one shared Chrome
profile per process), `computer.py`/`computer_use.py` (real mouse/
keyboard/screen control via `pyautogui`, vision-model-read), `vision.py`
(screenshot description, OpenAI primary/Claude fallback), `sandbox_python.py`
(macOS `sandbox-exec` profile — no network, no writes outside a scratch
dir), `reminders.py`/`calendar.py`/`notes.py` (AppleScript against native
macOS apps), `weather.py`, `music.py`, `agenda.py`, `credential_store.py`/
`manage_logins.py`/`manage_secrets.py` (Keychain-backed, CLI-only setup,
deliberately kept out of chat).

**`agent/code_graph.py`** (Graphify G1, `tools/schemas/graphify.py`,
4 tools, all `permission_level=0`): a read-only reader over the locally
generated `graphify-out/graph.json` — a third-party static-analysis
tool's (Graphify, external, `uv`-installed, never a runtime dependency)
structural map of this codebase, parsed with the standard library only,
never by importing `graphifyy` or invoking the `graphify` executable.
Fails closed on staleness (graph must be built at the current git HEAD
with a clean tracked working tree, or `code_graph_status`/
`search_code_graph`/`analyze_code_impact`/`find_code_path` refuse to
analyze); every result is marked `authoritative: False`, with
`source_verification_required: True` on anything touching
`tools/registry.py`/`agent/autonomy.py`/permission or credential code.
Full detail: `docs/GRAPHIFY.md`.

## 8. Skills

**`agent/skills/`** — structured workflow guidance Jarvis can attach as
extra system-prompt context for a matching request. **A skill is data, not
code**: `SKILL.md` files (YAML-style frontmatter + Markdown instructions
body — the same shape as Claude's own Agent Skills), parsed by a
deliberately narrow hand-rolled frontmatter parser (`loader.py`) — never a
general YAML parser, since a skill file is untrusted input. There is no
code path from a skill file to Python execution anywhere in this module.

Loaded from two directories: the repo's own `skills/` (currently
`research`, `document_creation`, `data_analysis` — each one `SKILL.md`)
and a user-local `~/Library/Application Support/CampusPilot/skills/` (drop
in a custom skill without touching the repo).

`agent/skills/router.py` does deterministic keyword/capability-overlap
scoring (`agent/delegation.py`'s `decide()` calls it) — never a model call.
`agent/skills/safety.py` wraps a matched skill's instructions before they
reach a prompt, but the real enforced security boundary is elsewhere: a
skill's instructions can say anything, including "skip confirmation" — it
changes nothing about whether `agent/executor.py`'s `_run_tool` actually
enforces `agent/autonomy.py`'s decision, because that check is code, never
a question asked of the model.

**"Plugins"** — there is no separate plugin system distinct from Skills.
Skills are the closest existing concept (drop-in, file-based, no code
required to extend).

## 9. MCP

**NOT IMPLEMENTED.** No Model Context Protocol client, server, or
integration exists anywhere in this codebase (confirmed by repo-wide
search). Tool integration today is entirely `tools/registry.py`'s
in-process `ToolSpec` mechanism. See `ROADMAP.md`.

## 10. Frontend / UI

Three separate processes, all calling the same orchestrator:

- **`app.py`** — Streamlit web chat. Browser-based voice via the Web
  Speech API, passive/active wake-word mode, multi-device conversation
  sharing (`agent/conversation_store.py`), a "speak replies aloud"
  checkbox (`agent/tts_control.py`'s `speak_interruptible` — plain macOS
  `say`, PID-tracked so a reply from a different device can interrupt).
- **`pages/1_Dashboard.py`** — live status: current execution, recent
  execution history, standing rules/facts, tools by permission level,
  registered coworker agents + recent agent-routed requests, and (Phase
  8 Part 2) a "💰 Jarvis Usage" section — today's requests/tokens/
  estimated cost, by-provider and by-operation breakdowns, top expensive
  requests.
- **`ui/menu_bar.py`** — native macOS menu-bar app (`rumps`), the primary
  voice-first interface. Status icon reflects live state (idle/listening/
  thinking/computer-use-active), native notifications, click-to-ask, and
  runs the background voice-listening loop plus its own built-in
  scheduler loop. Packaged as its own code-signed `.app` bundle
  (`setup_app.py`, py2app alias mode) specifically so it has its own TCC
  identity for microphone/Speech-framework permissions — the shared
  system Python launcher has no way to declare mic usage without editing
  (and breaking the signature of) an Apple-signed file. Its dropdown also
  has an "Estimated Cost" item (alongside Recent Notes/Tasks/Actions),
  read lazily via `agent/usage.py`'s `cost_today()` only when clicked —
  no background timer, and the always-on title/state icon is untouched.
  Fails safely (an "unavailable" alert, not a wrong number) if
  `usage_history.json` can't be read or parsed.

## 11. Backend

The backend *is* `agent/`, `tools/`, `voice/`, `config/`, `database/` —
there is no separate backend service; the three UI processes each embed
the same backend code directly (import, not RPC).

## 12. Database / storage

No database server, no ORM. Every general-purpose store is a flat JSON
file under `~/Library/Application Support/CampusPilot/`: `memory.json`,
`execution_history.json`, `usage_history.json`, `scheduled_tasks.json`,
`quiet_mode.json`, `conversation.json`, `jarvis_state.json`,
`personal_context.json`, `audit.log` (JSON-lines), `tts.pid`. Writes use
tmp-file-then-`os.replace`; several add an `fcntl.flock`-guarded
read-modify-write cycle for cross-process safety (memory, execution
history, usage history, scheduled tasks).

Two narrow, deliberate `sqlite3` exceptions exist, both documented so
neither is mistaken for a general SQL layer: `agent/personal_context.py`
opens the macOS Photos library's own database read-only (`mode=ro` URI)
for aggregate stats only; `agent/history_store.py` (§12a) owns a real,
Jarvis-written SQLite database — the only one in this project.

### 12a. History store (Phase 9 M4.1)

`agent/history_store.py` owns `~/Library/Application
Support/CampusPilot/history.db`, a dedicated SQLite database — the
implementation slice that follows the Phase 9 M4A audit's storage-boundary
recommendation. **As of M4.1, this module is built but not wired in**: no
code path calls it yet (see below).

**HISTORY vs MEMORY, the boundary that governs this design**: memory
(`agent/memory/`, §6) is *distilled, superseding* — "the user prefers X"
replaces "the user prefers Y" because only one can currently be true.
History is *append-only evidence* — what was actually said, when, is
never superseded by a later, unrelated turn; each row is independently
true forever. Running conversation content through memory's same-subject
supersession logic would be actively wrong for this reason, which is why
the two modules never import from each other.

**Why a dedicated SQLite database instead of another JSON file**: this is
the first store in the project that needs full-text search over
free-form conversation content at retrieval time; grep-equivalent
scanning of an ever-growing JSON file doesn't scale the way SQLite's
FTS5 extension does, and FTS5 needs a real SQLite database to attach to.

**Schema (`PRAGMA user_version = 1`)** — two canonical tables, one
derived index:
- `history_session(session_id PK, source, started_at, ended_at)`
- `history_turn(turn_id PK, session_id FK, request_id, role, content,
  created_at, redacted, truncated)`, indexed on `session_id`,
  `request_id`, `created_at`, `role`, plus a partial unique index on
  `(request_id, role) WHERE request_id IS NOT NULL` giving idempotent
  writes (a retried request never double-records a turn) without
  deduplicating `request_id IS NULL` backfill rows against each other.
- `history_turn_fts` — an external-content FTS5 table
  (`content='history_turn'`, `content_rowid='turn_id'`, `tokenize='porter
  unicode61'`) kept in sync purely by `AFTER INSERT/UPDATE/DELETE`
  triggers. Canonical rows are the only authoritative data; losing the
  FTS index would only lose search capability, never real data.

**Connection policy**: stdlib `sqlite3` only (no new dependency),
connection-per-operation (never one shared global handle, matching this
project's load/modify/save-per-call convention everywhere else). Every
write connection sets `foreign_keys=ON`, `journal_mode=WAL`, a bounded
`busy_timeout`, `synchronous=FULL` (reliability over the throughput this
project has no need for), and `secure_delete=ON`. Read-only operations
(`history_status`, `search_history`) open via a `file:...?mode=ro` URI —
the same pattern `agent/personal_context.py` established — which
structurally cannot create a missing database file, so the read-only
surface provably has no creation side effects.

**Two-layer secure deletion**: a single hardening pass over M4.1 found
that the core pragma above is not sufficient on its own. Two
independent layers are both required:
- **Core `PRAGMA secure_delete=ON`** — per-*connection*, not persisted
  in the database file the way `journal_mode` is; every write connection
  this module opens sets it explicitly (see `_connect_writable`).
  Empirically verified (not assumed) to scrub a deleted row's bytes from
  the raw file, even without `VACUUM`, only a `wal_checkpoint(TRUNCATE)`,
  and compatible with WAL. This layer covers ordinary SQLite table
  storage — it does **not** by itself guarantee an FTS5 index's own
  b-tree segments stop retaining old term data after a logical
  delete/update, which official SQLite documentation calls out
  explicitly.
- **FTS5's own `secure-delete` config** — a property of the
  `history_turn_fts` table itself (persisted in its `_config` shadow
  table, not the connection — set once at schema creation via
  `INSERT INTO history_turn_fts(history_turn_fts, rank) VALUES
  ('secure-delete', 1)`, it survives every future reopen without being
  set again). Requires SQLite >= 3.42.0. Enabled inside the same
  `_ensure_schema()` transaction that creates the table, via real
  feature probing (attempting the actual command and handling failure)
  rather than a version-string comparison — an unrecognized FTS5
  special command was verified to raise `sqlite3.OperationalError`, and
  that failure is translated into `HistoryUnsupportedRuntime` (a new,
  distinct `HistoryStoreError` subclass) and fails the whole
  schema-initialization transaction closed. A history database is never
  created with an FTS index that lacks this invariant, regardless of
  runtime — there is no silent degradation path.
- Both layers were verified together against a synthetic token pushed
  through the real `record_turn()` → update/delete → FTS trigger path,
  followed by `wal_checkpoint(TRUNCATE)` and a raw byte-scan of the
  `.db`/`-wal`/`-shm` files. This is a SQLite/FTS logical and file-level
  deletion claim only — not a claim of cryptographic secure erasure
  against underlying storage hardware (SSD wear-leveling, etc.), which
  no SQLite-level pragma can guarantee.

**Redaction**: reuses `agent.memory.safety.redact_secrets()` — no
separate secret-pattern list. Every turn is redacted, then length-bounded
(4000 chars) before it ever reaches `sqlite3.execute()`; the unredacted
form is never persisted. `redacted`/`truncated` flags on each row record
whether either happened.

**Safe search**: `search_history()` never lets caller text reach FTS5's
`MATCH` syntax unescaped — terms are extracted, individually quoted, and
joined as a literal AND expression, neutralizing FTS5 operator/column/
wildcard/exclusion syntax. Ranking is `bm25()` ascending (FTS5's bm25
returns more-negative values for better matches) with `created_at DESC`
as a tie-breaker only — no weighted BM25/recency formula is invented
here; that's deferred to a later milestone once real retrieval quality
can be observed.

**What M4.1 deliberately did not do yet** (each a distinct, later,
explicitly-gated milestone — M4.2 below implements the first of these):
no backfill of the existing `conversation.json`; no Jarvis-facing
ToolSpec (`search_history`/`history_status` are still plain Python
functions, not registered tools); no proactive context injection; no
automatic age-based deletion (retention defaults to indefinite).

### 12b. History capture (Phase 9 M4.2)

`agent/history_capture.py` is the *only* place that decides when a turn
is recorded and which session it belongs to — `agent/history_store.py`
(§12a) has no opinion about either, by design. It is called
unconditionally from two fixed points in `agent/executor.py`'s
`execute_task_stream()`: once near the very top (before delegation,
agent routing, or `is_complex()`'s own planning-model call — the
earliest point a real `request_id` exists), and once at every terminal
path (normal completion, cancellation, `PartialToolExecution`, and both
provider-failure branches). **The LLM never decides whether a turn is
written** — there is no write-history ToolSpec, and none of the four
terminal points depend on anything a model chose to say or do; they are
plain control-flow branches `execute_task_stream()`'s own loop already
had.

**Assistant-turn accumulation without breaking streaming**: a plain list
(`captured_chunks`) is appended to at the exact same point every real
chunk is already `yield`ed — never a separate buffering pass, never
delaying output. At whichever terminal point is reached, the accumulated
text (if any) is joined and recorded as one assistant turn. A request
that yielded zero visible text records no assistant turn at all — never
a fabricated placeholder, matching M4.1's "evidence, not a requirement
that every request forms a perfect pair" framing.

**Session lifecycle** (three sources, three different lifetimes): `chat`
and `voice` each get one process-lifetime session, cached in a
module-level dict and reused across every turn on that source for the
life of the process — deliberately *not* merged with each other even
when both occur in the same process (e.g. `app.py`'s Streamlit UI can
emit both `source="chat"` typed turns and `source="voice"` mic-input
turns from the same run). `scheduled` gets a brand-new session on every
single top-level request, never cached — one request, one session. A
small `request_id -> session_id` map (populated by the user-turn
capture, consumed by the assistant-turn capture) guarantees both turns
of one request land in the same session even for `scheduled`, which has
no per-source cache to fall back on. No cross-process or cross-restart
session continuation exists.

**Failure isolation is structural, not incidental**: both public
functions (`capture_user_turn`, `capture_assistant_turn`) catch every
exception internally and never raise — a database lock, a corrupt
schema, an unsupported source, or any other `history_store` failure is
logged as a bounded `history_capture_failed`/`history_capture_skipped`
warning (operation/source/request_id/error-type only, never raw turn
content) and otherwise ignored. Exactly one capture attempt per logical
operation — no retry loop — relying on `history_store`'s own
`(request_id, role)` idempotency, not a scheme built here. This means a
history-store outage degrades Jarvis to "no memory of this exchange,"
never to "the exchange itself failed."

**Test isolation — a real finding, not an assumption**: `tests/__init__.py`'s
package-level guard (already used for `agent.usage.USAGE_FILE`) does
**not** reliably execute under this project's actual
`python -m unittest discover -s tests -v` invocation — confirmed
empirically (a stderr marker at that file's top level never printed
during a real `discover` run), because `discover` with no `-t` flag
imports each test file as a bare top-level module, not as a submodule of
the `tests` package, and therefore never triggers `tests/__init__.py`.
This is a pre-existing, previously-undetected gap that predates M4.2 and
was only ever masked because every affected test file already
redundantly isolates `USAGE_FILE` itself in its own `setUp`/`tearDown`.
The real, verified-working protection is exactly that per-file pattern,
now extended to `agent.history_store.HISTORY_DB` in every test file
whose tests exercise a real (even if provider-mocked)
`execute_task_stream()`/`execute_task()` call. See `tests/__init__.py`'s
own docstring for the full account.

## 13. Authentication / security

- **Secrets**: `agent/secrets.py` — macOS Keychain first
  (`keyring`, service `CampusPilot-APIKeys`), falls back to an environment
  variable. Never stored in `config/settings.py` (that module is
  explicitly typed *non-secret* config only) and never written to any of
  the documentation files this project maintains.
- **Login credentials**: `tools/credential_store.py` +
  `tools/manage_logins.py` (CLI-only, `python -m tools.manage_logins add`
  — deliberately kept out of chat; the model is instructed to never ask
  for or accept a password in conversation).
- **Permission levels + gating flags**: `tools/registry.py` (§7 above).
- **Autonomy**: `agent/autonomy.py` — a per-session dial
  (`config.settings.autonomy_level`, default 4) that controls which
  permission levels auto-run vs. require an explicit "yes, go ahead"
  confirmation. Cannot loosen the hard gates below. One hardcoded,
  autonomy-independent rule: `add_reminder`, `open_browser`, and
  `consult_coworker_agent` always require confirmation when
  `source="voice"`, regardless of autonomy level — added after a live
  incident where background audio (not the user) was transcribed as
  commands and triggered real browsing/reminder attempts.
- **Hard gates** (never affected by autonomy level): `requires_live_
  confirmation` (`confirm_login`, `send_email` — blocked entirely from
  `source="scheduled"`) and `unattended_allowed=False` (the whole
  `computer_*` family — blocked from scheduled runs for a broader reason:
  real unsupervised screen control).
- **Memory safety filter**: `agent/memory/safety.py` — refuses
  credential-shaped content and content that reads as an instruction
  aimed at Jarvis rather than a fact about the user.
- **Skill safety**: `agent/skills/safety.py` — a skill's text is never
  trusted as a permission grant; see §8.
- **Voice-specific safety**: `agent/quiet_mode.py` (indefinite "quiet", or
  timed "sleep"/"off" — 10/30 minutes, auto-expiring, cancellable early by
  a wake phrase) plus the voice-confirmation rule above.

## 14. External APIs

- **Anthropic** (`agent/chat.py`'s `anthropic_client`) — primary chat
  model, vision fallback, planner, deep reasoning, research agent.
- **OpenAI** (`openai_client`) — balanced/economy/quality chat tiers,
  vision primary, transcription (`gpt-4o-transcribe`), TTS
  (`gpt-4o-mini-tts`).
- **xAI** (`xai_client`, Phase 9 Milestone 2) — optional, OpenAI-API-
  compatible chat tiers, only configured/called if `XAI_API_KEY` is
  present; degrades to filtered-out, never a raised exception, otherwise.
- **Perplexity** (Phase 9 Milestone 2) — optional, grounded-research-only
  via its Agent API (`POST /v1/agent`, called directly with `httpx`, not
  through the `openai` SDK client — see §5); only configured/called if
  `PERPLEXITY_API_KEY` is present.
- **Apple frameworks**: `Speech` (on-device transcription fallback,
  `voice/local_transcribe.py`), AppleScript (Reminders, Calendar, Mail,
  Music, login-form autofill), Keychain (`keyring`), Photos (read-only
  SQLite).
- **Playwright/Chromium** — real browser automation (`tools/browser.py`),
  not an API in the network sense but an external process dependency.
- **OpenClaw Gateway** (`agent/openclaw_gateway.py`, OpenClaw M1) —
  optional, disabled by default, a local WebSocket RPC service (not a
  model provider) — see "OpenClaw bridge" below for its own subsection.

Both Anthropic's and OpenAI's *usage/billing* APIs were checked live and
found inaccessible with the project's regular (non-admin) API keys — both
require a separate Admin API key generated by an org owner. Not currently
wired; see `ROADMAP.md`.

### OpenClaw bridge (optional, read-only, OpenClaw M1)

OpenClaw (github.com/openclaw/openclaw, docs.openclaw.ai) is a separate,
independently-developed, real open-source project — a personal AI
assistant/messaging gateway, not a Jarvis subsystem. Researched against
its current official documentation 2026-08-16 (current stable release
found: `openclaw 2026.7.1-2`; current Gateway protocol version: 4).
Jarvis treats it as **optional subordinate infrastructure**, never a
second orchestrator:

```
Jarvis (model, via the ordinary tool-calling loop)
 ↓
permissioned ToolSpec (openclaw_status / openclaw_list_nodes,
                        tools/schemas/openclaw.py — permission_level=0,
                        flows through tools.registry.dispatch like
                        every other tool, no second dispatch path)
 ↓
OpenClaw bridge (agent/openclaw_gateway.py — connection, auth, protocol
                  negotiation, timeouts, normalized errors; makes NO
                  Jarvis policy decisions of its own)
 ↓
fixed RPC allowlist ({"health", "status", "node.list"} only —
                      node.invoke/chat.send/config.*/exec.*/approval.*/
                      plugin.*/every other method is structurally
                      unreachable, not merely undocumented)
 ↓
OpenClaw Gateway (a separate local process Jarvis connects TO as a
                   client — ws://127.0.0.1:18789, loopback only,
                   authenticated, role=operator, scopes=["operator.read"])
```

**Never the reverse direction** — there is no path from OpenClaw back
into Jarvis internals: OpenClaw cannot invoke a Jarvis tool, cannot
write Jarvis memory, cannot influence Jarvis's model routing, and holds
no authority over `tools.registry`/`agent.autonomy`'s decisions. Every
result this bridge returns is just data a normal, permission-gated
Jarvis tool call happens to have fetched — Jarvis remains the sole
orchestrator and the sole thing that decides what happens with an
OpenClaw result.

**Connection model**: one-shot (`websockets.sync.client`, not the async
API — fits Jarvis's existing synchronous tool-execution architecture
with no asyncio event-loop adapter needed). A fresh WebSocket is opened,
authenticated, used for exactly one RPC, and closed, per call — no
persistent connection manager in M1; correctness/isolation over
connection reuse, appropriate for two low-volume, read-only tools.

**Transport**: authenticated loopback WebSocket
(`ws://127.0.0.1:18789`, OpenClaw's own documented default bind mode) —
plain `ws://`, not `wss://`. This is the documented local default, not a
decision to skip encryption: loopback traffic never leaves this machine.

**Auth — device-identity, challenge-signed handshake** (verified
2026-08-16 against the actual published `@openclaw/gateway-client` and
`@openclaw/gateway-protocol` npm packages, then re-verified the same day
against the actual current STABLE `openclaw` app package
(`openclaw@2026.7.1-2`) since the client/protocol packages turn out to
have no stable release of their own, only prereleases — all downloaded
and inspected directly, not paraphrased from docs, since docs.openclaw.ai
does not cover third-party device auth and a related GitHub issue's own
claims turned out to be partly stale): Jarvis holds its own persistent
Ed25519
device identity (`OPENCLAW_DEVICE_PRIVATE_KEY`, PEM/PKCS8, generated
once and reused, via `agent/secrets.py`'s Keychain-first store — no new
secrets mechanism, same trust boundary every other secret already
relies on). Each connection:
1. Waits for the Gateway's `connect.challenge` event (nonce + timestamp)
   — never sends device-auth material before receiving one.
2. Builds the real, verified V3 device-auth payload
   (`"v3|deviceId|clientId|clientMode|role|scopes|signedAtMs|token|nonce|platform|deviceFamily"`)
   and signs it with the Ed25519 private key. `signedAtMs` is the
   challenge's own `ts` when the Gateway provides one, falling back to
   the client's wall clock only if it doesn't — matching real client
   behavior verified against both the CLI/backend and browser reference
   clients (re-verified 2026-08-16 against a newer npm release after a
   claim to the contrary surfaced; the claim was checked against
   primary source and found incorrect, so this was left unchanged).
   `nonce` always comes from the challenge, never elsewhere.
3. Sends `connect` with `role="operator"`, `scopes=["operator.read"]`
   only (never `operator.write`/`operator.pairing`/`operator.admin` in
   M1), and the signed `device` block (id, publicKey, signature,
   signedAt, nonce). The credential itself — the shared Gateway token
   (`OPENCLAW_GATEWAY_TOKEN`) or a previously-issued device token — is
   sent under `auth.token` or `auth.deviceToken` respectively. This is
   verified against the Gateway SERVER's own connect-auth resolution
   (`resolveSharedConnectAuth`/`resolveDeviceTokenCandidate`, read
   directly from `openclaw@2026.7.1-2`'s compiled server source, the
   actual current STABLE app release — not just client-side schema
   field existence, which only proves a field CAN be sent, not what it
   MEANS): `auth.token` is checked against the Gateway's own configured
   shared secret; `auth.deviceToken` is checked via a wholly separate
   `verifyDeviceToken` path, required (not just preferred) so a
   rejection reports `AUTH_DEVICE_TOKEN_MISMATCH` rather than being
   silently reinterpreted as a failed shared-token check. `auth
   .bootstrapToken` is a genuinely distinct credential (verified via
   `verifyBootstrapToken(deviceId, publicKey, token, ...)`, meant for a
   device-pairing/setup code) that Jarvis does not hold and never sends
   — an earlier version of this bridge incorrectly sent the shared
   Gateway token there, corrected 2026-08-16.
4. On `hello-ok`: verifies the negotiated protocol AND that
   `operator.read` was actually granted — **fails closed** if not, never
   proceeds on authentication success alone. Persists a newly-issued
   `deviceToken` (`OPENCLAW_DEVICE_TOKEN` secret) if the Gateway returns
   one.
5. On `PAIRING_REQUIRED` (a new/unrecognized device identity): returns a
   clean, normalized `pairing_required` result with only a safe request
   ID — **never auto-approved**. A human must run
   `openclaw devices approve <requestId>` (or `--latest`) themselves;
   Jarvis has no pairing-approval tool.
6. On `AUTH_DEVICE_TOKEN_MISMATCH` for a stored device token: clears it
   and retries **exactly once** with the shared Gateway token — mirroring
   the real client's own verified "cleared stale device-auth token"
   behavior — never loops further.

`client.id`/`client.mode` are validated by the Gateway against a real,
closed enum (verified in `@openclaw/gateway-protocol`'s `client-info.mjs`)
— **not** free text. This bridge uses `"cli"` for both (the closest
legitimate, non-reserved identity for a non-interactive integration);
`"gateway-client"`/`"backend"` are OpenClaw's own reserved internal
identity and are never used, per explicit instruction. Jarvis's own
honest identification goes in the free-text `deviceFamily` field
(`"jarvis"`) instead.

**Device-ID derivation — CONFIRMED**: SHA-256 of the raw 32-byte Ed25519
public key, hex-encoded. Originally an unverified assumption (neither
published beta client/protocol package exposes key generation/signing
except as an injected dependency, stubbed as a no-op in the default
export); confirmed 2026-08-16 against the actual current STABLE
`openclaw` app package (`openclaw@2026.7.1-2`), which contains a literal
`deriveDeviceIdFromPublicKey` function (`src/infra/device-identity.ts`)
doing exactly this, and whose Gateway server independently re-derives
and compares this value against the client-claimed `device.id` on every
connect — an exact match to this bridge's implementation.
`DEVICE_AUTH_DEVICE_ID_MISMATCH` handling (`_raise_for_error` in
`agent/openclaw_gateway.py`) is kept as defense-in-depth regardless, not
because of remaining doubt. See `agent/openclaw_gateway.py`'s own module
docstring for the full verification trail (both the beta-package and
stable-app-package inspections).

**Testing scope**: all of the above is protocol-verified against a
genuine **local fake** Gateway server (`websockets.sync.server`,
ephemeral loopback port, real Ed25519 signature verification) in
`tests/test_openclaw_gateway.py` — this proves the handshake, auth-field
selection, and error handling are implemented correctly against the
documented/reverse-engineered protocol.

**OpenClaw M1.5 — real loopback Gateway smoke test (2026-08-17,
✅ verified)**: the bridge was also validated against an actual, running
`openclaw@2026.7.1-2` Gateway process — isolated, loopback-only,
temporary, never installed as a daemon or left on this machine. Real
`openclaw_status` and `openclaw_list_nodes` calls succeeded through
Jarvis's own `tools.registry.dispatch` path (protocol 4, `operator.read`
only, confirmed independently via the OpenClaw CLI's own `devices list`
inspection, empty node list as expected). This surfaced two real bugs
the local fake server and source-reading alone had missed: `client
.platform` was never sent, though the real `ConnectParams.client` schema
requires it; and `client.deviceFamily` was signed into the V3 payload
but never actually included on the wire, so the real Gateway's
independent signature reconstruction failed. Both are fixed (`agent/
openclaw_gateway.py`'s `_CLIENT_PLATFORM`/`_CLIENT_DEVICE_FAMILY` now
appear in both the wire `client` block and the signed payload), and the
fake test server's own signature verification was corrected to
reconstruct from the actual captured wire values rather than duplicate
expected constants, so a regression of this same mistake would be
caught locally next time. No real Gateway was left running or installed
after this test.

**Real-Gateway smoke-test isolation — lesson learned**: the first
attempt at this smoke test used OpenClaw's `--dev` flag for convenience
and got a real isolation failure from it — `--dev`'s auto-created "dev
workspace" ignored the `OPENCLAW_STATE_DIR` override entirely and wrote
five template files under the real `~/.openclaw/workspace-dev`, and the
auto-loaded default plugin set included `bonjour`, which broadcast the
temporary Gateway's existence (with the real machine's device name) on
the LAN via mDNS within seconds of startup — even though the WebSocket
listener itself was correctly loopback-only throughout. This was caught
and stopped immediately, not a Jarvis security issue (Jarvis's own
loopback-only, no-LAN-exposure design was never violated — the WebSocket
bind stayed on `127.0.0.1`/`::1` the whole time), but a real OpenClaw
test-environment configuration gap. The corrected approach — used
successfully for the rest of this test and required for any future
temporary OpenClaw test harness — is: never use `--dev`; explicitly set
`OPENCLAW_STATE_DIR` AND patch `agents.defaults.workspace` to an
isolated path (`openclaw config patch`, not reliance on the env var
alone); explicitly set `plugins.enabled = false` (eliminates the whole
plugin set, not just `bonjour`, in one setting); verify the listener's
actual bind address before letting Jarvis connect; verify no writes
landed under `~/.openclaw` before and after; never run OpenClaw's normal
onboarding (it can discover/validate real model-provider credentials);
and delete the temporary Gateway/device-token secrets afterward (see
`CHANGELOG.md`'s M1.5 entry) since they're tied to Gateway state that no
longer exists.

**Failure isolation**: `openclaw_enabled` defaults to `False`. Nothing in
this module runs at import time (no module-level connection, unlike
`agent/chat.py`'s required provider clients) — OpenClaw disabled, not
installed, Gateway stopped, token absent, auth rejected, or a protocol
version mismatch all degrade to a normalized "unavailable" tool result,
never a Jarvis startup failure or an unhandled exception.

**Data minimization**: `get_node_list()` never passes a raw Gateway node
record through — explicit field-by-field whitelisting
(id/display_name/platform/connected/capability-names-only). Node-
published plugin/skill descriptors are read only for their capability
*name* string; the descriptor object itself (which could carry a
plugin's own tool/skill definition) is discarded. Jarvis does not import
OpenClaw tools or skills in M1, and has no mechanism to.

**Explicitly out of scope for M1 and not built**: `node.invoke` (any
node capability — notifications, device info, camera, screen, location),
and anything requiring `operator.admin`/`operator.approvals`/
`operator.pairing`/`operator.talk`. Outbound messaging is M2, described
next — still no `node.invoke`, still no device capabilities, still no
OpenClaw agent/model-routing authority.

### OpenClaw messaging bridge (optional, outbound-only, OpenClaw M2 — under review, no real channel configured)

A second, narrow capability layered on top of the M1 bridge: sending a
plain-text outbound message through an operator-configured OpenClaw
channel. Implemented and tested against a local fake Gateway server
only — **no real messaging channel (Telegram, Discord, WhatsApp, Slack,
Signal, iMessage, ...) has been configured or exercised, and no real
outbound message has been sent, as of this writing.**

```
Jarvis (model, via the ordinary tool-calling loop)
 ↓
send_message_via_openclaw (tools/schemas/openclaw.py — permission_level=3,
                            side_effect=True, requires_live_confirmation=True)
 ↓
agent/openclaw_messaging.py (channel/target allowlist, message
                              validation, idempotency-key generation,
                              result normalization — Jarvis's own policy
                              layer, makes no transport/auth decisions,
                              never auto-retries an uncertain delivery)
 ↓
agent/openclaw_gateway.py's _send_raw() (private — see below) → _MESSAGE_PROFILE
 ↓
Gateway `send` RPC (never `chat.send` — see below)
 ↓
operator-configured channel plugin → recipient
```

**`send`, never `chat.send`, and never `message.action`** — the core
architectural decision this milestone was built around. Verified
directly against `openclaw@2026.7.1-2`'s compiled server source (see
`agent/openclaw_gateway.py`'s module docstring for the full verification
trail): `send` is a genuine, distinct, top-level Gateway RPC method with
its own `SendParamsSchema`. `chat.send` requires a `sessionKey` and is
part of OpenClaw's own agent/session execution surface — using it would
mean an OpenClaw agent loop processes Jarvis's outbound message, which
is exactly the architectural blurring this project avoids; Jarvis
remains the sole orchestrator, and OpenClaw is transport only.
`message.action` is a broader action-dispatch RPC the OpenClaw CLI's
own richer commands use (stickers, broadcasts, moderation); this bridge
never uses it either.

**A separate device identity from M1, on purpose.** `send` requires
`operator.write` (confirmed against the real core method-scope
descriptor table); M1's read-only identity requests only
`operator.read`. Rather than upgrade the read identity's scope, M2 holds
its own, completely separate Ed25519 keypair and device token
(`OPENCLAW_MESSAGE_DEVICE_PRIVATE_KEY`/`OPENCLAW_MESSAGE_DEVICE_TOKEN`,
distinct secrets from `OPENCLAW_DEVICE_PRIVATE_KEY`/
`OPENCLAW_DEVICE_TOKEN`). This separation gives three distinct, non-
equivalent guarantees, and the boundary must not be overstated as
stronger than it is:
1. **Credential isolation (true, unconditional)** — the read and
   messaging keypairs/tokens are independent secrets; compromising one
   does not hand over the other.
2. **Jarvis RPC confinement (true, structurally enforced)** — `_call()`
   in `agent/openclaw_gateway.py` fails closed on any profile that isn't
   one of the two exact module-level `_Profile` instances (checked by
   Python identity, `is`, not `==`, so a forged profile with identical
   field values is still rejected), and each profile's RPC allowlist is
   independently exact (`_READ_PROFILE`: `{health, status, node.list}`;
   `_MESSAGE_PROFILE`: `{send}` only) — through Jarvis, a read-only
   connection cannot reach `send` and the messaging connection cannot
   reach `health`/`status`/`node.list`.
3. **Server-side scope semantics (asymmetric — do not overstate)** — per
   the real Gateway's own `operatorScopeSatisfied` compatibility check
   (verified against `openclaw@2026.7.1-2`'s compiled source),
   `operator.write` already satisfies an `operator.read` check
   server-side. So while the *read* identity is genuinely incapable of
   any write through Jarvis, the claim that a compromised *messaging*
   credential "carries no read authority at all" is not accurate at the
   server level — it is Jarvis's own RPC confinement (point 2), not the
   credential's cryptographic scope, that keeps the messaging identity
   from reading anything through this bridge.

Both identities may reuse the same `OPENCLAW_GATEWAY_TOKEN` shared
bootstrap credential during pairing; that credential authenticates the
connection generically and is not what determines a device's granted
scopes. There is no public API to construct a third `_Profile` or to
request an arbitrary scope list.

**Deterministic, Jarvis-side allowlists — never OpenClaw's own name/
directory lookups.** `openclaw_messaging_enabled` defaults to `False`;
`openclaw_allowed_channels`/`openclaw_allowed_targets` default to empty.
No channel is authorized and no message can be sent until an operator
explicitly configures both an exact channel name and an exact
"channel:target" pair — no wildcards, no regex, no fuzzy/name-based
resolution ("send to Bob" is never handed to OpenClaw as a name to
resolve). A future human-friendly alias layer, if built, would resolve
deterministically to one of these exact configured pairs through
Jarvis's own contacts layer, never through OpenClaw's own channel-side
directory.

**Text-only, first pass.** The real `send` RPC schema supports media,
voice notes, polls, and more; `agent/openclaw_messaging.py` deliberately
never emits any of those fields. `message` is the only content field
this bridge ever sends, capped at a conservative, channel-neutral 4000
characters (`MAX_MESSAGE_LENGTH`) — oversized input is rejected, never
silently truncated.

**Idempotency and uncertain delivery — at most ONE transmission per
logical send, never an automatic retry.** Every send carries a fresh,
internally-generated `idempotencyKey` (a UUID; never caller-supplied).
The real Gateway does maintain a genuine, verified, in-memory idempotency
cache (5-minute TTL, single-process — see `agent/openclaw_gateway.py`'s
docstring for the exact primary-source citations), but that cache does
not survive a Gateway process restart, so a same-key resend is not
provably safe: if the Gateway successfully delivers the message and then
dies/restarts before Jarvis receives the response, the in-memory dedupe
entry is gone and a resend — even with the identical key — could produce
a real duplicate message. `agent/openclaw_gateway.py` distinguishes a
failure that happened *before* the request frame was transmitted
(definitive — reported as `delivery_status: "failed"`) from one that
happened *after* transmission but before a trustworthy response arrived
(`OpenClawUncertainDelivery` — reported as `delivery_status:
"uncertain"` and left there, never automatically retried, with the same
key or a new one). The `idempotencyKey` is still generated and sent on
every call (protocol correctness and defense-in-depth against the
Gateway's own retry/replay machinery), but `agent/openclaw_messaging.py`
never treats it as license to resend on its own. A dedicated verifier
(`agent/verification.py`'s `_verify_send_message_via_openclaw`) parses
this JSON result directly rather than relying on the generic string
check, so an `"uncertain"` result is always flagged as unverified —
never silently read as success.

**Pairing is normalized separately from M1's.** A first connection with
the new messaging identity may require pairing, exactly like M1's own
device identity did — never auto-approved, and the resulting result
shape (`sent`/`delivery_status`-keyed) is naturally distinct from
`get_status()`'s (`configured`/`available`-keyed), so a caller can never
confuse which identity's pairing is pending.

**Explicitly out of scope for M2's first pass**: any real channel
configuration/login, media/attachments/voice/polls, `node.invoke`,
device capabilities, OpenClaw agent/session execution, OpenClaw
model-routing authority.

## 15. Inter-agent communication

The only inter-agent communication is main-loop → coworker-agent, one
direction, via `consult_coworker_agent` (single task) or
`delegate_parallel_tasks` (2+ independent tasks, Phase 9 Milestone 3, §4)
→ `execute_agent()` → a subprocess running `agent/agents/worker.py` (one
subprocess per subtask even for a parallel batch — never a shared or
pooled worker process). Communication is JSON over stdin/stdout (task,
request_id, agent_name in; an `AgentResult` out) — chosen specifically so
free-text task descriptions never have to survive shell/argv quoting.
`request_id` is propagated into the subprocess via
`agent/request_context.py`'s contextvar, so a coworker agent's own logs
correlate back to the request that triggered it — including every
subtask in a parallel batch, which all share the outer request's
`request_id`. There is no agent-to-agent communication (`MAX_AGENT_DEPTH
= 1` structurally prevents an agent from consulting another agent; a
parallel batch is still Jarvis calling N coworkers directly, never a
coworker calling another coworker).

## 16. Error handling

- **Retry**: `agent/retry_policy.py` — a small, deterministic, bounded
  policy per tool-call failure; unrecognized error types default to *not*
  retrying (conservative — retrying an unrecognized failure mode might
  itself be unsafe).
- **Provider fallback**: a live failure calling the current provider
  (not a tool failure) advances to the next candidate in
  `build_fallback_chain()`'s ordered list (§5) — as many providers as are
  configured and passed filtering, tried one at a time, never
  simultaneously — *unless* a side-effecting tool already ran this
  request (`PartialToolExecution` — reported to the user instead of
  silently retried on another provider, since retrying risks repeating a
  real action).
- **Cancellation**: `agent/cancellation.py` + `agent/execution_state.py`
  — cooperative, not preemptive. A cancellation request sets a flag
  checked at loop-iteration/tool-dispatch boundaries; a tool already in
  flight is allowed to finish.
- **Usage limits**: `agent/usage.py`'s `check_request_limits()` — a
  circuit breaker (max requests/tokens/estimated cost per request),
  checked at the same loop boundaries as cancellation. Already-made calls
  can't be undone; nothing further compounds.
- **Agent timeouts**: `agent/agents/manager.py`'s `execute_agent()` — a
  real OS-level `subprocess.run(timeout=...)`, SIGKILLs a runaway
  coworker-agent subprocess (§4).
- **Verification**: `agent/verification.py` — lightweight post-action
  checks for the subset of side-effect tools where "did this actually
  take effect" is cheaply answerable; not attempted for pure reads.

## 17. Logging

Three deliberately separate logs, different questions each:

- **`agent/audit.py`** — "what did Jarvis actually do": every tool call,
  input, result, permission label. User-facing accountability record.
- **`agent/observability.py`** — "what is the system doing internally,
  and where did something go wrong": structured JSON-lines to stderr,
  correlated by `request_id`. Never logs secrets; user content capped to
  a short preview.
- **`agent/usage.py`** — "what did this cost": per-call provider/model/
  operation/token/cost records, correlated by `request_id` and `agent`.
  Estimated cost only (`_PRICING` is a maintained snapshot, not a live
  pricing API) — see the module's own docstring. `cost_since(cutoff)`/
  `cost_today()` are the shared aggregation primitive behind any
  "at a glance" cost figure — returns `None` (not `0.0`) if
  `usage_history.json` can't be read/parsed, so a caller can distinguish
  "no usage yet" from "data unavailable" and fail safely; currently used
  by `ui/menu_bar.py`'s cost dropdown item, intended to also back
  `pages/1_Dashboard.py`'s equivalent figures and any future always-
  visible menu-bar title indicator rather than each reimplementing the
  same sum-over-`get_since()` logic.

## 18. Testing

`tests/` — 753 tests as of this writing (`python -m unittest discover -s
tests`), organized roughly by phase/module (`test_agents_*`,
`test_phase4_security.py` through `test_phase7_security.py`, `test_usage*`,
`test_voice_*`, `test_skills_*`, etc.). Established policy: mock at the
**external-call boundary** (the real Anthropic/OpenAI client, `subprocess.
run`, a real network call) — never mock internal application logic just to
make a test pass. File-backed stores are redirected to a temp path per
test class (`HISTORY_FILE`, `STATE_FILE`, `USAGE_FILE`, `QUIET_MODE_FILE`,
etc. are all module-level variables reassigned in `setUp`/restored in
`tearDown`) — this is a real, previously-violated invariant: several
executor-integration test files were found mid-Phase-8 to be writing
zero-cost test artifacts into the *real* `usage_history.json` because they
isolated `HISTORY_FILE`/`STATE_FILE` but not `USAGE_FILE`; fixed by adding
the same isolation to each. See §19 ("Rules for modifying architecture")
in `CLAUDE.md` before adding a new file-backed store's tests.

No CI configuration exists in this repo — tests are run manually.
