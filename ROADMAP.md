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
  Milestone 4 (now reframed as "Phase 9 / M4 — Conversation & History
  Intelligence", audited and split into sub-milestones M4.1-M4.4; all four
  are now complete and committed, M4.1-M4.3 merged to `main`, M4.4 built,
  wired, tested, and CI-verified but shipped **off by default**
  (`proactive_history_enabled=False`) pending real-use evidence — see
  "Completed" below for the full M4.4 entry) is not yet complete;
  OpenClaw interoperability landed between Milestone 3 and Milestone 4,
  see "Completed" above):
  - **Milestone 0 — GitHub Actions CI** (`d3481fc`) ✅: added
    `.github/workflows/tests.yml`, running the full suite
    (`python -m unittest discover -s tests -v`) on every push/PR against
    placeholder (non-functional) API key env vars — no real network calls
    in CI, consistent with this project's mocked-external-call-boundary
    test policy. (The command itself was later updated to add the
    load-bearing `-t .` flag by the Phase 9 Reliability S1 pass — see
    "Completed" above; this entry is kept as an accurate record of what
    Milestone 0 shipped at the time.)
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
- **Phase 9 / M4.1 — Durable History Store + FTS5 Core** (`cd13e2a`) ✅:
  dedicated `~/Library/Application Support/CampusPilot/history.db`
  SQLite database — canonical `history_session`/`history_turn` tables,
  an external-content FTS5 index kept in sync by triggers, write-time
  redaction via `agent.memory.safety.redact_secrets()`, a safe FTS5
  query builder, two independent secure-delete layers (core
  `PRAGMA secure_delete=ON` per write connection + FTS5's own persistent
  `secure-delete=1`, failing closed on an unsupported runtime), and
  read/write/search/status APIs. No automatic capture, no ToolSpecs, no
  backfill — see M4.2 below for the first of those. Pushed,
  CI-verified. See `ARCHITECTURE.md` §12a.
