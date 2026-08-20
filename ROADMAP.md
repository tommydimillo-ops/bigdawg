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
  Milestone 4 has not started; OpenClaw interoperability is planned to
  land between Milestone 3 and Milestone 4, see "Next" below):
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
  - **Milestone 3 — QA / verification expansion + bounded parallel
    coworker delegation** ✅: `agent/agents/manager.py`'s
    `execute_agents_parallel()` runs 2+ genuinely independent coworker
    subtasks concurrently, bounded to `settings.max_parallel_agents = 3`
    — a batch larger than that is rejected outright (no subprocess
    spawned), never silently truncated. Each subtask still goes through
    the existing `execute_agent()` unchanged (same subprocess isolation,
    same per-task depth/registered/enabled/cancellation checks — no
    second dispatch path). `delegate_parallel_tasks` (new tool,
    deliberately **not** `parallel_safe`) is the only entry point;
    `consult_coworker_agent` (single-task) is untouched. Subtasks marked
    `required=True` (the default) must succeed for the batch to count as
    successful; a failed `required=False` subtask degrades the batch to
    `PARTIAL` instead of `FAILED`. A failed subtask gets one bounded
    retry (`settings.max_agent_batch_retries = 1`), never applied to an
    already-cancelled task. `agent/verification.py`'s new
    `verify_agent_result()` evaluates each coworker's actual result
    (explicit failure, cancellation, or an agent-reported
    `verification_status` of `"failed"` — e.g. QAAgent's own test-suite
    check — all override a nominal `success=True`), plus a bounded,
    objective source-evidence heuristic for ResearchAgent specifically.
    `agent/agents/manager.py`'s `execute_agent()` gained genuine
    mid-flight cancellation: a cancelled parent request now terminates
    (`SIGTERM`, bounded grace period, `SIGKILL` fallback) an
    already-running coworker subprocess instead of only refusing to
    start new ones — verified against a real, separate OS process, not
    just a mock. `agent/audit.py`'s action log gained an `fcntl.flock`
    for cross-process write safety, since parallel coworker subprocesses
    can now genuinely write to it at once. `agent/research_agent.py` —
    the only coworker that directly makes an LLM call — now routes
    through the same `classify_task()`/`build_fallback_chain()` M2
    primitives the outer request uses, inheriting capability/health/
    budget filtering, cost-aware tiering, and cross-provider fallback
    instead of always calling Claude directly. See `CHANGELOG.md` for
    full detail.
