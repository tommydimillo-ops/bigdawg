# Jarvis — Roadmap

A living backlog, not a spec. Nothing here is a commitment or a timeline.
Items only move to **Completed** once they're actually implemented and
tested — see `CHANGELOG.md` for when/why. Cross-reference: `ARCHITECTURE.md`
marks the same implemented/not-implemented boundary from the code side;
this file is the planning side of the same line.

## Completed

Grouped by the phase that shipped them (see `CHANGELOG.md` for detail):

- **Foundation**: Streamlit chat, 53 tools, single registry
  (`tools/registry.py`), Claude-primary/OpenAI-fallback loop.
- **Phase 1-2**: Tool registry extraction, typed config
  (`config/settings.py`), request context, structured observability
  logging, model router scaffolding.
- **Phase 3**: Unified structured memory (`agent/memory/`) with types,
  confidence, importance, same-subject supersession.
- **Phase 4**: Multi-step planning (`agent/planner.py`), real autonomy
  levels (`agent/autonomy.py`), post-action verification, retry policy,
  cooperative cancellation.
- **Phase 5**: Persistent execution history, cross-interface Jarvis state
  (`agent/jarvis_state.py`), formal cancellation API.
- **Phase 6**: Voice-first native menu-bar app, wake-word detection,
  TCC/code-signing fix (own signed `.app` bundle), on-device transcription
  fallback, quiet mode.
- **Phase 6.5**: Claude Skills integration (`agent/skills/`) — data-only,
  prompt-context skills; Cowork integration point stubbed honestly
  (`agent/cowork_gateway.py` — no real API to call yet).
- **Phase 7**: Coworker agents (`agent/agents/`) — research and memory
  agents doing real work, coding/qa agents mostly deferred; pure routing
  for attribution, real execution behind a permission-gated tool.
- **Phase 8**: Observability, cost control, agent runtime hardening —
  real per-call token/cost accounting (`agent/usage.py`), a cost
  dashboard section, configurable per-request usage limits, genuine
  subprocess-based agent timeout (real `SIGKILL`, not a thread that keeps
  running), `request_id` correlation via contextvars, regression/security
  tests for all of it.
- **Post-Phase-8 live fixes** (same session, in response to a real
  production incident): voice-confirmation gating extended to
  `open_browser`/`consult_coworker_agent` (background audio was
  triggering real browsing); sentence-chunked TTS (measured ~2s faster
  time-to-first-audio on a 3-sentence reply); timed quiet modes
  ("sleep" = 10 min, "off" = 30 min, auto-expiring, wake-phrase-
  cancellable).
- **Documentation system** (committed `262bf2b`): `CLAUDE.md`,
  `HANDOFF.md`, `ARCHITECTURE.md`, `ROADMAP.md`, `CHANGELOG.md`,
  `SESSION_LOG.md` — session-to-session continuity without relying on
  conversational memory.
- **Doc accuracy fix** (committed `f3fd416`): corrected the documented
  tool count (was a stale "~45", actual registered count is 53) across
  `CLAUDE.md`/`ARCHITECTURE.md`/`ROADMAP.md`. `CHANGELOG.md`'s Phase 1
  entry deliberately left as `~45` — it's a historical record of that
  phase, not current state.
- **Menu-bar cost readout, Option B** (dropdown item, not an always-on
  title): "Estimated Cost" item in `ui/menu_bar.py`'s dropdown, read
  lazily via `agent/usage.py`'s new `cost_since()`/`cost_today()` on
  click — no background timer, no change to the title/state-icon system.
  Fails safely (an "unavailable" alert, never a wrong number) if
  `usage_history.json` can't be read/parsed. See "Next" below — Option A
  (always-visible title) was considered and explicitly not chosen.
- **Repository cleanup**: removed root `memory.json` (dead, superseded by
  the absolute-path store), `CampusPilotAgent.app.old-handbuilt/`
  (superseded by the py2app-built bundle; the live LaunchAgent plist
  never pointed at it), and `docs/old-launchagent-backups/` (a backup
  config for a defunct prior-project path) — each verified unreferenced
  by code, launch agents, scripts, tests, or documentation before
  removal.