- **Phase 9 / M4.2 — Deterministic History Capture** (`c0d5fc5`) ✅:
  wires real, non-model-controlled capture into `agent/executor.py`'s
  `execute_task_stream()`: a user-turn capture near the top of the
  function (before delegation/routing/planning), an assistant-turn
  capture at every terminal path (completed, cancelled,
  `PartialToolExecution`, both failure branches), using exactly the text
  chunks actually yielded to the caller — never a buffered/delayed
  stream, never a fabricated transcript for a request with zero visible
  output. Session lifecycle: `chat`/`voice` each get one
  process-lifetime session; `scheduled` gets a brand-new session per
  request. History-capture failure is fully isolated (caught, logged as
  a bounded warning, never changes the real task's outcome) and never
  retried (relies on `history_store`'s own idempotency). A real gap was
  found during this pass: the canonical suite command of the time did
  not actually execute `tests/__init__.py`'s package-level guard — fixed
  at the time by extending per-file isolation to every test file that
  exercises a real `execute_task_stream()` call, and fixed at the root
  afterward by the S1 pass below. See `ARCHITECTURE.md` §12b.
- **Phase 9 Reliability S1 — Structurally Safe Test Harness** (`e46f5bd`)
  ✅: built, verified (`tests/_safety.py`, rewritten `tests/__init__.py`,
  `tests/test_test_safety.py`, 49 new meta-tests), committed as
  "Harden test isolation and block external network", pushed, and
  CI-verified (GitHub Actions run `32653067541` — the first attempt
  failed on one pre-existing, S1-unrelated flaky test,
  `test_concurrent_initialization_is_safe`, under real SQLite lock
  contention; a re-run of the same commit succeeded, 1417/1417; the
  underlying root cause was later found and fixed for real by the
  S1.1 follow-up, see "In progress" below). The disk-space exhaustion
  this pass found and flagged was resolved during finalization by
  freeing ~9.4GiB of disposable, reconstructible caches (Homebrew/pip/
  uv/npm caches, browser cache) — zero personal data touched. Graphify
  refreshed against the merged commit (3514 nodes, 7421 edges, 172
  communities, `built_at_commit` matching HEAD, `state: fresh` — accurate
  as of S1's own finalization; superseded by S1.1's later refresh, see
  below). Fixes
  the M4.2-era test-isolation gap at the
  root: the canonical full-suite command changed to
  `python -m unittest discover -s tests -t . -v` (the `-t .` is what
  actually makes `discover` trigger `tests/__init__.py`), which now
  installs a package-level bootstrap redirecting every production
  persistent-store path constant to a disposable per-process temp
  directory, an external-network firewall at the stdlib `socket` layer
  (loopback only; everything else blocked before DNS/connection), a
  secondary `httpx` tripwire, and browser/computer-use tripwires. Found
  and fixed three real production bugs of the "captured a production
  path at definition time, not read dynamically" class this pass was
  designed to catch: `tools/sandbox_python.py`'s Seatbelt profile string
  baked in `SANDBOX_DIR`'s import-time value; `agent/history_store.py`'s
  six public functions and `agent/personal_context.py`'s
  `save_catalog`/`load_catalog` defaulted their path parameter directly
  to the module constant. All three now read the constant dynamically at
  call time. Also found and fixed a macOS-specific bug in the harness
  itself: `tempfile.mkdtemp()`'s default temp root is a symlink, which
  made the real Seatbelt sandbox test spuriously deny a legitimate
  in-sandbox write until the run root was resolved through
  `os.path.realpath()`. Full suite achieved a clean 1417/1417 pass;
  later re-runs flaked on real SQLite disk-I/O errors confined to
  `tests.test_history_store`, traced to this Mac's disk being at ~99%
  capacity and trending down — confirmed environmental (the same module
  flipped between clean and failing purely as free space changed), also
  a real risk to the live production `history.db`, flagged to the user.
  Zero production files touched (before/after metadata comparison across
  every verification run, including the flaky ones). Real Keychain/Obsidian/skill data never
  touched by the canonical suite; a separate opt-in
  `tools/keychain_smoke_test.py` exists for manually verifying the real
  Keychain seam. Full design: `ARCHITECTURE.md` §18. **Recorded as a
  follow-up, deliberately NOT fixed in this pass**: this audit
  (re-)confirmed `agent/memory/manager.py::search_scored()` silently
  persists a `last_accessed` timestamp on every memory retrieval,
  including read-only ones — a real production-quality question (should
  this remain persisted, be batched, become optional, or become
  non-persistent?) still undecided; see "Next" below.

- **Phase 9 Reliability S1.1 — History Store Concurrent Initialization
  Determinism** (`d38e794`) ✅: root-caused and fixed the exact flaky
  test S1's CI run hit (see above): `PRAGMA journal_mode=WAL`'s one-time
  transition on a brand-new database takes an internal exclusive lock
  that does not reliably honor `busy_timeout` — confirmed via
  `sqlite_errorcode == SQLITE_BUSY`, and confirmed to be exactly this
  one statement (not `BEGIN IMMEDIATE`, not any other PRAGMA) by
  isolating each one individually under barrier-synchronized thread
  contention. Fixed with a narrowly-scoped bounded retry around only
  that one statement, only for `SQLITE_BUSY` specifically — a real disk
  I/O failure or any other `OperationalError` still propagates
  immediately. No PRAGMA reordering, no durability/privacy setting
  weakened, ~0.18ms mean overhead in the uncontended case (not
  material). Verified via a 2400-attempt barrier-synchronized stress
  reproduction (0 failures with the fix). New regression coverage: a
  stronger barrier-based version of the original concurrent-
  initialization test, a bounded repeated-round version, a real
  multi-process version (separate OS processes, matching production's
  actual menu-bar/scheduler-daemon/Streamlit scenario), and a new
  `TestHistoryBusySemantics` class exercising the retry-then-succeed,
  retry-then-`HistoryBusy`, never-retry-a-non-busy-error, and (for the
  first time) a genuinely held write lock actually surfacing
  `HistoryBusy` end to end. Committed, pushed, **CI-verified on the
  first attempt** (GitHub Actions run `32659780845`, `run_attempt: 1`,
  1423/1423 passed, no rerun needed) — direct proof the root cause was
  correctly identified. Full design: `ARCHITECTURE.md` §12a.
- **Phase 9 / M4.3 — Read-Only Conversation History Tools** (`1519a51`,
  merged to `main` as `b19f042`) ✅:
  two Jarvis-facing ToolSpecs in `tools/schemas/history.py` —
  `history_status` and `search_conversation_history` (deliberately not
  named `search_history`, to avoid colliding conceptually with
  `agent/memory/manager.py`'s `search_scored()` — History vs. Memory is
  a stated invariant). Both `permission_level=0`, `parallel_safe=True`,
  matching `tools/schemas/graphify.py`'s precedent. Deliberately no
  session/turn direct-retrieval tools (the store has no
  `get_session`/`get_turn` read function; adding one would extend the
  store, not just wrap it — revisit under M4.4 only if actually needed).
  All six `history_store` exception classes map to a distinct,
  stable JSON `state`. `max_results` defaults to 10, hard-capped at 50.
  A real bug found and fixed during review: `int(max_results)` could
  raise an uncaught `ValueError`/`TypeError` out of a permission-0
  read-only tool for a non-numeric value; fixed with explicit
  coercion mapped to the existing `invalid_input` state, plus an
  explicit `is None` check so a real `0` clamps to `1` instead of
  silently becoming the default. New `tests/test_history_tools.py`, 34
  tests. Committed (`1519a51`), pushed, CI-verified on the first attempt
  (GitHub Actions run `32663268361`, 1457/1457 passed). **Merged to
  `main`** via a clean `--ff-only` merge (`d38e794..b19f042`, all 3
  commits preserved), pushed, **CI-verified again on the merged `main`,
  first attempt** (GitHub Actions run `32670629815`, `run_attempt: 1`,
  1457/1457 passed).
- **Phase 9 / M4.4 — Proactive History Retrieval** (`c992432` +
  `6fbc076`, on `main`) ✅: bounded, relevance-gated, provenance-visible,
  cost-aware, opt-in retrieval that surfaces relevant past conversation
  excerpts into the system prompt automatically, without a model or tool
  call deciding whether to look. New `agent/history_context.py`
  (`build_history_context()`), called from `agent.brain.
  build_system_prompt()` right after the memory patterns block. Four new
  `config/settings.py` fields, all `_env_*`-overridable —
  `proactive_history_enabled` (default `False`),
  `history_context_budget_tokens` (default `500`),
  `history_context_timeout_ms` (default `150`),
  `history_context_max_results` (default `3`). **Shipped off by
  default** — same posture as `openclaw_messaging_enabled` — nobody gets
  this behavior until `PROACTIVE_HISTORY_ENABLED=true` is set.
  A real design-premise correction found while writing the tests, not
  shipped quietly: the original justification for the new
  `search_history(busy_timeout_ms=...)` parameter (a normal write
  blocking a normal read for up to 5 seconds) does not reproduce under
  this store's real WAL journal mode — a read-only connection does not
  wait on another connection's open write transaction at all, proven
  empirically. The parameter is kept as defense-in-depth for narrower
  cases (WAL recovery, platform differences), not as a fix for a
  reproduced hazard — see `ARCHITECTURE.md` §12d for the full account.
  The disabled (default) path is proven **byte-identical** to a prompt
  built without the call present at all (`tests/test_brain.py`), and a
  history-store failure (all six `HistoryStoreError` subclasses) is
  proven unable to break a prompt build. New `tests/test_history_context.py`
  (20 tests) + `tests/test_brain.py` (11 tests) + 4 new
  `tests/test_history_store.py` tests. Committed in two steps — foundation
  (`c992432`, "Add bounded proactive history retrieval (inert)") then
  wiring (`6fbc076`, "Wire proactive history retrieval into the prompt
  builder") — both pushed directly to `main`, **CI-verified on the first
  attempt** (GitHub Actions run `32672234602`, `run_attempt: 1`,
  1492/1492 passed). Full design: `ARCHITECTURE.md` §12d.

## In progress

**Phase 10 increment 1 — real CodingAgent + checkpoint/rollback.** Built,
fully tested (1583/1583), and committed as three separate, reviewable
commits: M10.0 (`f8c638a`) first, Phase 10 increment 1 itself (`df26bc0`)
second, docs last — each with the suite green beforehand and CI green on
the first attempt. A structured `/code-review high` pass had found and
fixed six more real
issues — a missing denylist entry, a case-insensitivity bypass of the
whole write denylist, a real timeout-vs-loop-budget mismatch, a stale
model-visible tool description that made the new capability practically
unreachable, a concurrent-writer rollback-protection gap, and **a
pre-existing, already-shipped, live production bug in
`agent/agents/qa.py` unrelated to any of Phase 10's gating** — QAAgent's
real "do the tests still pass?" capability was missing the load-bearing
`-t .` flag, meaning every real invocation ran the actual suite against
real production paths and the real Keychain (**this one fix has since
been committed and pushed separately, `37fb078`, CI-verified**). See
`HANDOFF.md`'s "Structured code review pass" subsection for the full
account. **Then M10.0**: the user independently verified those findings
against the repo and directed a full audit of `agent/agents/worker.py`'s
own gating gap — every coworker agent's real side-effecting call site
was enumerated (five found, across four agents), CodingAgent's
`write_file` was routed through the same permission/autonomy decision
`agent/executor.py`'s `_run_tool` already uses for every registered
tool (ResearchAgent's and MemoryAgent's own pre-existing bypasses
deliberately left as-is this round), and a structural test
(`tests/test_gating_structural.py`) now fails automatically on any new,
undocumented bypass — demonstrated live (a bypass added, the test shown
failing, the bypass removed, the test shown passing again). See
`HANDOFF.md`'s "M10.0" subsection for the full account.
Real, user-authorized dogfooding (turned on via an env var, never the
shipped default) found and fixed five more real bugs — a `.git`-as-
worktree-file assumption, a checkpoint-prune ordering tie, a too-short
API timeout, a truncated-response-treated-as-success gap, and a real
test-isolation gap in `tests/test_agents_coding.py` — plus surfaced, and
in a follow-up pass resolved, one more important finding: a fully
"successful" run's own new test was silently never collected by
`unittest discover` because it didn't match this project's test-writing
convention, and the suite reporting green couldn't detect that class of
problem on its own. Now caught explicitly (a new test file that
collects zero tests is treated as a real verification failure, rolled
back like any other) — deliberately narrower than the ideal check
("collects zero," not "collects too few"), left for a real future need.
See `HANDOFF.md`'s "Real dogfooding pass" subsection for the
full account, including ~$0.296 real spend and how ten stray checkpoint
refs ended up in this real repo (a worktree-isolation gap in the
dogfooding methodology, not the product) and were cleaned up.
`agent/coding_checkpoint.py` (private git-ref checkpoint/rollback) and
`agent/agents/coding.py`'s real `execute()`, gated by `config.settings.
coding_agent_enabled` (default `False` — original stub behavior
unchanged until this is explicitly turned on). Full detail:
`HANDOFF.md`'s dedicated section, `CHANGELOG.md`'s 2026-08-27 entry,
`ARCHITECTURE.md` §4/§12e, `.relay/PHASE10-DESIGN.md`. Increment 1 is
scoped narrower than that design doc allows (Anthropic only, no
"run a named script" tool). The design doc's flagged-as-genuinely-open
concurrent-CodingAgent-runs locking question has since been resolved by
direct reproduction (real multiprocess, not a guess): checkpoint
creation needed no lock, `restore_paths` did, now fixed and covered by
a real regression test — see `ARCHITECTURE.md` §12e. Turning
`coding_agent_enabled` on by default is explicitly gated on real usage
evidence that doesn't exist yet, same posture already applied to
`openclaw_messaging_enabled`/`proactive_history_enabled`.

Otherwise nothing in progress — Phase 9 / M4 (M4.1 through M4.4) is
fully complete and on `main`, all four milestones CI-verified. M4.4 is
intentionally left off by default (`proactive_history_enabled=False`);
turning it on for real use is tracked as a "Next" candidate below, not
in-progress work.

## Next

- **MemoryAgent bypass audit (found by Phase 10's M10.0 pass, not yet
  scoped or started)** — `agent/agents/memory.py`'s `execute()` calls
  `remember()`/`recall()` directly, bypassing `tools.registry`/
  `agent.autonomy` entirely, the same way `agent/research_agent.py`'s
  own internal tool loop already does. Unlike ResearchAgent's version,
  this one was never named or audited as a deliberate exception anywhere
  before M10.0 found it while enumerating every coworker agent's real
  side-effecting call sites — it predates Phase 10 by phases (Phase 7)
  and has simply never been looked at as a permission question until
  now. `agent/memory/safety.py`'s content filter still applies (it's
  inside `agent.memory.remember` itself, a layer below the registry) —
  but that is a content filter, not a permission gate, and the
  registry/autonomy gate is genuinely bypassed. Now documented as an
  accepted-but-unaudited exception in `CLAUDE.md` rule 3 and structurally
  tracked by `tests/test_gating_structural.py`'s
  `ACCEPTED_UNGATED_CALL_SITES` (so it can't silently disappear from
  view again) — not fixed. Whether it needs the same
  `should_request_confirmation` treatment `agent/agents/coding.py`'s
  `write_file` just got, or is fine as-is given the content filter and
  MemoryAgent's narrower blast radius (a fact recorded, not something
  wiped), is an open question for a future pass, not a bug fix to
  reflexively apply.

**Phase 9 / M4A — Conversation & History Intelligence Architecture
Audit is complete** (an audit-and-design-only pass, delivered as an
in-conversation report — no committable artifact of its own beyond the
recommendation M4.1/M4.2 implement). It inventoried every existing
history/memory/state store, traced conversation flow across every UI,
audited the typed memory system and execution/audit history, empirically
proved SQLite/FTS5 capability on this project's real runtime, and
recommended the dedicated-SQLite-database design M4.1 built. Full
milestone breakdown (M4.1 through M4.4, each independently gated — **all
four now complete**, see "Completed" above for full detail on each):

- **M4.1 — Durable History Store + FTS5 Core** ✅ complete, committed
  (`cd13e2a`), pushed, CI-verified.
- **M4.2 — executor capture wiring** ✅ complete, committed (`c0d5fc5`).
  Voice/menu-bar conversations become durable starting here (a product
  decision already made).
- **M4.3 — Jarvis-facing search tools** ✅ complete, merged to `main`
  (`1519a51`, merge `b19f042`). Registers `search_conversation_history`/
  `history_status` ToolSpecs through `tools/registry.py` (never a
  special-cased dispatch path) so Jarvis itself can answer "what did we
  decide about X" / "what was I working on yesterday" questions. Named
  `search_conversation_history`, not the originally-sketched
  `search_history` — the longer, disambiguated name was chosen
  deliberately so the Jarvis-facing tool surface never reads as
  conceptually adjacent to `agent/memory/manager.py`'s `search_scored()`,
  given History vs. Memory is a stated architectural invariant, not a
  naming afterthought.
- **M4.4 — proactive context injection** ✅ complete, on `main`
  (`c992432`, `6fbc076`). Bounded, relevance-gated, provenance-visible,
  cost-aware, opt-in retrieval that surfaces relevant history
  automatically rather than only on explicit search. **Shipped off by
  default** — see "Completed" above and `ARCHITECTURE.md` §12d.
  **Turning it on for real use is the open next step**, not yet
  scheduled: the evidence worth collecting first is real
  `history_retrieved` log volume/relevance from someone running with
  `PROACTIVE_HISTORY_ENABLED=true` for a while (are the top-3 FTS hits
  actually relevant, does 500 tokens feel right, does 150ms ever
  actually matter) — not a code change, a usage-observation period. No
  target date; revisit once M4.3's tools have seen some real use too,
  since both draw on the same store.

`conversation.json` backfill (a product decision already made — it
*will* eventually happen, just not as part of M4.1/M4.2) and history
retention (defaults to indefinite; no automatic age-based deletion
planned without a separate, explicit design pass) are both still open
for whichever milestone ends up needing them, not yet scheduled to a
specific one.

- **"Say hi" → doubled greeting text, investigated, partially fixed.**
  Found during M4.3's live E2E proof (`.relay/report-2.md`): a bare
  one-word greeting led the model to call `get_system_status` and
  `get_weather` before replying (two provider round-trips, ~$0.0017 for
  that exchange — genuinely trivial as a cost question on its own) and
  produced a doubled "Hello, master." in the streamed reply: the model
  narrated a lead-in sentence in the first completion, then produced the
  ENTIRE templated greeting (including "Hello, master." again) fresh in
  the second completion once tool results came back — two separate
  visible replies from the user's perspective, not a capture bug (M4.2
  faithfully recorded exactly what was streamed).
  - **Root cause, confirmed live** (two real `execute_task("say hi",
    source="chat")` calls, ~$0.0034 total): the assistant narrates
    before a tool call by default; the greeting instruction's rigid
    template ("respond with a greeting in this shape: ...") is what the
    model re-applies fresh in the tool-result completion, without
    remembering its own first-completion text already said something.
  - **Fixed**: `agent/brain.py`'s greeting instruction now explicitly
    forbids narration before the tool calls and forbids saying "Hello,
    master" until the single final reply. **Confirmed this eliminates
    the literal doubled phrase** — neither live retest said "Hello,
    master" twice. **Did NOT fully eliminate a lead-in sentence** — even
    after strengthening the instruction to be maximally explicit ("ZERO
    text before them... no matter how short"), `claude-haiku-4-5`
    (this task type's routed model) still narrated once each time
    ("I'll get the real time and weather for you." / "I need to get the
    current time and weather for you first.") before calling the tools.
    Two real, live-tested attempts is where this stopped — a prompt
    instruction alone does not reliably suppress this model's narration
    tendency, and a third blind retry with real API spend wasn't a good
    trade for marginal, unverified improvement.
  - **Open remainder, if it's ever worth closing further**: a genuinely
    reliable fix likely needs a structural change, not another prompt
    tweak — e.g. gathering `get_system_status`/`get_weather` server-side
    before the model's first completion for a detected greeting, so the
    model only ever produces ONE completion with the data already in
    hand, never a narrate-then-tool-call round trip. That is a real
    change to the executor's request-shaping, not a "reflexive prompt
    tweak," and is exactly why this stops here as a documented, honest
    partial fix rather than an unscoped attempt at a bigger rewrite.
    `tests/test_brain.py`'s `TestGreetingInstructionForbidsNarrationBeforeToolCalls`
    pins the current instruction text so it can't silently regress.

Other candidates raised but not yet started, roughly in order of what's
been discussed most recently:

- **Walmart account integration (raised, NOT approved, NOT scoped)** — the
  user asked about letting Jarvis read purchase history, build a cart, and
  check out on confirmation. Recorded here so the thinking isn't lost; no
  design pass has been done and none is scheduled.
  - Walmart has no public consumer API and no official MCP server. The
    third-party MCP servers that exist work by storing session cookies —
    a credential-handling risk and a plausible route to an account flag.
    If this is ever built, browser automation in Jarvis's own owned Chrome
    profile fits the existing architecture instead (`agent/browser_lock.py`
    and Playwright already exist from Phase 9 M1).
  - Permission mapping under this repo's real `LEVEL_NAMES`: reading order
    history is read-only, cart building is level 3 (external communication —
    it mutates state on a third-party account), checkout is level 4
    (financial) and must stay confirmation-gated.
  - **Correction worth keeping.** An outside suggestion proposed sourcing
    order history from Gmail "since you already have that connector."
    **Jarvis does not.** `tools/schemas/logins_and_email.py` registers
    `fill_login`, `confirm_login`, `draft_email`, `send_email` — all
    outbound or auth. There is no read/search-inbox tool anywhere in
    `tools/schemas/`. The Gmail connector in question belongs to the Cowork
    session talking to the user, not to Jarvis. So "just read the
    confirmation emails" is not a cheap first phase here; it would require
    building inbound email reading first, which is its own milestone with
    its own privacy surface.
  - If ever built: three separable phases (read-only history → cart →
    checkout), each independently testable. Expect real bot detection —
    surface a CAPTCHA or account hold to the user rather than retrying
    silently. Log every level 3-4 action through `agent/audit.py` so there
    is an audit trail, not just a permission gate.

- **Inbound Gmail read/draft tool (raised, NOT approved, NOT scoped)** —
  the capability gap the Walmart entry above already names: Jarvis has no
  read/search-inbox tool anywhere in `tools/schemas/` today, only outbound
  (`send_email`) and auth (`fill_login`/`confirm_login`) actions. A
  reference implementation (`.relay/reference/gmail_tool.py`, from the
  same user-supplied second Jarvis implementation as the Telegram entry
  above — see `JarvisVault/Knowledge/Decisions/Second-Jarvis-Zip-Rejection.md`)
  reads inbox messages and drafts replies, with send gated behind an
  interactive human-only confirmation — read for ideas, do not import.
  If ever built, it must become `ToolSpec`s through `tools/registry.py`
  like every other tool, never a standalone CLI that bypasses the
  registry (invariant 4.2): read at permission level 0, draft at level 2,
  send at level 3 (external communication), matching this repo's real
  `LEVEL_NAMES`. Not started, not scoped.

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
  decided) after this pass is reviewed. A reference implementation
  (`.relay/reference/telegram_bot.py`, from a user-supplied second
  Jarvis implementation evaluated and not adopted as a stack — see
  `JarvisVault/Knowledge/Decisions/Second-Jarvis-Zip-Rejection.md`) is
  available to read for ideas, not to import: its owner-chat-ID
  whitelist (rejecting messages from anyone but the user's own Telegram
  account) is the right instinct and load-bearing if ported. Still
  blocked on the user creating a bot token; this doesn't change that.
  Still to hold for every future
  OpenClaw milestone: no OpenClaw model-routing authority, no arbitrary
  OpenClaw-initiated Jarvis tool execution, no shared secrets/memory
  store between the two systems, no third-party OpenClaw plugin
  dependency (OpenClaw plugins execute with full host privileges, no
  sandboxing — confirmed in the M0 audit), no `node.invoke`/device
  capabilities.
- **Memory `last_accessed` write-back on read** — `agent/memory/
  manager.py::search_scored()` silently persists an updated
  `last_accessed` timestamp (via `store.save_all(memories)`) on every
  call that returns at least one result, including calls that are
  conceptually read-only. This runs on every real request, since
  `agent.brain.build_system_prompt()` → `agent.context.
  build_profile_context()` calls it unconditionally. Found during the
  Phase 9 reliability audit and reconfirmed during the S1 implementation
  pass; deliberately **not** changed in either — this is a real,
  standalone production-quality question, not a test-isolation issue
  (S1's store redirection means canonical tests no longer touch the real
  `memory.json` because of it, so it's no longer a test-safety risk,
  just a design question). Still undecided: should `last_accessed`
  remain persisted on every read, be batched/debounced, become optional,
  or become non-persistent entirely? Needs a deliberate decision, not a
  reflexive fix.
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

- **QAAgent expansion beyond today's test-suite check and coworker-
  result verification** — Milestone 3 (complete, see above) added
  `agent/verification.py`'s `verify_agent_result()`, evaluating whether
  a coworker's result actually holds up (explicit failure, cancellation,
  agent-reported `verification_status`, plus a bounded source-evidence
  heuristic for ResearchAgent). Deliberately NOT extended to FILES/
  BROWSER-shaped checks (e.g. "does the expected file exist," "does the
  resulting page show X") this milestone: QAAgent's own non-test-suite
  path still defers entirely — building that verification now would be
  speculative, unused code. CodingAgent (Phase 10 increment 1, off by
  default, see "In progress" above) now produces a test-suite-shaped
  result of its own, but that already goes through
  `verify_agent_result()`'s new `suite_exit_code` check directly, not
  through this FILES/BROWSER gap. Revisit this item only if a coworker
  produces an actual file-existence/page-content result to check.
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
- **Low-disk health monitoring/alert** — the Phase 9 Reliability S1
  pass (and its finalization) found this Mac's disk reaching complete
  exhaustion (0 bytes free) during a test run, which caused real SQLite
  `disk I/O error`/`HistoryBusy` failures — a risk that applies equally
  to the live production `history.db`'s own writes, not just to testing.
  No automatic handling exists today (deliberately not built as part of
  S1 — out of scope for a test-safety pass). A future improvement would
  be some form of low-disk-space detection/health signal surfaced to the
  user (e.g. via `get_system_status` or the dashboard), not automatic
  deletion or cleanup behavior. Not started, not designed in detail —
  maintain reasonable free-space headroom operationally in the meantime.

## Experimental

Nothing currently in an experimental/prototype state. (Reserved section —
use this for genuinely speculative work-in-progress that isn't ready to
be called "Next," not as a dumping ground for every idea.)