- **OpenClaw interoperability / gateway bridge** (a separate,
  intermediate initiative, not a Phase 9 milestone — sequenced between
  Phase 9 Milestone 3 and Milestone 4):
  - **OpenClaw M0 — research/architecture audit** ✅: researched OpenClaw
    (a real, separate, MIT-licensed open-source project —
    github.com/openclaw/openclaw, docs.openclaw.ai) against its current
    official documentation. Confirmed real, current findings: Intel
    x86_64 macOS is supported (this development Mac's own architecture);
    the Gateway's documented local default is an *authenticated loopback
    WebSocket* (`ws://127.0.0.1:18789`), not TLS; current protocol
    version 4; a 7-scope operator authorization model where
    `health`/`status`/`node.list` need only the minimal `operator.read`
    scope; plugins execute with full host privileges, no sandboxing
    ("treat plugin installs like running code," per OpenClaw's own
    docs) — a real reason M1 depends on zero third-party OpenClaw
    plugins. No code changed in this pass.
  - **OpenClaw M1 — read-only Gateway bridge, device-identity
    authenticated** ✅: `agent/openclaw_gateway.py` (new) — a narrow,
    optional bridge making no Jarvis policy decisions of its own.
    One-shot connections (`websockets.sync.client`, fitting Jarvis's
    existing synchronous tool architecture with no asyncio adapter
    needed), a fixed, hard-coded RPC allowlist (`health`/`status`/
    `node.list` only — every other method is structurally unreachable).
    Auth was initially built on a shared-token assumption, then
    corrected after a follow-up review found current official OpenClaw
    docs actually require a persistent **Ed25519 device identity** with
    a challenge-signed handshake for normal third-party operator
    clients — verified directly against the real, published
    `@openclaw/gateway-client`/`@openclaw/gateway-protocol` npm packages
    (downloaded and inspected, not paraphrased, since this specific
    flow is undocumented on docs.openclaw.ai and a related GitHub
    issue's own technical claims were partly stale). Jarvis now holds
    its own persistent Ed25519 identity (`OPENCLAW_DEVICE_PRIVATE_KEY`,
    via `agent/secrets.py`), signs the real, verified V3 device-auth
    payload against the Gateway's `connect.challenge`, verifies
    `operator.read` was actually granted (fails closed if not), never
    auto-approves a `PAIRING_REQUIRED` response (a human runs
    `openclaw devices approve <requestId>`), and persists/reuses an
    issued device token with one bounded fallback-to-bootstrap-token
    retry on `AUTH_DEVICE_TOKEN_MISMATCH`. New dependency:
    `cryptography==50.0.0` (Ed25519 key generation/signing; verified
    Intel macOS + Python 3.14 compatible, though no pre-built wheel
    existed yet for this exact combination — it compiles from source
    cleanly). Two tools, `openclaw_status`/`openclaw_list_nodes`
    (`tools/schemas/openclaw.py`), both permission_level 0, read-only,
    unattended-allowed, flowing through the ordinary `tools/registry.py`
    path — no second dispatch path. Disabled by default
    (`openclaw_enabled = False`); every failure mode (not installed,
    stopped, misconfigured, pairing required, wrong scope, stale device
    token) degrades cleanly, never a Jarvis startup failure. Re-verified
    twice more after the initial pass: first against a newer beta npm
    release (a claimed `signedAt` bug was checked and found to contradict
    real client source — no change made — while that same pass introduced
    its own auth-field bug); then against the actual current STABLE
    `openclaw` app package (`openclaw@2026.7.1-2`) rather than the
    beta-only client/protocol packages, which turn out to have no stable
    release at all. That final pass fixed the auth-field bug for real
    (shared token → `auth.token`; stored device token → `auth.deviceToken`;
    `auth.bootstrapToken` never used — confirmed against the Gateway
    server's own connect-auth resolution, not just client-side schema
    field existence) and fully CONFIRMED the device-ID derivation
    algorithm (previously a documented low-risk assumption) against a
    literal server-side function doing the exact same computation. All
    of this was protocol-verified against a local fake Gateway server
    (real Ed25519 signature verification, not a stub).
  - **OpenClaw M1.5 — real loopback Gateway smoke test** ✅: validated
    the above against an actual, isolated, temporary
    `openclaw@2026.7.1-2` process — real `openclaw_status`/
    `openclaw_list_nodes` succeeded through Jarvis's own tool-registry
    path (protocol 4, `operator.read` only). Found and fixed two real
    bugs the fake-server/source-reading approach had missed
    (`client.platform` and `client.deviceFamily` both required on the
    wire, not just in the signed payload); corrected the fake test
    server's signature verification to reconstruct from actual captured
    wire values so this class of bug is now caught locally. No daemon or
    permanent OpenClaw installation exists on this machine. See
    `CHANGELOG.md` for full detail.
  - **OpenClaw M2 — outbound text messaging bridge** ⏳ under review (no
    real channel configured yet): `agent/openclaw_messaging.py` (new) —
    the real Gateway `send` RPC, never `chat.send`
    (`ChatSendParamsSchema` requires a `sessionKey` and is part of
    OpenClaw's own agent/session execution surface, confirmed via real
    source, not this bridge's concern). A SEPARATE `operator.write`
    device identity from M1's `operator.read` one
    (`OPENCLAW_MESSAGE_DEVICE_PRIVATE_KEY`/
    `OPENCLAW_MESSAGE_DEVICE_TOKEN`), implemented as a small closed
    `_Profile` type in `agent/openclaw_gateway.py` (exactly two
    instances; no public API for a caller-supplied scope list); each
    profile's RPC allowlist is independently exact (`_READ_PROFILE`:
    `{health, status, node.list}`; `_MESSAGE_PROFILE`: `{send}`).
    Deterministic Jarvis-side channel/target allowlists
    (`openclaw_messaging_enabled` defaults `False`,
    `openclaw_allowed_channels`/`openclaw_allowed_targets` default
    empty — no wildcards, no fuzzy matching, no OpenClaw-side name
    resolution). Text-only first pass, capped at 4000 characters,
    rejected (never truncated) if oversized. A fresh, internally-
    generated `idempotencyKey` per send; at most ONE transmission per
    logical send, always. A genuinely uncertain delivery (request
    transmitted, no trustworthy response) is reported as such and never
    automatically retried — a same-day hardening pass removed an
    earlier same-key retry after review found the real Gateway's
    in-memory dedupe cache does not survive a Gateway process restart,
    so a resend isn't provably safe (see `CHANGELOG.md`'s hardening
    entry). Messaging pairing normalized separately from M1's own, never
    auto-approved. One new tool, `send_message_via_openclaw`
    (permission_level=3, side_effect=True,
    requires_live_confirmation=True, matching `send_email`'s existing
    external-communication convention; exactly `channel`/`target`/
    `message`, no `account_id`/`thread_id` — narrowed out in the
    hardening pass since neither has its own independent allowlist
    yet). A dedicated `agent/verification.py` verifier parses this
    tool's JSON result directly so an uncertain/failed delivery is never
    mistaken for confirmed success. See `CHANGELOG.md` for full detail
    and exact current test counts.

- **Graphify G0 — development codebase graph baseline.** Evaluated and
  approved Graphify (`graphifyy` on PyPI, `graphify` CLI,
  `Graphify-Labs/graphify` on GitHub, v0.9.47) as an optional,
  supplementary, development-time structural-intelligence layer — never
  Jarvis's source of truth, never a permission/autonomy authority, never
  a runtime dependency. Installed isolated via `uv tool install
  graphifyy` (CampusPilot's own `.venv`/`requirements.txt` untouched).
  Built a code-only graph (`graphify extract . --code-only`, then
  `graphify cluster-only . --no-label` for the report — zero LLM/API
  calls, zero paid extraction): 3024 nodes, 6407 edges, 163 communities,
  96% EXTRACTED / 4% INFERRED edge confidence, zero import cycles.
  Manually cross-checked Graphify's `explain`/`path`/`affected` output
  against real source across 8 architectural areas (orchestration, tool
  registry, autonomy, provider routing, coworker system, OpenClaw, voice
  pipeline, memory/Obsidian) — mostly accurate down to the exact line
  number, and independently corroborated three separate architectural
  claims already documented in `CLAUDE.md` purely from static analysis.
  Found and documented two concrete, verified limitations: a
  same-basename module-collision false positive
  (`tools/registry.py` vs. `agent/skills/registry.py`), and a
  system-wide miss of the `register(ToolSpec(..., handler=...))`
  registration-wiring pattern that is this codebase's core tool-wiring
  mechanism. Full detail, rebuild commands, and the complete limitations
  list: `docs/GRAPHIFY.md`. Generated graph artifacts
  (`graphify-out/`) are gitignored and kept local, not committed — see
  `docs/GRAPHIFY.md` for why. No MCP integration, no Claude Code hooks,
  no CLAUDE.md changes, no Jarvis runtime integration in this pass.

- **Graphify G1 — four narrow, read-only Jarvis code-graph tools.**
  `agent/code_graph.py` (new): a standard-library-only reader over the
  locally generated `graphify-out/graph.json` — never imports
  `graphifyy`, never invokes the `graphify`/`graphify-mcp` executables;
  the only subprocess call in the module is a fixed-argv, `shell=False`
  `git rev-parse HEAD`/`git status --porcelain --untracked-files=no`
  (bounded timeout, pinned cwd) used to decide graph freshness.
  Registered through the normal `tools/registry.py` path
  (`tools/schemas/graphify.py`, no separate dispatch path):
  `code_graph_status`, `search_code_graph`, `analyze_code_impact`,
  `find_code_path` — all `permission_level=0`, `side_effect=False`,
  `unattended_allowed=True`, `parallel_safe=True`. Staleness is
  enforced, not advisory: a graph must be built at the current git HEAD
  with a clean tracked working tree (untracked files, including
  `graphify-out/` itself, never count as dirty) to be used at all;
  search/impact/path all refuse with a structured stale/unavailable/
  invalid result otherwise, and nothing auto-rebuilds a stale graph.
  Every result carries `authoritative: false` and G0's known-limitations
  list; results touching `tools/registry.py`/`ToolSpec`/
  `tools/schemas/`/`agent/autonomy.py`/permission or credential code
  additionally carry `source_verification_required: true`. Bounded
  throughout (search ≤20 results, impact ≤depth 3/≤100 results, path
  ≤depth 10, one shortest path) — no giant graph dump ever reaches model
  context. No caller-supplied filesystem path, CLI subcommand, or raw
  query surface exists; no fifth generic "run a graph command" tool.
  `graphifyy` remains outside `requirements.txt` and CampusPilot's
  `.venv`. Validated against a small synthetic fixture suite (no real
  `graphifyy`/network needed in CI) and, read-only, against the real
  local graph — which the tool correctly reported as stale (built at the
  prior commit, working tree modified by this same implementation work),
  proving the fail-closed staleness path for real rather than only in a
  mock. Full detail: `docs/GRAPHIFY.md`.

- **Graphify G1.1 — incremental-vs-full extraction consistency audit.**
  A narrow reliability investigation, prompted by a real accuracy
  question found while validating G1 against the live graph. Confirmed,
  via two isolated local clones (`git clone --local --no-hardlinks`) —
  one reproducing the exact old-commit-to-G1 incremental transition, one
  a clean extraction with no prior cache at the same commit — that
  Graphify 0.9.47's incremental mode can silently omit a direct
  per-symbol `imports` edge when a newly-added Python file imports a
  named symbol from an old/unchanged (cached) module, even when
  `built_at_commit`/clean-tree/`fresh` all check out. Confirmed on two
  independent instances (`tools/schemas/graphify.py` → `ToolSpec`/
  `register`; `tests/test_graphify_tools.py` → `_run_tool`) and ruled
  out a one-file fluke via four unrelated peer-module controls, all
  clean. Every node and every `contains`/`calls`/`inherits`/`method`
  relation for the new G1 files was 100% identical between modes —
  narrow, not broad. Read (never modified) the installed Graphify
  source and identified the likely mechanism: incremental extraction
  shares "unchanged corpus" context with several call-resolution passes
  but not with the plain `from X import Y` symbol-binding pass. Replaced
  the live local graph with a clean full rebuild (old graph backed up
  outside the repo, validated, backup then deleted): 3157 nodes, 6650
  edges, 152 communities. No `agent/code_graph.py`/ToolSpec/test change
  — its existing `authoritative: false` design already covers this
  regardless of extraction mode. Documented in `docs/GRAPHIFY.md`: the
  finding, a terminology clarification that `fresh` never implies
  structural completeness, and a 5-step precision rebuild workflow.

## In progress

- Nothing mid-flight as of the last session update — see `HANDOFF.md`
  for the authoritative current state (this section will drift faster
  than this file gets updated; `HANDOFF.md` is the source of truth for
  "right now").

## Next

**Designated next major milestone: Phase 9 / M4 — Conversation &
History Intelligence.** Not started. Its currently-known scope
(carried forward from the prior "Phase 9 Milestone 4 — FTS5
conversation/history search" framing, not yet re-scoped under the new
name): full-text search over conversation/execution history (see
`agent/conversation_store.py`/`agent/execution_history.py` for the
current, non-searchable stores this would index) using SQLite's FTS5
extension rather than a new external search dependency. Sequenced after
OpenClaw interoperability and the Graphify G0/G1/G1.1 work, both now
complete. Not designed in detail yet — treat as a fresh planning
conversation with the user before implementing anything.

Other candidates raised but not yet started, roughly in order of what's
been discussed most recently:

- **Graphify G2 (not yet scoped)** — possible future direction: MCP
  exposure, Claude Code hooks, or automatic graph regeneration, none of
  which G1 implements or assumes. Not started, not approved; would need
  its own explicit scoping the same way G0 → G1 each did.
- **Provider admin-key cost reconciliation** — link real OpenAI/Anthropic
  Admin API keys (checked live, confirmed the project's regular keys
  can't reach either provider's usage/billing endpoint) to reconcile
  `agent/usage.py`'s *estimated* costs against actual billed amounts.
  Explicitly deferred by the user pending them generating the admin keys
  themselves ("it's okay for now").
- **OpenClaw M2 follow-up — a real messaging channel.** M2's own
  outbound-send implementation is complete and under review (see
  "Completed" above) — text only, no real channel configured or tested
  yet. The next increment is choosing and configuring the FIRST real
  channel (Telegram is the presumed first candidate but not yet
  decided) after this pass is reviewed. Still to hold for every future
  OpenClaw milestone: no OpenClaw model-routing authority, no arbitrary
  OpenClaw-initiated Jarvis tool execution, no shared secrets/memory
  store between the two systems, no third-party OpenClaw plugin
  dependency (OpenClaw plugins execute with full host privileges, no
  sandboxing — confirmed in the M0 audit), no `node.invoke`/device
  capabilities.
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
- **QAAgent expansion beyond today's test-suite check and coworker-
  result verification** — Milestone 3 (complete, see above) added
  `agent/verification.py`'s `verify_agent_result()`, evaluating whether
  a coworker's result actually holds up (explicit failure, cancellation,
  agent-reported `verification_status`, plus a bounded source-evidence
  heuristic for ResearchAgent). Deliberately NOT extended to FILES/
  BROWSER-shaped checks (e.g. "does the expected file exist," "does the
  resulting page show X") this milestone: no current coworker agent
  produces that shape of result to check yet (CodingAgent still defers
  entirely; QAAgent's non-test-suite path still defers too) — building
  that verification now would be speculative, unused code. Revisit once
  a coworker actually produces file/browser-state results to check.
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
