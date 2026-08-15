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
- **Documentation system** (this work): `CLAUDE.md`, `HANDOFF.md`,
  `ARCHITECTURE.md`, `ROADMAP.md`, `CHANGELOG.md`, `SESSION_LOG.md` —
  session-to-session continuity without relying on conversational memory.

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
- **Known lifecycle risks** (documented, not yet fixed):
  - Duplicate scheduler: `agent/scheduler_daemon.py` and
    `ui/menu_bar.py`'s built-in loop both execute the same scheduled
    tasks independently if run together.
  - Playwright profile contention: the Streamlit and menu-bar processes
    each hold their own browser context pointed at the same on-disk
    Chrome profile; no locking/coordination if both drive a browser at
    once.
- **Menu-bar cost readout** — user asked about a persistent, always-
  visible usage summary in the menu bar (not the full Streamlit
  dashboard); discussed, not yet built. Two shapes discussed: a live-
  updating menu-bar title (e.g. `🤖 $0.02 today`) vs. a dropdown item —
  needs a decision before building.
- **Cleanup**: `memory.json` at the repo root (dead legacy file, real
  store is elsewhere), `CampusPilotAgent.app.old-handbuilt` (superseded
  by the py2app-built bundle), `docs/old-launchagent-backups/` — none
  currently causing harm, all candidates for a future tidy-up pass.

## Future

Larger, not-yet-started capabilities, matching the long-term architecture
this project is meant to grow into:

- **Task-based model routing** — `agent/model_router.py`'s `select()`
  already reserves a `context` parameter for this. Intended shape: a
  strong coding model for software-engineering work, a strong reasoning
  model for planning/hard logic, a vision-capable model for visual
  understanding, a voice-capable model for speech interaction, a cheap/
  fast model for simple classification and routing subtasks — chosen per
  task rather than one fixed primary/fallback pair. The system should
  stay model-agnostic where practical.
- **CodingAgent real capability** — currently a stub
  (`metadata={"deferred_to_executor": True}` for everything). Real
  code-editing/execution capability needs to land *on top of* Phase 8's
  subprocess isolation and usage limits, not bypass them — this was an
  explicit design constraint when Phase 8 was built ("before we allow
  long-running coworker agents to do computer/code work, we should make
  agent execution genuinely killable").
- **QAAgent expansion** — today only runs this project's own test suite
  read-only; broader verification capability (checking arbitrary tool
  results, regression review) already has a narrow real capability via
  `agent/verification.py` that a future QAAgent could build on.
- **MCP integration** — no client/server exists yet. If built, tools
  reached through MCP should register through the existing
  `tools/registry.py`, not create a parallel dispatch path (same
  principle already stated for a future Cowork integration).
- **Cowork integration** — `agent/cowork_gateway.py` is an honest stub;
  there is no documented, programmatic Cowork API to integrate against
  yet. Wire it in when one exists, through the registry, keeping Jarvis
  as the orchestrator (any Cowork-originated action still flows through
  `tools.registry`/`agent.autonomy`/`agent.executor`).
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