- **Duplicate-scheduler fix**: `agent/scheduler_lock.py` (new) — a
  non-blocking, kernel-managed `fcntl.flock(LOCK_EX | LOCK_NB)` on a
  dedicated lock file, re-attempted every poll tick by both
  `agent/scheduler_daemon.py` and `ui/menu_bar.py`'s built-in scheduler.
  Only the tick's lock-winner executes due tasks; the loser skips the
  tick entirely (no execution, no `mark_run`, no UI/voice-state touch)
  and logs a `scheduler_lock_deferred` diagnostic. Released automatically
  by the kernel if the holding process crashes or is killed — no
  PID-file/stale-lock detection code involved. Both deployment modes
  (menu-bar always-on, `scheduler_daemon.py` headless fallback) are
  preserved; they may now run together safely. See "Known lifecycle
  risks" below — this was previously listed there as unfixed.
- **Phase 9 — Task-Aware Routing & Verification Hardening** (milestones
  tracked individually below; the phase overall is still in progress —
  Milestone 3 has not started):
  - **Milestone 0 — GitHub Actions CI** (`d3481fc`) ✅: added
    `.github/workflows/tests.yml`, running the full suite
    (`python -m unittest discover -s tests -v`) on every push/PR against
    placeholder (non-functional) API key env vars — no real network calls
    in CI, consistent with this project's mocked-external-call-boundary
    test policy.
  - **Milestone 1 — Playwright browser-profile ownership hardening**
    (`7b67bf0`) ✅: closes the "Playwright profile contention" risk
    previously listed under "Next" below. `agent/browser_lock.py` adds a
    non-blocking, cross-process `fcntl.flock` (same primitive as
    `agent/scheduler_lock.py`, held for the browser context's lifetime
    rather than reacquired per-tick) so Streamlit/menu-bar/scheduled-task
    processes no longer race Chrome's own profile lock when opening the
    shared persistent profile; a losing process gets a clean
    `BrowserBusyError` message instead. See `CHANGELOG.md`.
  - **Milestone 2 — Task-aware, multi-provider model routing + layered
    fallback** ✅: deterministic task classification
    (`agent/task_classifier.py`), capability/health/budget-filtered
    cost-aware provider ranking (`agent/model_router.py`'s
    `build_fallback_chain()`), a generalized N-provider fallback loop
    (`agent/executor.py`), and two new working (not scaffolded) optional
    providers — xAI and Perplexity, the latter via its Agent API rather
    than Chat Completions (see `ARCHITECTURE.md` §5). Currency-reviewed
    against official provider documentation immediately pre-commit
    (model IDs, the pricing catalogue, and `vision_model`, which had been
    missed by the router-scoped pass and was caught in a narrow
    follow-up). This satisfies the "Task-based model routing" item
    previously listed under "Future" below — removed from there as
    superseded. See `CHANGELOG.md` for full detail.

## In progress

- Nothing mid-flight as of the last session update — see `HANDOFF.md`
  for the authoritative current state (this section will drift faster
  than this file gets updated; `HANDOFF.md` is the source of truth for
  "right now").

## Next

Candidates raised but not yet started, roughly in order of what's been
discussed most recently:

- **Provider admin-key cost reconciliation** — link real OpenAI/Anthropic
  Admin API keys (checked live, confirmed the project's regular keys
  can't reach either provider's usage/billing endpoint) to reconcile
  `agent/usage.py`'s *estimated* costs against actual billed amounts.
  Explicitly deferred by the user pending them generating the admin keys
  themselves ("it's okay for now").
