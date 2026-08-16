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
  (task-aware, cost-   (53 tools, permission     manager.py
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
model's own judgment.

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
| `research` | `agent/agents/research.py` | Wraps `agent/research_agent.py` — real multi-step web research (its own small Claude tool loop: `open_browser`, `read_document`), returns a synthesized answer |
| `memory` | `agent/agents/memory.py` | Wraps `agent/memory_agent.py`'s `remember`/`recall` — real memory read/write |
| `coding` | `agent/agents/coding.py` | **Stub.** Returns `metadata={"deferred_to_executor": True}` — no real execution yet |
| `qa` | `agent/agents/qa.py` | Real, but narrow: runs this project's own test suite read-only (`python -m unittest discover`) when the task text matches test-running phrasing; everything else defers |

**Execution model (Phase 8 Part 4):** real execution goes through
`agent/agents/manager.py`'s `execute_agent()`, which spawns
`python -m agent.agents.worker` as a genuine OS subprocess and enforces
`settings.agent_timeout_seconds` via `subprocess.run(timeout=...)` — a real
`SIGKILL` on timeout, not a `ThreadPoolExecutor` that merely stops waiting
while the underlying thread keeps running. Verified live: a subprocess
mid-way through a real ~5-7s test-suite run was killed at 1.01s with the
process confirmed gone, not orphaned.

`agent/agents/manager.py` also has a second function, `route_and_execute()`
— pure-routing-then-in-process-dispatch, used only by
`agent/executor.py`'s attribution-only routing call and by its own test
suite (which depends on registering fake, in-process Agent instances a
subprocess couldn't see). **The real, live tool (`consult_coworker_agent`)
calls `execute_agent()`, not `route_and_execute()`.**

`MAX_AGENT_DEPTH = 1` — an agent's own `execute()` must never itself
trigger another agent consultation; enforced structurally in
`execute_agent()`, not by convention.

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

The real, live memory store is the absolute,
`~/Library/Application Support/CampusPilot/`-based path in
`database/memory.py`; a dead repo-root `memory.json` fossil from before
that path fix was removed in a repository cleanup pass (see
`CHANGELOG.md`).

## 7. Tools

**`tools/registry.py`** is the single source of truth for every tool: name,
description, JSON schema, `permission_level` (0-5), and four gating flags
(`requires_live_confirmation`, `unattended_allowed`, `side_effect`,
`parallel_safe`). 53 tools, grouped by theme into `tools/schemas/*.py`
(`browsing`, `computer_use`, `execution_control`, `logins_and_email`,
`memory_and_learning`, `productivity`, `reasoning`, `scheduling`, `skills`,
`system`, `agents`) — each calls `register(ToolSpec(...))` at import time.
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

No database server, no ORM, no SQL (one narrow exception: read-only
`sqlite3` access to the macOS Photos library's own database in
`agent/personal_context.py`, for aggregate stats only). Every store is a
flat JSON file under `~/Library/Application Support/CampusPilot/`:
`memory.json`, `execution_history.json`, `usage_history.json`,
`scheduled_tasks.json`, `quiet_mode.json`, `conversation.json`,
`jarvis_state.json`, `personal_context.json`, `audit.log` (JSON-lines),
`tts.pid`. Writes use tmp-file-then-`os.replace`; several add an
`fcntl.flock`-guarded read-modify-write cycle for cross-process safety
(memory, execution history, usage history, scheduled tasks).

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

Both Anthropic's and OpenAI's *usage/billing* APIs were checked live and
found inaccessible with the project's regular (non-admin) API keys — both
require a separate Admin API key generated by an org owner. Not currently
wired; see `ROADMAP.md`.

## 15. Inter-agent communication

The only inter-agent communication is main-loop → coworker-agent, one
direction, via the `consult_coworker_agent` tool → `execute_agent()` →
a subprocess running `agent/agents/worker.py`. Communication is JSON over
stdin/stdout (task, request_id, agent_name in; an `AgentResult` out) —
chosen specifically so free-text task descriptions never have to survive
shell/argv quoting. `request_id` is propagated into the subprocess via
`agent/request_context.py`'s contextvar, so a coworker agent's own logs
correlate back to the request that triggered it. There is no agent-to-
agent communication (`MAX_AGENT_DEPTH = 1` structurally prevents an agent
from consulting another agent).

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