- **Phase 9 Milestone 3 — QAAgent / verification expansion + bounded
  parallel coworker delegation** — not started; the next Phase 9 item.
  Builds on `agent/verification.py`'s existing narrow capability (see
  "Future" below) and Phase 7's coworker-agent framework. "Bounded
  parallel" means a capped number of concurrent coworker delegations for
  independent subtasks — it does not touch `MAX_AGENT_DEPTH = 1` (an
  agent still can never consult another agent; this is about breadth of
  concurrent delegation from the main loop, not depth).
- **Menu-bar cost readout, Option A** (always-visible title, e.g.
  `🤖 $0.02 today`) — considered alongside Option B (the dropdown item,
  now built — see "Completed"), not chosen: it would need a recurring
  update path (timer or per-record refresh) and would contend with the
  title's existing job of reflecting live voice/task state. Revisit only
  if the dropdown item proves insufficiently glanceable in practice; the
  underlying `agent/usage.py` aggregation (`cost_since()`/`cost_today()`)
  is already shared and reusable, so this would not need new aggregation
  logic, only a new display path.

## Future

Larger, not-yet-started capabilities, matching the long-term architecture
this project is meant to grow into:

- **Phase 9 Milestone 4 — FTS5 conversation/history search** — later in
  Phase 9, after Milestone 3. Not started, not designed in detail yet;
  intended to give full-text search over conversation/execution history
  (see `agent/conversation_store.py`/`agent/execution_history.py` for the
  current, non-searchable stores this would index) using SQLite's FTS5
  extension rather than a new external search dependency.
- **Phase 10 — Real CodingAgent capability + checkpoint/rollback** —
  CodingAgent is currently a stub
  (`metadata={"deferred_to_executor": True}` for everything). Real
  code-editing/execution capability needs to land *on top of* Phase 8's
  subprocess isolation and usage limits, not bypass them — this was an
  explicit design constraint when Phase 8 was built ("before we allow
  long-running coworker agents to do computer/code work, we should make
  agent execution genuinely killable"). Checkpoint/rollback (the ability
  to undo a CodingAgent's changes) is part of this same phase, not a
  separate later item — real code-editing capability without a way back
  out is not considered safe to ship on its own.
- **QAAgent expansion** — today only runs this project's own test suite
  read-only; broader verification capability (checking arbitrary tool
  results, regression review) already has a narrow real capability via
  `agent/verification.py` that a future QAAgent could build on. The near-
  term slice of this is Phase 9 Milestone 3 above.
- **MCP integration** — remains future/deferred; no client/server exists
  yet. If built, tools reached through MCP should register through the
  existing `tools/registry.py`, not create a parallel dispatch path (same
  principle already stated for a future Cowork integration).
- **Cowork integration** — `agent/cowork_gateway.py` is an honest stub;
  there is no documented, programmatic Cowork API to integrate against
  yet. Wire it in when one exists, through the registry, keeping Jarvis
  as the orchestrator (any Cowork-originated action still flows through
  `tools.registry`/`agent.autonomy`/`agent.executor`).
- **Hermes** — not integrated, and deliberately staying that way for now;
  no client/adapter for it exists anywhere in this codebase. Revisit only
  if a concrete need is identified — not a currently active line of
  work.
- **Progressive skill disclosure** (`agent/skills/`) — today's skill
  matching is a flat keyword-overlap lookup over every registered
  `SKILL.md`; a tiered/progressive disclosure model (e.g. surfacing a
  short list before expanding full instructions) is deliberately deferred
  until the skill library is substantially larger than it is today — not
  worth the added complexity for the current, small skill set.
- **Vector/embedding-based memory search** — deliberately not built;
  today's relevance retrieval is deterministic keyword overlap. Revisit
  only if keyword-overlap retrieval demonstrably stops being good enough,
  not preemptively.
- **A hardware client** — mentioned as a long-term possibility in earlier
  architecture notes (`agent/jarvis_state.py`'s docstring anticipates
  "voice/hardware clients"); nothing concrete planned.

## Experimental

Nothing currently in an experimental/prototype state. (Reserved section —
use this for genuinely speculative work-in-progress that isn't ready to
be called "Next," not as a dumping ground for every idea.)
