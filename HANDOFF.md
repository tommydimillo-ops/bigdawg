# HANDOFF — Jarvis current state

**Read this after `CLAUDE.md`.** This file is the single source of truth
for "what's going on right now" — it will drift out of date faster than
the other docs; if anything here contradicts the actual code or git
state, trust the code (see `CLAUDE.md`'s NEW SESSION PROTOCOL) and fix
this file.

Last updated: 2026-08-23. **Phase 9 / M4 (Conversation & History
Intelligence) is now fully complete — all four sub-milestones (M4.1
through M4.4) are committed, on `main`, CI-verified.** `main` HEAD is
`6fbc076c6f51e806c6d1492ede10c0d153d532bc` ("Wire proactive history
retrieval into the prompt builder"). This is a correction of every
earlier version of this paragraph, which described M4.3 as stuck on a
feature branch and M4.4 as not started — both are done (see CLAUDE.md's
NEW SESSION PROTOCOL — trust `git log` over this file when they
disagree; that protocol is exactly why this correction happened rather
than silently trusting a stale file).

In commit order on `main`: **M4.1** (durable history store + FTS5 core,
`cd13e2a`), **M4.2** (deterministic history capture, `c0d5fc5`), **Phase
9 Reliability S1** (structurally safe test harness, `e46f5bd`, CI run
`32653067541`), **S1.1** (history store concurrent initialization
determinism, `d38e794`, CI-green on the first attempt, run
`32659780845`), **M4.3** (read-only conversation-history ToolSpecs,
`1519a51` + `d78ba09`, merged to `main` via a clean `--ff-only` merge as
`b19f042`, CI-green on the merged `main` on the first attempt, run
`32670629815`), and **M4.4** (proactive history retrieval, `c992432` +
`6fbc076`, CI-green on the first attempt, run `32672234602`,
**1492/1492 tests passed**). M4.4 ships **off by default**
(`proactive_history_enabled=False`) — see the dedicated "Phase 9 / M4.4"
section below for what it does, the WAL-timeout finding, and what it
deliberately does not build. OpenClaw M1/M1.5/M2 (including the M2
hardening/review pass) and Graphify G0/G1/G1.1 all remain **complete,
committed, pushed, and CI-verified** from prior sessions — see their own
dedicated sections below; nothing about them changed this round.

This same session then ran **Graphify G0** — evaluating and approving
Graphify as an optional, local, development-time structural-code-graph
tool for this repo (not a Jarvis runtime subsystem). G0 finalized as
commit-worthy documentation only (`.gitignore` entry, `docs/GRAPHIFY.md`,
and these project records) — no Jarvis source file changed — and is
committed as `7b4d0b6b2fecdd3264d8a5f48b3babcc1c5ee295` ("Document local
Graphify development integration"), pushed, CI-verified (GitHub Actions
run `32312885286`, success).

A later session implemented **Graphify G1** — four narrow, read-only
Jarvis tools over the locally generated Graphify graph
(`agent/code_graph.py`, `tools/schemas/graphify.py`) — **now complete,
committed as `c99e7928b34ee11de803a23fae2fc819d532b620`** ("Add
read-only Jarvis code graph intelligence"), pushed, CI-verified (GitHub
Actions run `32418542989`, success). The `graphify`/`graphifyy`
package/executable is still never invoked from Jarvis runtime; only the
graph *data* it already wrote is read, with the standard library only.

A later session then ran **Graphify G1.1** — a narrow reliability audit
triggered by an observation from G1's own real-graph validation (a
possible incremental-extraction completeness gap). Confirmed real and
reproducible via a controlled two-clone experiment; documented in
`docs/GRAPHIFY.md` (no `agent/code_graph.py` change was needed — its
`authoritative: false` design already covers this). The live local
graph was replaced with a clean full rebuild during the audit, and that
finding's documentation-only finalization is committed — see the
"Graphify G1.1" section below and `CHANGELOG.md`'s dated entry for the
exact commit hash.

## Phase 9 / M4.1 — HISTORY STORE ✅ COMPLETE, COMMITTED, PUSHED, CI-VERIFIED

- **M4A (audit)**: complete. Design-only pass, no code, no commits —
  delivered as an in-conversation architecture report recommending a
  dedicated SQLite database over another JSON file or piggybacking on
  `agent/personal_context.py`'s read-only pattern.
- **M4.1 (implementation + hardening)**: committed as `cd13e2a` ("Add
  durable FTS5 conversation history store"), pushed, CI-verified
  (GitHub Actions run `32577096526`, success). New files:
  `agent/history_store.py`, `tests/test_history_store.py` (83 tests).
  Owns `~/Library/Application Support/CampusPilot/history.db` —
  canonical `history_session`/`history_turn` tables, an external-content
  FTS5 index kept in sync by triggers, **two independent secure-delete
  layers** (core `PRAGMA secure_delete=ON` per write connection + FTS5's
  own persistent `secure-delete=1`, failing closed via
  `HistoryUnsupportedRuntime` on an unsupported runtime), write-time
  redaction reusing `agent.memory.safety.redact_secrets()`, a safe FTS5
  query builder, and six public functions: `initialize_history_store`,
  `create_session`, `close_session`, `record_turn`, `history_status`,
  `search_history`. Full detail: `ARCHITECTURE.md` §12a,
  `CHANGELOG.md`'s two 2026-08-22 entries.
- **What M4.1 deliberately did not do** (M4.2 below implements the
  first of these): no automatic capture, no backfill of
  `conversation.json`, no Jarvis-facing ToolSpec, no proactive context
  injection, no automatic age-based deletion.

## Phase 9 / M4.2 — HISTORY CAPTURE ✅ COMPLETE, COMMITTED (`c0d5fc5`)

**This section previously said "BUILT, TESTED, UNCOMMITTED" — that went
stale. M4.2 was committed as `c0d5fc5` ("Add deterministic conversation
history capture") before this update.** The "central guard doesn't
actually execute" finding described below is real and was the correct
diagnosis at the time, but the FIX described (per-file isolation only)
was superseded by the Phase 9 Reliability S1 pass (see the dedicated
section right after this one), which fixed the central guard itself —
`tests/__init__.py` now reliably runs under the canonical
`-t .` command. The rest of this section is kept as an accurate
historical record of the M4.2 pass.

- **What it does**: new `agent/history_capture.py` makes M4.1's store
  operational — deterministically, non-model-controlled capture of real
  Jarvis interactions, wired into `agent/executor.py`'s
  `execute_task_stream()` at fixed control-flow points (a user-turn
  capture near the top, an assistant-turn capture at every one of the
  four real terminal paths). **No ToolSpec decides this — there is no
  write-history tool, and none of the capture points depend on anything
  a model said or chose.** Session lifecycle: `chat`/`voice` each cache
  one process-lifetime session; `scheduled` never caches, one session
  per request. Capture failure is fully isolated (caught, logged as a
  bounded warning, never changes the real task's outcome) and never
  retried. Full detail: `ARCHITECTURE.md` §12b, `CHANGELOG.md`'s
  2026-08-23 entry, `SESSION_LOG.md`'s 2026-08-23 entry.
- **Two real, self-caught issues during this pass** (see `CHANGELOG.md`
  for full detail on both): (1) a genuine concurrency bug in the
  session-cache logic — the check-then-create sequence wasn't atomic,
  so concurrent first-time callers on one source could each create a
  separate orphaned session (proven by a threaded test, fixed by
  widening the lock to cover the whole sequence); (2) a real,
  **unmocked API call to live OpenAI** during this pass's own test
  development — an early `PartialToolExecution` test forgot to pin
  `build_fallback_chain`, so the real router picked a different
  provider and sent one genuine request (a 400 validation error came
  back before any generation, so likely zero token cost, but the
  network call itself was real). Caught and fixed within the same pass
  by pinning the chain in every test that exercises a real provider
  path.
- **A more consequential finding — test isolation was broken for a
  reason unrelated to M4.2's own code**: the full suite silently wrote
  76 real rows into the actual production `history.db` on the first
  attempt. Root cause: `tests/__init__.py`'s package-level guard (the
  same pattern already relied on for `agent.usage.USAGE_FILE`) does
  **not** actually execute under this project's real
  `python -m unittest discover -s tests -v` invocation — proven with a
  stderr marker that never printed. `discover` with no `-t` flag
  imports test files as bare top-level modules, never triggering the
  package's `__init__.py`. **This is a pre-existing gap that predates
  M4.2** and was invisible until now only because every test file that
  touches `USAGE_FILE` already redundantly isolates it itself in its
  own `setUp`/`tearDown`. Fixed by extending that real, verified-working
  per-file pattern to `agent.history_store.HISTORY_DB` across the 8
  existing test files that exercise a real `execute_task_stream()` call
  (`test_claude_gateway.py`, `test_executor_multi_provider_fallback.py`,
  `test_executor_phase5_integration.py`,
  `test_agents_executor_integration.py`, `test_phase6_security.py`,
  `test_usage_limits_integration.py`, `test_voice_session.py`,
  `test_voice_skill_integration.py`), plus the new
  `tests/test_history_capture.py`. `tests/__init__.py`'s docstring
  corrected to document this honestly. **Worth independently verifying**
  in a future session whether any OTHER file-backed store in this
  project has silently relied on the same broken central guard.
- **What M4.2 deliberately does NOT do yet**: **no ToolSpec** —
  `tools/registry.py`/`tools/schemas/__init__.py` untouched, no
  `search_conversation_history`/`history_status` tool exists. **No
  backfill** of `conversation.json`. **No typed-memory change** —
  `agent/memory/*` untouched. **No proactive context injection.**
  `app.py`/`ui/menu_bar.py`/`agent/voice_session.py`/
  `agent/scheduler_daemon.py` themselves are **not modified** — capture
  lives entirely inside `execute_task_stream()`, which every one of them
  already calls, so there is no separate per-caller capture path to
  duplicate or miss.
- **Tests**: new `tests/test_history_capture.py`, 27 tests. Full suite:
  `python -m unittest discover -s tests -v` → **1363 passed, 0 failed**
  (1336 M4.1 baseline + 27 new). No paid provider calls in the final,
  corrected suite (see the OpenAI-call finding above — caught and fixed
  before this count). Confirmed the real production `history.db` was
  not created after the isolation fix landed. Latency: ~4.6ms per
  capture call, ~9.3ms per full turn pair — negligible next to real LLM
  call latency; M4.1's `synchronous=FULL` durability pragma was not
  weakened for speed.
- **Do not start M4.3** (Jarvis-facing search ToolSpecs) until a human
  has reviewed and committed M4.2. See "Exact recommended next steps"
  below.

## Phase 9 Reliability S1 — STRUCTURALLY SAFE TEST HARNESS ✅ COMPLETE, COMMITTED (`e46f5bd`), PUSHED, CI-VERIFIED

**This section previously said "BUILT, TESTED, UNCOMMITTED" — that went
stale.** S1 was committed as `e46f5bd` ("Harden test isolation and block
external network"), pushed, and CI-verified. **One real wrinkle worth
recording accurately**: the first CI run (GitHub Actions run
`32653067541`) failed on exactly one test,
`tests.test_history_store.TestConcurrency.test_concurrent_initialization_is_safe`,
with a real `sqlite3.OperationalError: database is locked`. A re-run of
the identical commit succeeded (1417/1417), which at the time was taken
as reasonably strong evidence of CI-environment flakiness rather than a
regression — correct as far as it went, but "a rerun passing" was
explicitly NOT accepted as sufficient closure, and a dedicated follow-up
(**Phase 9 Reliability S1.1**, see the section right after this one)
root-caused and fixed the actual defect: `PRAGMA journal_mode=WAL`'s
one-time transition does not reliably honor `busy_timeout` under
concurrent first-time initializers. The rest of this section is kept as
an accurate historical record of the S1 pass itself.

- **What it does**: fixes, at the root, the test-isolation gap M4.2
  discovered and worked around with per-file redirects only. The
  canonical full-suite command changed to
  `python -m unittest discover -s tests -t . -v` (package-style single
  module: `python -m unittest tests.test_name -v`) — the `-t .` flag is
  what makes `discover` actually import `tests` as a package and run
  `tests/__init__.py` before any test module. New `tests/_safety.py`,
  installed exactly once by a rewritten `tests/__init__.py`, provides:
  a disposable per-process temp "run root" (`tempfile.mkdtemp()`,
  resolved through `os.path.realpath()`); a redirect of every production
  persistent-store path constant into that run root (audit log,
  conversation store, history store, jarvis state, personal-context
  catalog, execution history, scheduler/browser locks, quiet-mode flag,
  scheduled tasks, TTS pid file, usage history, typed memory,
  credential-store config dir/logins file/Keychain service name,
  computer-use screenshot dir, browser profile dir, sandbox dir/Seatbelt
  profile path); an external-network firewall at the stdlib `socket`
  layer (loopback only — everything else raises
  `tests._safety.ExternalNetworkBlocked` before DNS/connection); a
  secondary `httpx` transport tripwire; and poisoned-by-default
  `tools.browser.sync_playwright`/`tools.computer_use.pyautogui`
  tripwires. Existing per-file `setUp`/`tearDown` redirects are
  untouched, real defense-in-depth, not made redundant. Full design:
  `ARCHITECTURE.md` §18.
- **Three real production bugs found and fixed** (not test code — actual
  application modules), all of the same class the audit predicted
  ("captured a production path at definition time instead of reading it
  dynamically"): (1) `tools/sandbox_python.py`'s Seatbelt profile string
  was a module-level f-string baking in `SANDBOX_DIR`'s import-time
  value — now built fresh inside `_ensure_profile()` on every call; (2)
  `agent/history_store.py`'s six public functions
  (`initialize_history_store`, `create_session`, `close_session`,
  `record_turn`, `history_status`, `search_history`) defaulted their
  `db_path` parameter directly to the `HISTORY_DB` module constant,
  bound at function-definition time; (3) `agent/personal_context.py`'s
  `save_catalog`/`load_catalog` did the same with `CATALOG_FILE`. All
  three now default to `None` and read the module constant inside the
  function body. Every existing caller already passed the path
  explicitly (production's own `agent/history_capture.py` was already
  written this way), so none of these were live bugs in production or
  the pre-S1 suite — they were latent traps for exactly the kind of
  default-relying meta-test this pass added, caught by writing that
  meta-test rather than assumed safe.
- **One real macOS-specific bug found and fixed in the harness itself**:
  `tempfile.mkdtemp()`'s default temp root (`/var/folders/...`) is
  itself a symlink to `/private/var/folders/...`; `sandbox-exec`'s
  Seatbelt profile resolves `subpath` rules against the canonical path,
  so a profile written with the symlinked form spuriously denied a
  legitimate in-sandbox write. Fixed by resolving the run root through
  `os.path.realpath()` once, upstream of every derived path.
- **Real Keychain**: confirmed empirically (again) that even a
  distinctly-named test Keychain service can raise a real
  `keyring.backends.macOS.api.Error` in this non-interactive session (no
  GUI to answer the access-control prompt) — `tests/test_safety.py`'s
  `TestConfirmLoginGate` now mocks `tools.credential_store.keyring`
  directly rather than relying on the service-name redirect alone. New
  `tools/keychain_smoke_test.py`: an opt-in, synthetic-credentials-only,
  own-service-namespace script for manually verifying the real Keychain
  seam end to end — never run automatically by the canonical suite or
  CI, and not run during this pass (the risk it would hit the same
  non-interactive Keychain-prompt error was already confirmed above, so
  running it again would add no new information).
- **Verification**: full suite `python -m unittest discover -s tests -t
  . -v` achieved a clean **1417 passed, 0 failed** run mid-pass. **This
  Mac's disk reached complete exhaustion during this pass** (0 bytes
  free — even trivial shell commands failed with `ENOSPC` at one point)
  after fluctuating around ~99% capacity/130-200Mi free earlier in the
  session. Later re-runs before recovery hit SQLite `disk I/O error`/
  `HistoryBusy` failures confined entirely to `tests.test_history_store`
  (22 failures on one re-run, 42 on a later one, worsening as free space
  kept shrinking; zero failures anywhere else in the other 1300+ tests
  every time) — confirmed environmental, not a defect in this pass's
  architecture: the same module flipped between clean and failing purely
  as available disk space changed, and every failure was a real
  `sqlite3.OperationalError`, never an assertion failure. **Resolved** in
  the finalization pass: freed disk space using only clearly disposable,
  reconstructible caches — `brew cleanup -s --prune=all`, `pip cache
  purge`, `uv cache clean`, `npm cache clean --force`, and clearing
  `~/Library/Caches/Google` (browser cache) — recovering free space from
  ~3.6Gi to **13Gi**, comfortably past the 10Gi target, with zero
  personal/user data or Jarvis Application Support data touched. Two
  subsequent full-suite runs both passed cleanly (1417/1417 each) with
  the disk holding steady at 13Gi, confirming the diagnosis. **This
  remains a real risk class for the live production app** — the real
  `history.db` does the same kind of SQLite writes and would hit the
  same error class under the same disk pressure again in the future,
  independent of anything in this pass. No automatic disk-space handling
  was added (deliberately, per instruction) — a disk-space health check/
  low-space alert is recorded as a future reliability idea in
  `ROADMAP.md`, not built now. Maintain reasonable free-space headroom
  operationally. Production-store metadata
  (existence/size/mtime, never content) snapshotted before and after
  across every run in this pass, including the flaky ones:
  **zero differences, every time** — no real file was touched,
  regardless of disk pressure (the redirect happens before any test
  runs, so a test erroring out afterward due to disk I/O never had a
  chance to reach a real path). Network firewall independently
  re-verified outside the test suite: a real loopback connect succeeds, a deliberate
  connect attempt to a real external IP raises
  `ExternalNetworkBlocked` in 0.0001s (proving it's a local, structural
  block, not a network timeout). New `tests/test_test_safety.py` — 49
  meta-tests covering canonical bootstrap, all redirected store
  constants against their real production values, the
  metadata-unchanged proof, the network firewall's
  loopback/external/IPv4/IPv6/UDP/hostname behavior, the `httpx`
  tripwire against a real loopback server, Keychain/Obsidian/skills
  isolation, browser/computer-use tripwires (including per-test-mock
  composability), and the real Seatbelt sandbox against the redirected
  run root. Security review checklist (public-internet unreachability,
  no production state written, Keychain/Obsidian/skills untouched,
  browser/computer actions fail locally, real sandbox boundary still
  exercised, no secrets in the diff, no new runtime dependency) — all
  passed; see this session's final report for the full itemized list.
- **Deliberately NOT done in this pass, on explicit instruction**: no
  commit, no push. `agent/memory/manager.py::search_scored()`'s
  `last_accessed` write-back-on-read behavior was (re-)confirmed but not
  changed — recorded as a follow-up in `ROADMAP.md`'s "Next" section.
  M4.3 was not started.
- **Files changed**: modified `tests/__init__.py`, `tests/test_safety.py`
  (`TestConfirmLoginGate` mocks `keyring`), `.github/workflows/tests.yml`
  (now runs the `-t .` command), `agent/history_store.py`,
  `agent/personal_context.py`, `tools/sandbox_python.py`. New:
  `tests/_safety.py`, `tests/test_test_safety.py`,
  `tools/keychain_smoke_test.py`. Documentation: this file,
  `ARCHITECTURE.md` §18 (rewritten) and §12b (test-isolation paragraph
  corrected), `ROADMAP.md`, `CLAUDE.md`'s "How to test" section,
  `CHANGELOG.md`, `SESSION_LOG.md`.
- **Reviewed and committed** as `e46f5bd`. Phase 9 Reliability S1.1
  (immediately below) is also reviewed, committed, and CI-verified —
  M4.3 (further below) has since started and is itself committed on a
  feature branch.

## Phase 9 Reliability S1.1 — HISTORY STORE CONCURRENT INITIALIZATION DETERMINISM ✅ COMPLETE, COMMITTED (`d38e794`), PUSHED, CI-VERIFIED ON FIRST ATTEMPT

- **What it does**: root-causes and fixes, deterministically, the exact
  flaky test S1's first CI run hit (see the S1 section above) — a real
  production concern, not just a test artifact, since multiple real
  Jarvis processes (menu-bar app, scheduler daemon, Streamlit) can
  legitimately race to be the first to initialize a not-yet-existing
  `history.db`.
- **Root cause**, found empirically, not assumed: isolated every PRAGMA
  statement in `_connect_writable()` individually under
  barrier-synchronized thread contention (reproduced with a standalone
  script before touching production code). `PRAGMA journal_mode=WAL`'s
  one-time transition — creating a brand-new database's `-wal`/`-shm`
  files the first time anything switches it out of SQLite's default
  rollback-journal mode — takes its own internal exclusive lock that
  does not reliably honor the connection's `busy_timeout` the way an
  ordinary statement does. Confirmed via `sqlite_errorcode == 5`
  (`SQLITE_BUSY`) on every reproduction (7 failures / 1800 attempts
  isolating each PRAGMA; 0 failures on `busy_timeout`, `foreign_keys`,
  `synchronous`, or `secure_delete` — only `journal_mode`). This is a
  documented, real SQLite behavior, not specific to this project's code.
- **Fix**: `agent/history_store.py`'s new `_set_journal_mode_wal()`
  wraps only this one PRAGMA in a bounded retry, narrowly matched to
  `sqlite_errorcode == SQLITE_BUSY` specifically — any other
  `OperationalError` (a real disk I/O failure, for instance) still
  propagates immediately, never retried. Bounded by the same window
  `_BUSY_TIMEOUT_MS` already promises callers (5000ms); exceeding it
  raises the same `HistoryBusy` a caller would see from a locked `BEGIN
  IMMEDIATE` elsewhere in this module. No PRAGMA/transaction reordering,
  no change to `busy_timeout`'s value, no durability/privacy setting
  weakened. Verified via a 2400-attempt barrier-synchronized stress
  reproduction using the real production code path: 0 failures with the
  fix (versus a real, reproducible failure rate without it). Measured
  overhead in the uncontended (normal) case: ~0.18ms mean — not
  material for a one-time, first-use-only operation.
- **New regression coverage**, all in `tests/test_history_store.py`:
  `TestConcurrency.test_concurrent_initialization_is_safe` rewritten to
  use `threading.Barrier` (never sleep-based) with 16 threads and full
  post-condition validation (schema v1, every table/index/trigger, WAL
  active, FTS5 secure-delete==1, core secure_delete==ON on a fresh
  connection, clean reopen) instead of just "no exception raised"; a new
  bounded repeated-round version (15 rounds × 12 threads, fast); a new
  real multi-process version (4 separate OS processes via
  `multiprocessing`, matching production's actual scenario, not just
  threads within one process — confirmed necessary since the race is a
  genuine SQLite/filesystem-level lock, not a Python GIL artifact). New
  `TestHistoryBusySemantics` class: `_set_journal_mode_wal` retry-then-
  succeed, retry-then-`HistoryBusy`-after-deadline, and
  never-retry-a-non-SQLITE_BUSY-error (all via a fake connection object,
  fast and deterministic, no real timing dependency); plus the first-
  ever end-to-end test of a genuinely held write lock (a real second
  connection holding `BEGIN IMMEDIATE` open) actually surfacing
  `HistoryBusy` rather than a raw error or the wrong exception class —
  this path existed since M4.1 but was previously checked only
  structurally (`HistoryBusy` is-a `HistoryStoreError`), never exercised
  against a real held lock.
- **Verification**: `tests.test_history_store` run 10 consecutive times,
  all clean (89/89 each, up from 83). Full canonical suite run three
  consecutive times, all clean (1423/1423 each, up from 1417 — 6 net new
  tests). Production-store metadata unchanged before/after (real
  `history.db` still does not exist — untouched either way). Full
  design: `ARCHITECTURE.md` §12a.
- **Files changed**: `agent/history_store.py` (the fix),
  `tests/test_history_store.py` (regression coverage), plus
  documentation (`ARCHITECTURE.md`, `CHANGELOG.md`, `SESSION_LOG.md`,
  `HANDOFF.md`, `ROADMAP.md`).
- **Committed** as `d38e794` ("Harden concurrent history initialization"),
  pushed to `origin/main`, **CI-verified on the first attempt** — GitHub
  Actions run `32659780845`, `run_attempt: 1`, 1423/1423 passed, no
  rerun needed. This is the direct proof the root-cause diagnosis was
  correct: the exact class of failure S1's own first CI attempt hit did
  not recur.

## Phase 9 / M4.3 — READ-ONLY CONVERSATION HISTORY TOOLS ✅ COMPLETE, MERGED TO `main`, CI-VERIFIED

- **Branch**: built on `phase9-m4.3-history-search`, cut from `main` at
  `d38e794`. **Merged to `main`** via a clean `--ff-only` merge
  (`d38e794..b19f042`, all 3 commits preserved), pushed, CI-verified
  again on the merged `main` (GitHub Actions run `32670629815`,
  `run_attempt: 1`, 1457/1457 passed). `main` HEAD is now downstream of
  this at `6fbc076` — see the M4.4 section below.
- **What it does**: two new Jarvis-facing ToolSpecs in
  `tools/schemas/history.py`, wrapping `agent/history_store.py` (M4.1)
  read-only — `history_status` (no input; availability, session/turn
  counts, schema version, date range) and `search_conversation_history`
  (full-text search; `query` required, `source`/`role`/`session_id`/
  `max_results` optional). Both `permission_level=0`, `parallel_safe=
  True`, `side_effect=False`, `unattended_allowed=True` (defaults),
  matching `tools/schemas/graphify.py`'s established precedent for a
  read-only tool group exactly.
- **Deliberately narrow**: no session/turn direct-retrieval tools —
  `history_store` has no `get_session`/`get_turn` read function, only
  create/close/record/status/search, so adding direct retrieval would
  have meant extending the store, turning a tool-wrapping milestone into
  a store-extension milestone. Revisit under M4.4 only if proactive
  retrieval actually needs it.
- **Tool name vs. store function name is a deliberate mismatch**: the
  ToolSpec is `search_conversation_history`, wrapping the store's
  `search_history()`. The store keeps its short internal name; the
  Jarvis-facing tool gets the disambiguated one so it can never collide
  conceptually with `agent/memory/manager.py`'s `search_scored()` —
  History vs. Memory is a stated architectural invariant.
- **Every one of `history_store`'s six exception classes maps to its own
  stable, machine-readable `state`** in the tool's JSON output — never a
  generic error string, never an uncaught traceback:
  `HistoryUnavailable→unavailable`, `HistorySchemaError→
  schema_incompatible`, `HistoryCorruption→corrupt`, `HistoryBusy→busy`,
  `HistoryValidationError→invalid_input`,
  `HistoryUnsupportedRuntime→unsupported_runtime`.
- **`max_results`**: default 10, hard cap 50, clamped silently
  server-side and declared in the input schema. A real, self-caught bug
  during review: the first pass computed `int(tool_input.get(
  "max_results") or 10)`, which let a non-numeric value (a model can
  emit `"max_results": "ten"` despite the schema) raise an uncaught
  `ValueError`/`TypeError` out of a permission-0 read-only tool, and let
  an explicit `0` silently become the default instead of clamping to 1.
  Fixed: explicit `is None` check, `int()` wrapped in `try/except
  (TypeError, ValueError)` mapped to the existing `invalid_input` state.
- **Snippet already bounded by the store**: `search_history()`'s SQLite
  FTS5 `snippet()` call already caps at 32 tokens (`_SNIPPET_TOKENS`);
  the tool passes it through unmodified, no double-truncation.
- **No `db_path` ever crosses the tool boundary** — `history_store.
  HISTORY_DB` is always passed explicitly by the tool module itself
  (matching `agent/history_capture.py`'s established pattern), never
  accepted as a tool input; the input schema has no `db_path`/`path`/
  `sql`/`match`/`raw_query` property, and a test proves an injected
  `db_path` key in the tool input is simply ignored.
- **Tests**: new `tests/test_history_tools.py`, 34 tests — registration/
  permission-flags, status against an empty store, search with complete
  provenance (turn_id/session_id/request_id/source/role/created_at/
  snippet/rank/redacted/truncated — all passed through from
  `SearchResult` unmodified), `max_results` clamping and every
  never-raises edge case (non-numeric string, `None`, float, bool,
  negative, `0`), all six error states individually, a hostile
  FTS-operator-shaped query proven neutralized (not passed through
  raw), a schema-enum-vs-store-validator drift tripwire, and real
  dispatch through `agent/executor.py`'s `_run_tool` (no separate
  dispatch path).
- **Commits**: `1519a51` ("Add read-only conversation history tools") —
  CI-verified on the first attempt, GitHub Actions run `32663268361`,
  `run_attempt: 1`, **1457/1457 passed**. `d78ba09` ("Correct S1.1 and
  Graphify status, document M4.3 tools") — docs only, no code change.
  `b19f042` ("Correct residual stale status claims, refresh code graph")
  — docs only, no code change; also the fast-forward merge tip landing
  all of the above on `main`.

## Phase 9 / M4.4 — PROACTIVE HISTORY RETRIEVAL ✅ COMPLETE, ON `main`, CI-VERIFIED, OFF BY DEFAULT

- **What it does**: `agent/history_context.py`'s
  `build_history_context(user_input, request_id, state)`, called from
  `agent.brain.build_system_prompt()` right after the memory patterns
  block, injects a bounded, relevance-gated block of past-conversation
  excerpts into the system prompt automatically — no ToolSpec, no model
  choice involved; the decision is pure code, matching M4.2's "the LLM
  never decides" framing for capture, now mirrored for retrieval.
- **Off by default, real evidence needed before flipping it on.**
  `config.settings.proactive_history_enabled` defaults to `False`. This
  is a deliberate product decision, not a placeholder — see "Exact
  recommended next steps" below for what turning it on for real would
  need.
- **The WAL finding** (do not re-litigate; see `ARCHITECTURE.md` §12d
  for the full account): the original justification for the new
  `search_history(busy_timeout_ms=...)` parameter was that a 5-second
  busy-wait could stall an ordinary chat turn if capture and retrieval
  ever contended for the database. Writing the tests for it forced an
  empirical check, and the premise was wrong — under this store's real
  WAL journal mode, a read never waits on another connection's open
  write transaction at all. The parameter is kept (three lines, changes
  nothing by default, real value for WAL recovery/platform differences)
  but the docstrings now say plainly that it is defense-in-depth, not a
  fix for a reproduced hazard.
- **The four settings** (`config/settings.py`, all `_env_*`-
  overridable): `proactive_history_enabled` (default `False`),
  `history_context_budget_tokens` (default `500` — a hard token
  ceiling, hits dropped whole once the next one would overflow it,
  never truncated/summarized), `history_context_timeout_ms` (default
  `150`, this one caller's `busy_timeout_ms` override only), and
  `history_context_max_results` (default `3`). **500 and 150 are
  unvalidated starting points** — chosen without production data,
  deliberately made settings rather than constants so they can be
  revised without a code change.
- **What M4.4 deliberately did not build** — recorded here specifically
  so a future session doesn't have to re-derive or re-litigate scope:
  no whole-session/ordered multi-turn retrieval (the store has no
  `get_session`/`get_turn` read function — a distinct, separately-gated
  milestone if ever needed); no embeddings/vector search (standing
  project principle); no automatic history-to-memory promotion (would
  blur the History-vs-Memory boundary this project treats as
  foundational); no summarization of dropped/truncated hits (FTS
  snippets are already small and complete; summarization would add a
  second paid LLM call gating every ordinary turn); no adaptive/dynamic
  budget (fixed ceiling for v1); no review UI for what got injected
  (the `history_retrieved` log events make this buildable later without
  a backend change); no injection for `source="scheduled"` (unattended
  runs get more conservative treatment elsewhere already, no clear
  benefit case yet).
- **Tests**: the disabled (default) path is proven **byte-identical**
  to a prompt built with the call stubbed out entirely
  (`tests/test_brain.py::TestDisabledPathIsByteIdenticalToNotWiredIn`)
  — clean by construction, not by extra effort, because
  `HistoryContext.prompt_text` already owns its full section text
  including the header. A history-store failure (all six
  `HistoryStoreError` subclasses) is proven unable to break a prompt
  build. New `tests/test_history_context.py` (20 tests),
  `tests/test_brain.py` (11 tests), 4 new `tests/test_history_store.py`
  tests for the timeout override.
- **Commits**: `c992432` ("Add bounded proactive history retrieval
  (inert)") — foundation, store parameter + `history_context.py` +
  settings, inert (not called from anywhere real yet). `6fbc076` ("Wire
  proactive history retrieval into the prompt builder") — the
  `brain.py` wiring + the two test files above. Both pushed directly to
  `main`, **CI-verified on the first attempt** (GitHub Actions run
  `32672234602`, `run_attempt: 1`, **1492/1492 passed**).

## OpenClaw M1 + M1.5 — READ-ONLY GATEWAY BRIDGE ✅ COMPLETE, COMMITTED, PUSHED, CI-VERIFIED

- **M1 commit**: `d1eb8130609d03e0f4f68a3f2cc46c4e3d66ade2`
- **M1.5 commit**: `8502c03b396d774e8e1f41f1ace7e87383ec429b` (pushed,
  CI-verified, GitHub Actions run `32073836073`, unittest step
  completed/success)
- **Current OpenClaw M1 capabilities**: an optional, disabled-by-default,
  read-only Gateway bridge — authenticated loopback WebSocket, stable
  compatibility target `openclaw@2026.7.1-2` (verified against an
  actual running instance of that exact version, not just its source),
  protocol version 4, a persistent Jarvis Ed25519 device identity, a
  human-only pairing flow, `operator.read`-only scope, a fixed RPC
  allowlist (`health`/`status`/`node.list`), and two Jarvis tools
  (`openclaw_status`/`openclaw_list_nodes`).

**Automated vs. real-world testing**: M1 has extensive mocked and
local-fake-Gateway protocol tests (real Ed25519 signature verification
against a genuine local WebSocket server, never a stub), **and** as of
M1.5, has also been verified against a real, running
`openclaw@2026.7.1-2` process (temporary, isolated, removed afterward —
no OpenClaw installation persists on this machine).

## OpenClaw M2 — OUTBOUND TEXT MESSAGING ✅ COMPLETE, HARDENED, COMMITTED, PUSHED, CI-VERIFIED

- **Status**: code- and test-complete, hardened per review, **committed
  as `d270dc461b15d8bd79e013032fea9ba05a674f87`** ("Add
  permission-gated OpenClaw outbound messaging"), pushed to
  `origin/main`, CI-verified (GitHub Actions run `32310485314`,
  success). 1176 tests passing at that commit.
- **What it adds**: `send_message_via_openclaw` (permission_level=3,
  side_effect=True, requires_live_confirmation=True — matching
  `send_email`'s convention). Input is exactly `channel`/`target`/
  `message`, all required — no `account_id`/`thread_id` in this first
  release (both are optional in the real `SendParamsSchema` but not yet
  independently allowlisted; narrowed out of the public surface in the
  hardening pass). Backed by `agent/openclaw_messaging.py` (new) and a
  profile-based extension to `agent/openclaw_gateway.py`.
- **Security boundaries** (in addition to M1's, all still true): the
  messaging identity is a SEPARATE Ed25519 device identity/token from
  M1's read identity (`OPENCLAW_MESSAGE_DEVICE_PRIVATE_KEY`/
  `OPENCLAW_MESSAGE_DEVICE_TOKEN`, never `OPENCLAW_DEVICE_PRIVATE_KEY`/
  `OPENCLAW_DEVICE_TOKEN`); requests only `operator.write`; its own RPC
  allowlist is exactly `{send}` (the read identity cannot reach `send`;
  the messaging identity cannot reach `health`/`status`/`node.list`
  either — independently exact, not a superset), and `_call()` now
  enforces this by identity (`is`, not `==`) against a fail-closed
  check for exactly `_READ_PROFILE`/`_MESSAGE_PROFILE`, rejecting any
  forged `_Profile`, even one copying valid scopes/methods.
  **Precise wording on read authority** (corrected in the hardening
  pass — see CHANGELOG.md's hardening entry for the full three-way
  distinction): the read identity is genuinely incapable of any write
  through Jarvis. The reverse claim — that a compromised messaging
  credential carries no read authority at all — is NOT accurate at the
  Gateway's own server-side scope-semantics level (`operator.write`
  already satisfies an `operator.read` check there); what actually
  keeps the messaging identity from reading anything is Jarvis's own
  RPC confinement (the `{send}`-only allowlist above), not the
  credential's cryptographic scope. `chat.send`/`message.action`/
  `node.invoke` remain structurally unreachable; no raw-RPC tool
  exists, and the transport function once named `send_raw()` is now
  private (`_send_raw()`), used only by `agent/openclaw_messaging.py`;
  messaging is globally disabled by default
  (`openclaw_messaging_enabled = False`) with empty channel/target
  allowlists, so a fresh install cannot send anywhere.
- **Delivery semantics (corrected in the hardening pass)**: at most ONE
  transmission per logical send. An `OpenClawUncertainDelivery` (frame
  transmitted, no trustworthy response) is reported as
  `delivery_status: "uncertain"` and never automatically retried — the
  original implementation retried once with the same idempotencyKey,
  reasoning the Gateway's in-memory dedupe cache made that safe; review
  found that reasoning doesn't hold across a Gateway process restart,
  so the retry was removed. A dedicated verifier
  (`agent/verification.py`'s `_verify_send_message_via_openclaw`,
  registered in `_VERIFIERS`) parses this tool's JSON result directly
  so `uncertain`/`failed` are never mistaken for `confirmed` by the
  generic failure-marker string check.
- **Still not done**: no real channel (Telegram/Discord/WhatsApp/Slack/
  Signal/iMessage/...) configured or logged into, no real outbound
  message ever sent — every automated test uses the same
  local-fake-Gateway-server pattern as M1/M1.5, never a real channel.
  Choosing/configuring the first real channel is a separate, not-yet-
  started future step (see ROADMAP.md's "Next" section).

## Graphify G0 — DEVELOPMENT CODEBASE GRAPH BASELINE ✅ COMPLETE, COMMITTED, PUSHED, CI-VERIFIED

- **Commit**: `7b4d0b6b2fecdd3264d8a5f48b3babcc1c5ee295` ("Document local
  Graphify development integration"), pushed, CI-verified (GitHub
  Actions run `32312885286`, success).

Not a Jarvis subsystem — an optional, local, development-time
structural code graph (`graphify` CLI, `graphifyy` on PyPI,
`Graphify-Labs/graphify` on GitHub, v0.9.47), installed isolated via
`uv tool install graphifyy`, entirely separate from CampusPilot's
`.venv`/`requirements.txt`. Full detail, rebuild commands, and verified
limitations: `docs/GRAPHIFY.md`.

- **What it is**: a queryable graph of this repo's own code structure
  (`graphify extract . --code-only` then `graphify cluster-only .
  --no-label` — both fully local, zero LLM/API calls, zero cost),
  giving a Claude Code session faster answers to "what calls/imports/
  depends on X" than repeated grep/Read round-trips.
- **G0 result**: 3024 nodes, 6407 edges, 163 communities, 96% EXTRACTED
  / 4% INFERRED edge confidence, zero import cycles. Validated clean —
  zero secrets, zero ignored/private paths, zero meaningful absolute-path
  leakage in the generated output.
- **Cross-checked against real source across 8 architectural areas**
  (orchestration, tool registry, autonomy, provider routing, coworker
  system, OpenClaw, voice pipeline, memory/Obsidian) — mostly accurate
  down to the exact source line, and independently corroborated three
  separate architectural claims already documented in `CLAUDE.md`.
- **Two verified limitations, must be respected going forward**: (1) a
  same-basename module-collision false positive
  (`tools/registry.py` vs. `agent/skills/registry.py`); (2) a
  system-wide miss of the `register(ToolSpec(..., handler=...))`
  registration-wiring pattern — this codebase's core tool-wiring
  mechanism, invisible for all 14 `tools/schemas/*.py` modules. Because
  of these, Graphify must never be trusted alone for ToolSpec-to-handler
  wiring or any permission/autonomy/credential-boundary question.
- **Decision**: generated `graphify-out/` is gitignored, kept **local**,
  rebuilt on demand (~12s, free) — not committed, since this repo is
  public and the graph would be a large, low-diff-signal blob mapping
  exactly where the security-relevant logic lives.
- **Not enabled (at G0)**: `graphify-mcp`, MCP registration, `graphify
  install`/`claude install`, `PreToolUse` hooks (soft or strict), `watch`
  mode, semantic/document extraction, any Jarvis runtime integration.
  `CLAUDE.md` was deliberately not modified.

## Graphify G1 — FOUR NARROW READ-ONLY JARVIS CODE-GRAPH TOOLS ✅ COMPLETE, COMMITTED, PUSHED, CI-VERIFIED

- **Status**: code- and test-complete, **committed as
  `c99e7928b34ee11de803a23fae2fc819d532b620`** ("Add read-only Jarvis
  code graph intelligence"), pushed, CI-verified (GitHub Actions run
  `32418542989`, success). 1253 tests passing at that commit.
- **What it adds**: `agent/code_graph.py` (new) — a read-only reader
  over `graphify-out/graph.json`, standard library only (`json`, `os`);
  never imports `graphifyy`, never invokes the `graphify`/`graphify-mcp`
  executables. The only subprocess call anywhere in the module is a
  fixed-argv, `shell=False` `git rev-parse HEAD` / `git status
  --porcelain --untracked-files=no` (5-second timeout, cwd pinned to the
  repo root), used only to decide graph freshness — never to run
  Graphify itself. Four ToolSpecs (`tools/schemas/graphify.py`,
  registered through the normal `tools/registry.py` path, no separate
  dispatch mechanism): `code_graph_status`, `search_code_graph`,
  `analyze_code_impact`, `find_code_path` — all `permission_level=0`,
  `side_effect=False`, `unattended_allowed=True`, `parallel_safe=True`,
  no live confirmation (same risk class as `get_system_status`). Total
  registered tools: 64 (up from 60).
- **Staleness enforcement**: `fresh` only when the graph parses AND
  `built_at_commit` equals current git HEAD AND the tracked working tree
  is clean (untracked files, including gitignored `graphify-out/`
  itself, never count as dirty — only tracked modifications do).
  `search_code_graph`/`analyze_code_impact`/`find_code_path` all refuse
  to run any traversal unless state is exactly `fresh`, returning a
  structured `{"ok": false, "state": ..., "reason": ...,
  "rebuild_required": ...}` shape instead. Nothing auto-rebuilds a stale
  graph — that stays a manual, deliberate step.
- **Never authoritative, by construction**: every result carries
  `authoritative: false` and G0's exact known-limitations list. A result
  touching `tools/registry.py`, `ToolSpec`, `tools/schemas/`,
  `agent/autonomy.py`, or permission/credential-keyword paths
  additionally carries `source_verification_required: true` —
  deterministic path-prefix/keyword matching, never a model judgment
  call. Impact/path results are worded as structural relationships only,
  never a guaranteed-runtime-effect claim.
- **Bounds enforced**: search ≤20 results (hard cap); impact ≤depth 3
  (hard cap), ≤100 results (hard cap); path ≤depth 10 (hard cap), one
  shortest path only. No caller-supplied filesystem path, CLI
  subcommand, or raw query surface in any of the four `input_schema`s;
  no fifth generic "run a graph command" tool.
- **Real-graph validation**: before commit, the actual local
  `graphify-out/graph.json` (built at commit `d270dc4`) correctly
  reported `stale` once checked against the then-current HEAD (`7b4d0b6`,
  then this implementation's own uncommitted changes) — proving the
  fail-closed staleness path against a real scenario, not only a mock.
  After commit, the graph was rebuilt against the new `c99e792` HEAD and
  every tool (`code_graph_status`/`search_code_graph`/
  `analyze_code_impact`/`find_code_path`) was re-exercised against real
  data. A same-day follow-up audit (**Graphify G1.1**, see below) then
  found and fixed a real incremental-extraction completeness gap in that
  rebuilt graph — see the "Graphify G1.1" section.
- **Not enabled (still, at G1)**: `graphify-mcp`, MCP registration,
  `graphify install`/`claude install`, `PreToolUse` hooks, `watch` mode,
  automatic graph regeneration, any permission/autonomy/routing decision
  based on graph content. `CLAUDE.md` deliberately not modified.
  `graphifyy` still not in `requirements.txt`/CampusPilot's `.venv`.

## Graphify G1.1 — INCREMENTAL-VS-FULL EXTRACTION AUDIT ✅ COMPLETE, DOCUMENTED

Documentation-only follow-up to G1 — no `agent/code_graph.py`,
ToolSpec, or test change. Full detail: `docs/GRAPHIFY.md`'s "Known
incremental-extraction limitation" section.

- **What was found**: Graphify 0.9.47's default incremental extraction
  (`graphify extract . --code-only` reusing its on-disk manifest/AST
  cache) can silently omit a direct per-symbol `imports` edge when a
  newly-added/changed Python file imports a named symbol from an old/
  unchanged (cached) module — even though `built_at_commit` matches
  HEAD, the tracked tree is clean, and `code_graph_status` reports
  `fresh`. Confirmed for real: `tools_schemas_graphify --imports-->
  tools_registry_toolspec`/`tools_registry_register` were missing from
  the live incremental graph, present in a clean full extraction at the
  identical commit. A second instance (`test_graphify_tools.py`
  importing `_run_tool` from unchanged `agent/executor.py`) confirmed
  the pattern generalizes.
- **How it was confirmed**: two isolated local clones
  (`git clone --local --no-hardlinks`, outside the repo) — one
  reproducing the exact old-commit-to-G1 incremental transition, one a
  clean extraction with no prior cache at the same commit. Four
  unrelated pre-existing `tools/schemas/*.py` modules checked as a
  control showed zero difference, ruling out a one-file fluke. Every
  node and every `contains`/`calls`/`inherits`/`method` relation for
  the new G1 files was 100% identical between modes — narrow, not
  broad. Likely mechanism identified by reading (not modifying) the
  installed Graphify source: incremental mode shares "unchanged corpus"
  context with call-resolution passes but not the plain
  `from X import Y` symbol-binding pass.
- **Action taken**: the live local graph was replaced with a clean full
  rebuild (old graph backed up outside the repo, validated, backup then
  deleted) — final: 3157 nodes, 6650 edges, 152 communities, built at
  `c99e792`.
- **Why no runtime change**: `agent/code_graph.py` already marks every
  result `authoritative: false` with its `limitations` list regardless
  of which extraction mode produced the graph — this finding is a
  rebuild-workflow note, not a gap in the tool's own trust model.
- **Documented**: a new `docs/GRAPHIFY.md` section, a terminology
  clarification that `fresh` never implies structural completeness or a
  clean-extraction provenance, and a 5-step precision rebuild workflow
  (clean tree → move old graph to an external temp backup → re-extract
  from empty → validate → only then delete the backup). Also notes
  `--force`'s documented scope doesn't confirm AST-cache bypass, so an
  empty `graphify-out/` remains the verified full-rebuild method.

## Current project status

**Phase 9**: Milestones 0-3 complete, committed, pushed, CI-verified.
Milestone 4 (reframed as "Phase 9 / M4 — Conversation & History
Intelligence", audited and split into M4.1-M4.4) is **now fully
complete**: M4A (audit), M4.1 (durable history store, `cd13e2a`), M4.2
(deterministic history capture, `c0d5fc5`), M4.3 (read-only
conversation-history ToolSpecs, `1519a51`, merged to `main` as `b19f042`),
and M4.4 (proactive history retrieval, `c992432` + `6fbc076`) are all
complete, committed, pushed, and CI-verified on `main`. **Phase 9
Reliability S1 (`e46f5bd`) and S1.1 (`d38e794`) are both complete,
committed on `main`, pushed, and CI-verified — S1.1 CI-green on the
first attempt.** M4.4 is intentionally **off by default**
(`proactive_history_enabled=False`) — see its dedicated section above
for what turning it on for real would need. See the dedicated sections
near the top of this file for full detail on each milestone. OpenClaw
M1/M1.5/M2 and Graphify G0/G1/G1.1 landed between Milestone 3 and M4,
both complete/committed on `main`.

**OpenClaw** (a separate, real, independently-developed open-source
project — github.com/openclaw/openclaw, docs.openclaw.ai — not a Jarvis
subsystem): **M0** complete, approved, no code. **M1**, **M1.5**, and
**M2** (including its hardening pass) all complete, committed, pushed,
CI-verified — see the section above.

**Graphify**: **G0** (evaluation), **G1** (four narrow read-only Jarvis
tools), and **G1.1** (incremental-vs-full extraction audit) all
complete — see the sections above and `docs/GRAPHIFY.md`. Still not a
Jarvis runtime dependency — `graphifyy` itself is never imported or
invoked; only its already-generated graph.json is read. Refreshed a
second time, against M4.3's tracked-file changes (`tools/schemas/
history.py`, `tools/schemas/__init__.py`) — **3585 nodes, 7523 edges,
170 communities**, `built_at_commit = d78ba09` at refresh time (this
pass's own docs-only commit had not yet landed, so this is one commit
behind current HEAD by the time you read this; per this project's own
stated policy, that's expected — refresh only when actually useful
before code-graph analysis, not reflexively after every commit). Prior
counts, superseded in order: S1-era `3514/7421/172` against `e46f5bd`;
S1.1-era `3532/7452/163` against `d38e794`.

**Working tree**: `main` HEAD is `6fbc076` — all of S1.1/M4.3/M4.4 and
this documentation pass are on `main` now, no feature branch work
outstanding. Confirm with a live `git status`/`git log` rather than
trusting this file. **1492 tests pass, 0 failures** under the canonical
`python -m unittest discover -s tests -t . -v` on `main` (1457 after
M4.3's merge, +24 from M4.4's foundation, +11 from M4.4's wiring),
reproduced multiple times, no live/paid API calls during testing except
the one explicitly authorized real chat turn used for M4.3's live E2E
proof (see `.relay/report-2.md`, $0.001656 total), zero production files
touched by the test suite itself. No real OpenClaw installation persists
on this machine.

## What we are currently building

Phase 9 / M4 (M4.1 through M4.4) is fully complete and committed on
`main` — nothing from it is in progress. Everything else is also
complete and committed: OpenClaw M1/M1.5/M2, Graphify G0/G1/G1.1. The
next real work (see `.relay/BACKLOG.md`) is populating the Obsidian
vault (`JarvisVault/`) with real interlinked notes generated from this
project's own docs and conversation history, then evaluating a
third-party AGPL vault-visualization UI *outside* this repo — read its
source before running it, since it ships a local Express server. **Do
not start Graphify G2 (MCP/hooks/auto-rebuild — none implemented or
assumed), a real OpenClaw messaging channel, OpenClaw device
capabilities, or OpenClaw agent/model-routing integration** until the
user explicitly says so. **Do not flip `proactive_history_enabled` to
`True` by default** without real usage evidence — see the M4.4 section
above.

## What was completed (this session, most recent first)

-4. **Graphify G1.1 — incremental-vs-full extraction audit**
    (documentation-only follow-up): see the "Graphify G1.1" section
    above and `docs/GRAPHIFY.md`'s "Known incremental-extraction
    limitation" section for full detail. Summary: confirmed, via a
    controlled two-clone experiment, that Graphify 0.9.47's incremental
    extraction can miss direct per-symbol `imports` edges from a new
    file to an old/unchanged module; replaced the live local graph with
    a clean full rebuild; documented the finding and a precision
    rebuild workflow. No runtime code change — `agent/code_graph.py`'s
    existing `authoritative: false` design already covers it.
-3. **Graphify G1 — four narrow, read-only Jarvis code-graph tools**
    (prior session): see the "Graphify G1" section above and
    `docs/GRAPHIFY.md` for full detail. Summary: `agent/code_graph.py`
    (new, standard-library-only reader, one fixed-argv `git` subprocess
    call for freshness only, never invokes `graphify`/`graphify-mcp`);
    four ToolSpecs (`tools/schemas/graphify.py`) registered through the
    normal `tools/registry.py` path; strict fail-closed staleness
    (fresh only at matching HEAD + clean tracked tree); every result
    marked `authoritative: false` with `source_verification_required:
    true` on security-relevant results; bounded search/impact/path with
    hard caps; validated against both synthetic fixtures and the real
    local graph. Committed as `c99e792`, pushed, CI-verified.
-2. **Graphify G0 — development codebase graph baseline** (new session):
    evaluated and approved Graphify as optional, local, development-time
    tooling — see the "Graphify G0" section above and `docs/GRAPHIFY.md`
    for full detail. Committed: `.gitignore` entry, `docs/GRAPHIFY.md`,
    and these project records. `graphify-out/` itself stays local,
    gitignored, never committed.
-1. **OpenClaw M2 hardening/review pass** (prior session): a review of
    the M2 diff found several issues, all fixed — see CHANGELOG.md's
    2026-08-19 entry for full detail. Summary: removed the automatic
    same-key retry on uncertain delivery (unsafe across a Gateway
    restart); added a dedicated JSON-parsing verifier for
    `send_message_via_openclaw` in `agent/verification.py`; enforced the
    closed `_Profile` set by Python identity (`is`) in `_call()`, not
    `==`; renamed `send_raw()` to private `_send_raw()`; corrected
    documentation that overstated a compromised messaging credential as
    having no read authority at all (the Gateway's own server-side scope
    semantics are asymmetric); narrowed `account_id`/`thread_id` out of
    the public tool surface. Committed as `d270dc4`, pushed, CI-verified.
0. **OpenClaw M2 — outbound text messaging bridge** (new session,
   implementation + tests only, uncommitted): the real Gateway `send`
   RPC, never `chat.send` (`ChatSendParamsSchema` requires a
   `sessionKey` and is part of OpenClaw's own agent/session execution
   surface — confirmed via real `openclaw@2026.7.1-2` server source,
   not just followed on instruction) or `message.action` (a broader CLI
   action-dispatch RPC). Added a small, closed `_Profile` type to
   `agent/openclaw_gateway.py` — exactly two instances
   (`_READ_PROFILE`, unchanged from M1; new `_MESSAGE_PROFILE`, its own
   separate Ed25519 device identity/token
   `OPENCLAW_MESSAGE_DEVICE_PRIVATE_KEY`/`OPENCLAW_MESSAGE_DEVICE_TOKEN`,
   `operator.write` only — confirmed against real source that this
   already satisfies `operator.read`, so never both — `{send}`-only RPC
   allowlist). New `agent/openclaw_messaging.py`: Jarvis-side channel/
   target allowlists (disabled and empty by default — no wildcards, no
   OpenClaw-side name resolution), message validation (4000-char cap,
   rejects rather than truncates), a fresh internally-generated
   `idempotencyKey` per send. **Corrected in a same-day hardening pass**
   (see the item above and CHANGELOG.md): this originally included one
   bounded same-key retry on a genuinely uncertain delivery, reasoned
   safe against the real Gateway's in-memory, 5-minute-TTL idempotency
   cache — review found that reasoning doesn't survive a Gateway process
   restart, so the retry was removed; a genuinely uncertain delivery is
   now reported as such and left there, never auto-retried. New tool
   `send_message_via_openclaw` (permission_level=3, side_effect=True,
   requires_live_confirmation=True, matching `send_email`'s
   convention; `account_id`/`thread_id` also later narrowed out of its
   input in the hardening pass). New tests in
   `tests/test_openclaw_messaging.py` (see the hardening item above and
   this session's final report for exact current counts). **Explicitly
   not done**: no real channel configured/logged into, no real message
   sent, nothing committed or pushed.
1. **OpenClaw M1.5 — real loopback Gateway smoke test** (prior session
   within the same overall OpenClaw initiative, committed as `8502c03`,
   pushed, CI-verified — GitHub Actions run `32073836073`): ran an
   actual `openclaw@2026.7.1-2` process for the first
   time — isolated npm install under `/tmp`, isolated
   `OPENCLAW_STATE_DIR`, loopback-only bind, test token stored via
   `agent/secrets.py`. First attempt (`--dev`) exposed a real isolation
   gap (dev workspace escaped the state-dir override, wrote under the
   real `~/.openclaw`; the `bonjour` plugin broadcast the Gateway on the
   LAN) — caught within ~8 seconds, killed before any Jarvis call,
   cleaned up with explicit user approval. Corrected approach (no
   `--dev`, explicit workspace patch, `plugins.enabled = false`)
   produced a clean isolated Gateway. The real, load-bearing test —
   Jarvis's actual `openclaw_status`/`openclaw_list_nodes` tools via
   `tools.registry.dispatch()` — found and fixed two real bugs
   (`client.platform` required by the real schema but never sent;
   `client.deviceFamily` signed into the payload but never sent on the
   wire, breaking real signature verification). With both fixed: full
   success, `operator.read` only (independently confirmed via
   `openclaw devices list`), empty node list as expected. Cleaned up
   fully afterward: Gateway killed, port freed, ~363MB temp install
   removed, smoke-test-only Keychain secrets (`OPENCLAW_GATEWAY_TOKEN`,
   `OPENCLAW_DEVICE_TOKEN`) deleted, `OPENCLAW_DEVICE_PRIVATE_KEY`
   preserved. 2 new tests (1098 total, up from 1096); the fake test
   server's signature verification was also corrected to reconstruct
   from actual captured wire values instead of duplicate constants.
2. **OpenClaw M1 re-verification #2 — stable compatibility: auth-field
   bug fixed for real, device-ID CONFIRMED** (same session, follow-up to
   item 3 below): re-verification #1 (also this session, folded below)
   had checked a claimed `signedAt` bug against the beta client packages
   and correctly left `signedAt` unchanged, but its OWN fix — sending
   Jarvis's shared `OPENCLAW_GATEWAY_TOKEN` under `auth.bootstrapToken`
   — was itself wrong, based on client-side field *existence* rather
   than the Gateway server's actual field *semantics*. This pass
   re-verified against the actual CURRENT STABLE `openclaw` npm app
   package (`openclaw@2026.7.1-2`, `dist-tags.latest`) rather than the
   separately-published client/protocol packages, which turn out to have
   **no stable npm release at all** — only an intentionally-empty `0.0.0`
   placeholder and prerelease `-beta.N` versions (their own CHANGELOG.md
   says "Publish the reference Gateway WebSocket client for the first
   time" — they were extracted from the main app and published only
   very recently). Downloaded and inspected the stable app's own
   87MB-unpacked bundle directly (`npm pack openclaw@2026.7.1-2`),
   including its **server-side** connect-auth resolution
   (`resolveSharedConnectAuth`, `resolveDeviceTokenCandidate`,
   `resolveConnectAuthDecisionCore`) — the actually-authoritative source
   for wire-field meaning, since a schema only proves a field can exist,
   not what it does. Confirmed: `auth.token` (+ `auth.password`) is
   checked against the Gateway's own configured SHARED secret — this IS
   what `OPENCLAW_GATEWAY_TOKEN` conceptually is. `auth.bootstrapToken`
   is checked via a wholly separate path (`verifyBootstrapToken(deviceId,
   publicKey, token, ...)`) meant for a genuinely distinct device-
   pairing/setup credential Jarvis does not hold — re-verification #1's
   fix incorrectly used this field, now corrected. `auth.deviceToken` is
   checked via a third, separate path (`verifyDeviceToken`), and MUST be
   used (not `auth.token`) for a stored device credential specifically
   because a rejection there reports `AUTH_DEVICE_TOKEN_MISMATCH`
   (`candidateSource === "explicit-device-token"`) — reusing `auth.token`
   would instead surface as `AUTH_TOKEN_MISMATCH`, silently breaking the
   stale-token clear-and-retry logic. Fixed: shared credential → always
   `auth.token`; stored device credential → always `auth.deviceToken`;
   `auth.bootstrapToken` → never populated. Also confirmed, while in the
   stable bundle: the `signedAt` conclusion from re-verification #1 is
   safe regardless of the stable/beta client difference (stable's own
   client uses plain `Date.now()` unconditionally, no `challengeTs`
   concept at all — a real version difference — but the Gateway
   SERVER's actual freshness check is `Math.abs(Date.now() - signedAt) >
   DEVICE_SIGNATURE_SKEW_MS` (120s), a wall-clock skew check against the
   SERVER's own clock, never an exact-match against the challenge's own
   `ts` — so either client behavior is compatible with either server
   version). And: the device-ID derivation, previously flagged as a
   documented low-risk assumption, is now **CONFIRMED** — the stable
   bundle contains a literal `deriveDeviceIdFromPublicKey` function
   (`src/infra/device-identity.ts`) doing exactly `SHA-256(raw 32-byte
   Ed25519 public key).hexdigest()`, and the Gateway server independently
   re-derives and compares this value against the client-claimed
   `device.id` on every connect — an exact match to this bridge's own
   implementation. The "unverified assumption" language has been removed
   from the module docstring, `_load_or_create_device_identity`, and
   this file; `DEVICE_AUTH_DEVICE_ID_MISMATCH` handling is kept as
   defense-in-depth, not because of remaining doubt. 3 net new tests (57
   total in `tests/test_openclaw_gateway.py`, up from 54): correct wire
   field for the shared token, correct wire field for a stored device
   token, a payload/wire-value consistency check (STEP 4: never sign one
   credential while sending another), and a known-answer test for the
   device-ID algorithm (fixed test keypair → fixed expected 64-char hex).
3. **OpenClaw M1 re-verification #1 — signedAt checked, superseded auth
   fix** (same session, folded into item 2 above for the corrected final
   state): a claim surfaced that `device.signedAt` must always be the
   client's current wall-clock time and must never be copied from the
   `connect.challenge` event's own `ts`. Checked directly against a
   freshly re-pulled, newer beta npm release (`2026.8.1-beta.2`) — both
   the CLI/backend `GatewayClient.buildConnectPlan` and the browser
   `GatewayBrowserDeviceAuthLifecycle.buildPlan` real implementations do
   `signedAtMs = challengeTs ?? Date.now()`, the opposite of the claim,
   and exactly what `agent/openclaw_gateway.py` already implemented — so
   no change was made to `signedAt` handling; two regression tests were
   added (`test_signed_at_uses_the_connect_challenge_timestamp_not_wall_clock`,
   `test_signed_at_falls_back_to_wall_clock_when_challenge_omits_timestamp`).
   This same pass's OWN auth-field fix (sending credentials under
   `auth.bootstrapToken`/`auth.deviceToken` based on client-side schema
   field existence alone) was itself incorrect, as item 2 above found
   and corrected.
4. **OpenClaw M1 correction — real Ed25519 device-identity auth**
   (replaces the shared-token-only design from the pass below): the
   original M1 explicitly flagged its auth as an unverified assumption.
   Verified against the actual published `@openclaw/gateway-client` and
   `@openclaw/gateway-protocol` npm packages (downloaded via `npm pack`,
   inspected as real compiled source — docs.openclaw.ai doesn't cover
   this flow, and a GitHub issue claiming to describe it (#17571) had
   its own payload-format claim proven stale — "v1" vs. the real,
   current "v3" — caught only by checking the real package, a genuine
   reason not to trust a single secondary source uncritically). Rebuilt
   `agent/openclaw_gateway.py`: a persistent Ed25519 device identity
   (`OPENCLAW_DEVICE_PRIVATE_KEY`, PEM/PKCS8, via `agent/secrets.py`,
   generated once and reused); the real, verified V3 device-auth payload
   format and Ed25519 signing; a `connect.challenge`-first handshake
   (the original version incorrectly sent `connect` immediately); post-
   `hello-ok` verification that `operator.read` was actually granted,
   **failing closed** otherwise (new `OpenClawScopeError`); a new
   `OpenClawPairingRequired` error for a new/unrecognized device
   identity — **never auto-approved**, a human must run
   `openclaw devices approve <requestId>` themselves; device-token
   persistence/reuse (`OPENCLAW_DEVICE_TOKEN`) with exactly one bounded
   fallback-to-bootstrap-token retry on `AUTH_DEVICE_TOKEN_MISMATCH`,
   mirroring the real client's own verified behavior — never looping
   further. `client.id`/`client.mode` use `"cli"` (the real, closed
   enum's closest legitimate, non-reserved identity — confirmed via the
   actual `client-info.mjs` source; `"backend"`/`"gateway-client"` are
   OpenClaw's own reserved internal identity and are never used, per
   explicit instruction). New dependency: `cryptography==50.0.0` —
   verified to build and work on Intel macOS + Python 3.14 (no
   pre-built wheel existed for this exact combination yet; compiles
   from source cleanly). **At this point in the session, one residual,
   explicitly-flagged assumption remained** (since CONFIRMED — see item 1
   at the top of this list): the exact device-ID hash algorithm (SHA-256
   of the raw Ed25519 public key) could not yet be confirmed against any
   primary source — genuinely not part of either published beta package,
   which both expose key generation/signing only as an injected
   dependency (stubbed as a no-op in the default export); the real
   implementation turned out to live inside the main `openclaw`
   application's own bundle, not the separately-published packages
   inspected at this stage. Deliberately low-risk if wrong: the real
   Gateway has a dedicated error code for exactly that case
   (`DEVICE_AUTH_DEVICE_ID_MISMATCH`), handled as a clean
   `OpenClawAuthError`, never a crash. The fake Gateway test server was
   rewritten to perform genuine Ed25519 signature verification against
   the real client's actual output (reconstructing the exact payload
   with the module's own real builder function, then
   `Ed25519PublicKey.verify()`), not a "signature is non-empty" stub
   check. 15 new tests (51 total in `tests/test_openclaw_gateway.py`,
   up from 36). The RPC allowlist, `operator.read`-only scope ceiling,
   and the two tools (`openclaw_status`/`openclaw_list_nodes`) are
   unchanged from the original M1 pass.
5. **OpenClaw M1 v1 — read-only Gateway bridge, shared-token auth**
   (same session, superseded by items 2-4 above, kept here for full
   session history): `agent/openclaw_gateway.py` (new), a fixed RPC
   allowlist (`health`/`status`/`node.list` only), normalized errors,
   two tools (`openclaw_status`/`openclaw_list_nodes`,
   `tools/schemas/openclaw.py`, both permission_level 0, read-only).
   `websockets==16.1.1` added (was already an incidental transitive
   dependency of `streamlit`, now pinned directly). 51 tests at this
   stage (36 + 15 across the two new test files).
6. **OpenClaw M0 — research/architecture audit** (no code changes) —
   see `CHANGELOG.md`/`ROADMAP.md` for the full finding list (Intel
   macOS support, loopback-WebSocket local default, protocol version 4,
   the 7-scope operator model, unsandboxed plugin execution).

## What is partially completed

**Phase 9 / M4.3 (read-only conversation-history ToolSpecs)** is
complete and fully tested on its feature branch; the only thing not done
is the merge/PR decision — see the dedicated section above before
assuming anything about its state. (S1, S1.1, M4.1, and M4.2 are no
longer partial — all four are committed on `main`.)

OpenClaw M2 is complete and fully tested; the only thing not done is
the user's review/commit decision, plus choosing and configuring a
first real messaging channel afterward.

## Current bugs / known issues

- **`agent/memory/manager.py::search_scored()` silently persists a
  `last_accessed` timestamp on every read that returns ≥1 result** — not
  a bug in the sense of incorrect behavior, but an undocumented
  side-effect on a conceptually read-only path, called unconditionally
  on every real request via `agent.brain.build_system_prompt()`.
  Confirmed during the Phase 9 reliability audit and reconfirmed during
  the S1 pass; deliberately not changed in either (see S1's section
  above and `ROADMAP.md`'s "Next" section for the open design question).
  No longer a test-isolation risk — S1's store redirection means
  canonical tests never touch the real `memory.json` because of it — but
  still an open production design decision.

Otherwise none remaining. The two real bugs M1.5's smoke test found
(`client.platform`, `client.deviceFamily` both missing from the wire
`connect` params) are fixed and verified against a real Gateway — see
item 1 in "What was completed" above. The device-ID hash algorithm
(flagged in a prior session) remains CONFIRMED against real primary
source.

## Current blockers

None technical. OpenClaw M1/M1.5/M2, Graphify G0/G1/G1.1, and all of
Phase 9 / M4 (M4.1 through M4.4) plus S1/S1.1 are committed, pushed, and
CI-verified on `main`. Open decisions (none urgent): which real
messaging channel (if any) to configure for OpenClaw next, whether/when
to pursue a further Graphify milestone (MCP/hooks/auto-rebuild — none
implemented or assumed so far), the `last_accessed` design question
above, and whether/when to turn `proactive_history_enabled` on by
default once M4.4 has seen real usage.

## Recent architectural decisions

- **A separate device identity per scope tier, not a scope upgrade of
  an existing one.** OpenClaw M2 needs `operator.write` for `send`; M1's
  device identity holds only `operator.read`. Rather than request a
  broader scope on the same credential, M2 holds its own Ed25519
  keypair/device token entirely — a compromised read credential can
  never send a message through Jarvis. The reverse claim needs more
  care: a compromised messaging credential cannot escalate *through
  Jarvis* (its RPC allowlist is exactly `{send}`), but the real
  Gateway's own server-side scope semantics are asymmetric
  (`operator.write` already satisfies an `operator.read` check there),
  so it is not accurate to claim the credential itself is
  cryptographically incapable of read authority — see the hardening-
  pass entry in CHANGELOG.md for the full three-way distinction.
  Implemented as a small, closed `_Profile` type
  (`agent/openclaw_gateway.py`) with exactly two fixed instances, now
  enforced by identity (`is`, not `==`) in `_call()` so a forged
  `_Profile` with copied field values is still rejected; there is no
  public API to construct a third or request an arbitrary scope list.
- **`send`, never `chat.send` — verified against real source, not just
  followed on instruction.** The Gateway's real `chat.send` RPC requires
  a `sessionKey` and is part of OpenClaw's own agent/session execution
  surface; using it for Jarvis's outbound messages would mean an
  OpenClaw agent loop processes them, exactly the architectural blurring
  this project avoids. Confirmed directly against
  `openclaw@2026.7.1-2`'s compiled server source that `send` is a
  genuinely separate, simpler RPC method with no session/agent concept
  at all.
- **A retry that looked verified-safe, then wasn't, on closer review.**
  Before implementing any automatic retry for a side-effecting `send`
  call, the real Gateway's idempotency-cache behavior was read directly
  from its compiled server source (a real, in-memory, 5-minute-TTL,
  `idempotencyKey`-keyed cache that replays cached results on a repeat
  key) — a one-time same-key retry was then implemented on that basis.
  A subsequent hardening/review pass identified the gap: that cache is
  in-memory and single-process, so it does not survive a Gateway
  process restart. If the Gateway delivers the message and then dies/
  restarts before Jarvis receives the response, a same-key resend is no
  longer provably deduplicated and could send a real duplicate. The
  automatic retry was removed entirely as a result — for an external,
  user-visible side effect, correctness beats speculative automatic
  recovery of an ambiguous outcome. Lesson: verifying a mechanism is
  real is not the same as verifying it's durable enough for the
  specific safety property being relied on — both need checking.
- **Source-reading and a careful local fake server are not a substitute
  for testing against the real thing at least once.** M1.5's real
  loopback Gateway smoke test found two real bugs (`client.platform`,
  `client.deviceFamily` missing from the wire) that survived every prior
  source-reading pass and the entire local fake-server test suite,
  because the fake server's own signature verification reconstructed
  payloads from expected constants rather than the actual captured wire
  values. Fixed both the bugs and the fake server's fidelity gap that
  let them through.
- **A temporary test harness's own isolation claims still need
  verification, not just trust.** OpenClaw's documented
  `OPENCLAW_STATE_DIR` override, combined with `--dev`, did not fully
  isolate a real Gateway process from the user's real `~/.openclaw` —
  the dev workspace path escaped it, and the default plugin set
  (including `bonjour`) broadcast the test Gateway on the LAN. Caught
  within seconds by checking the Gateway's own log output rather than
  assuming the isolation worked; not a Jarvis security issue (the
  WebSocket bind itself was loopback-only throughout), but a reminder to
  verify a test environment's actual behavior, not just its documented
  behavior. Future temporary OpenClaw test harnesses must: never use
  `--dev`; explicitly patch the workspace path in addition to setting
  `OPENCLAW_STATE_DIR`; set `plugins.enabled = false`; verify the
  listener's real bind address before connecting; verify no `~/.openclaw`
  writes occurred; skip normal onboarding entirely; and delete temporary
  Gateway/device-token secrets afterward (a stored device token tied to
  deleted Gateway state is worse than no token — it looks configured but
  isn't valid).
- **A user-supplied "bug report" was checked against primary source
  before being applied, and the code was NOT changed when the source
  contradicted it** — a claim that `device.signedAt` must never come
  from `connect.challenge`'s own `ts` was checked against a freshly
  re-pulled, newer beta npm release and found to be the opposite of
  real, current client behavior (real clients prefer the challenge
  timestamp). No code change was made; two regression tests were added
  instead to guard the current, verified-correct behavior. This
  project's standing rule to verify security-critical protocol details
  against primary source, not memory or a single claim, applies
  symmetrically — to a user's own claim, not only to secondary sources
  like GitHub issues.
- **A schema proving a field CAN exist is not proof of what it MEANS —
  the server's own field-interpretation logic is authoritative for wire
  compatibility, not client-side schema/field existence.** The first
  auth-field fix this session (based on the beta client packages'
  `ConnectParams.auth` schema having distinct `token`/`bootstrapToken`/
  `deviceToken` fields) sent Jarvis's shared `OPENCLAW_GATEWAY_TOKEN`
  under `auth.bootstrapToken` — which compiled, matched the schema, and
  was still wrong: the real Gateway SERVER's own connect-auth resolution
  (read directly from `openclaw@2026.7.1-2`'s compiled server source)
  shows `auth.bootstrapToken` is verified via a wholly separate
  `verifyBootstrapToken(deviceId, publicKey, token, ...)` path meant for
  a genuinely distinct device-pairing/setup credential — not what a
  plain shared Gateway secret is. Corrected: shared token → `auth.token`
  (checked via `resolveSharedConnectAuth`); stored device token →
  `auth.deviceToken` (checked via `verifyDeviceToken`, and required —
  not merely preferred — because only that field's rejection reports
  `AUTH_DEVICE_TOKEN_MISMATCH`, which this bridge's stale-token
  clear-and-retry logic depends on); `auth.bootstrapToken` never used.
- **The separately-published `@openclaw/gateway-client`/`@openclaw/
  gateway-protocol` npm packages have no stable release at all** — only
  an empty `0.0.0` placeholder and beta prereleases (confirmed via
  `npm view <pkg> versions`; their own CHANGELOG.md says they were
  "published for the first time" very recently, extracted out of the
  main app). For a genuine stable-compatibility check, the real source
  of truth is the main `openclaw` app's own stable npm release
  (`openclaw@2026.7.1-2`, `dist-tags.latest`), which vendors its own
  (slightly older) copy of this same logic directly in its ~87MB
  bundle — downloaded via plain `npm pack` and inspected directly, same
  as the beta packages were.
- **Device-ID derivation is now CONFIRMED, not assumed** — the stable
  app bundle contains a literal `deriveDeviceIdFromPublicKey` function
  doing exactly `SHA-256(raw 32-byte Ed25519 public key).hexdigest()`,
  and the Gateway server independently re-derives and compares this
  against the client-claimed `device.id` on every connect. This matches
  this bridge's implementation exactly. The "unverified assumption"
  framing has been removed from the module docstring and this file;
  `DEVICE_AUTH_DEVICE_ID_MISMATCH` handling is kept as defense-in-depth
  regardless, not because of remaining doubt.
- **Device-identity auth was verified against real published packages,
  not guessed from docs or a community issue** — the single most
  important process decision this session: when docs.openclaw.ai didn't
  cover third-party device auth and a GitHub issue claiming to describe
  it had an already-provably-stale detail (payload format version), the
  response was to download and directly inspect the actual npm packages
  rather than proceed on an unverified secondary source for a security-
  critical protocol detail. This same discipline — inspect the actual
  package/bundle rather than trust a schema, a claim, or a secondary
  source — is what caught both auth-field bugs and confirmed the
  device-ID algorithm this session.
- **`"cli"` is Jarvis's OpenClaw client identity, not `"jarvis"`** — the
  original M1 pass used a free-text `"jarvis"` client.id, which the real
  Gateway would have rejected outright (client.id/client.mode are
  validated against a closed enum, confirmed via real source). Jarvis's
  actual identification goes in the free-text `deviceFamily` field
  instead.
- **Fail closed on scope, never on authentication success alone** — a
  connection that authenticates but isn't granted `operator.read` is
  treated as a failure (`OpenClawScopeError`), not a degraded success.
- **Pairing is a human operation, never automated** — `PAIRING_REQUIRED`
  normalizes to a clean, safe result; Jarvis has no pairing-approval
  tool and never will in M1's scope.
- (Carried over from the M1 v1 pass, still true) OpenClaw is optional
  subordinate infrastructure, never a second orchestrator; a fixed RPC
  allowlist, not a blocklist; synchronous WebSocket client to fit
  Jarvis's existing tool architecture; one-shot connections, no
  persistent connection manager; zero third-party OpenClaw plugin
  dependency; OpenClaw M2 (messaging, `operator.write`) is next, not
  started.
- (Carried over, still true) `MAX_AGENT_DEPTH = 1`, real coworker-agent
  execution goes through `execute_agent()` (subprocess-isolated).
  `agent/quiet_mode.py` remains the one shared suppression mechanism.
  Root `ARCHITECTURE.md` is authoritative over `docs/ARCHITECTURE.md`.

## Files recently modified

**Phase 9 / M4.3 (read-only conversation-history ToolSpecs)** — built,
tested, committed on branch `phase9-m4.3-history-search`:
```
1519a51 "Add read-only conversation history tools":
new:      tools/schemas/history.py (both ToolSpecs, error-state mapping)
new:      tests/test_history_tools.py (34 tests)
modified: tools/schemas/__init__.py (registered `history` in the
          side-effect import list, alphabetically after `graphify`)

(this documentation commit):
modified: ARCHITECTURE.md, CHANGELOG.md, SESSION_LOG.md, HANDOFF.md,
          ROADMAP.md
```

**Phase 9 Reliability S1.1 (History Store Concurrent Initialization
Determinism)** committed as `d38e794` ("Harden concurrent history
initialization"), pushed to `origin/main`, CI-verified on the first
attempt (GitHub Actions run `32659780845`):
```
modified: agent/history_store.py (new _set_journal_mode_wal() bounded
          retry, narrowly scoped to SQLITE_BUSY on the journal_mode=WAL
          transition specifically)
modified: tests/test_history_store.py (barrier-based concurrency test,
          repeated-round coverage, multi-process test, new
          TestHistoryBusySemantics class -- 6 net new tests)
modified: ARCHITECTURE.md, CHANGELOG.md, SESSION_LOG.md, HANDOFF.md,
          ROADMAP.md
```

**Phase 9 Reliability S1 (Structurally Safe Test Harness)** committed as
`e46f5bd` ("Harden test isolation and block external network") — see
that section above for the full file list; not repeated here.

**Phase 9 / M4.2** committed as `c0d5fc5` ("Add deterministic
conversation history capture") — see that section above for the full
file list; not repeated here.

**OpenClaw M1** committed as `d1eb813` ("Add read-only OpenClaw gateway
bridge"), pushed to `origin/main`, CI-verified.

**OpenClaw M1.5** committed as `8502c03` ("Fix OpenClaw bridge against
real Gateway"), pushed, CI-verified (GitHub Actions run `32073836073`).

**OpenClaw M2 + hardening pass** committed as `d270dc4` ("Add
permission-gated OpenClaw outbound messaging"), pushed, CI-verified
(GitHub Actions run `32310485314`):
```
new:      agent/openclaw_messaging.py
new:      tests/test_openclaw_messaging.py
modified: agent/openclaw_gateway.py (profile abstraction enforced by
          identity; _send_raw (renamed private);
          OpenClawUncertainDelivery, no longer auto-retried)
modified: agent/verification.py (new _verify_send_message_via_openclaw,
          registered in _VERIFIERS)
modified: config/settings.py (openclaw_messaging_enabled,
          openclaw_allowed_channels, openclaw_allowed_targets)
modified: tools/schemas/openclaw.py (send_message_via_openclaw, no
          account_id/thread_id)
modified: tests/test_openclaw_gateway.py (profile abstraction;
          TestSecurityAllowlist restructured; new
          TestProfileIdentityEnforcement for forged-profile rejection)
modified: tests/test_openclaw_tool.py (new tool registration/dispatch
          tests)
modified: tests/test_verification.py (new verifier tests)
modified: ROADMAP.md, ARCHITECTURE.md, CHANGELOG.md, SESSION_LOG.md,
          HANDOFF.md
```

**Graphify G0** committed as `7b4d0b6` ("Document local Graphify
development integration"), pushed, CI-verified (GitHub Actions run
`32312885286`):
```
new:      docs/GRAPHIFY.md
modified: .gitignore (graphify-out/ entry)
modified: ROADMAP.md, CHANGELOG.md, SESSION_LOG.md, HANDOFF.md
```

**Graphify G1** committed as `c99e792` ("Add read-only Jarvis code
graph intelligence"), pushed, CI-verified (GitHub Actions run
`32418542989`):
```
new:      agent/code_graph.py
new:      tools/schemas/graphify.py
new:      tests/test_code_graph.py
new:      tests/test_graphify_tools.py
modified: tools/schemas/__init__.py (registers the new graphify module)
modified: docs/GRAPHIFY.md (G1 section)
modified: ARCHITECTURE.md (tool count/list, agent/code_graph.py paragraph)
modified: ROADMAP.md, CHANGELOG.md, SESSION_LOG.md, HANDOFF.md
```

**Graphify G1.1** (this session — documentation only; confirm current
state with a live `git status`):
```
modified: docs/GRAPHIFY.md (incremental-extraction limitation section,
          precision rebuild workflow)
modified: ROADMAP.md, CHANGELOG.md, SESSION_LOG.md, HANDOFF.md
```
No `.py` file, `requirements.txt`, `.gitignore`, `CLAUDE.md`, or
generated `graphify-out/` content touched. The live local graph itself
was replaced with a clean full rebuild as part of this investigation,
but that's ignored local data, not a tracked-file change.

**Phase 9 / M4A + M4.1 (+ hardening pass)** — **committed** as `cd13e2a`
("Add durable FTS5 conversation history store"), pushed, CI-verified
(GitHub Actions run `32577096526`, success):
```
new file: agent/history_store.py
new file: tests/test_history_store.py
modified: ARCHITECTURE.md, ROADMAP.md, CHANGELOG.md, SESSION_LOG.md,
          HANDOFF.md
```
`CLAUDE.md` deliberately not touched. No production `history.db`
created by this pass. Clean-full Graphify rebuild done after this
commit landed (3335 nodes, 7079 edges, 164 communities,
`built_at_commit == cd13e2a`).

**Phase 9 / M4.2 (history capture)** — this list previously said
**uncommitted**; that went stale. Committed as `c0d5fc5` ("Add
deterministic conversation history capture") — see the dedicated M4.2
section near the top of this file for full current status:
```
new file: agent/history_capture.py
new file: tests/test_history_capture.py
modified: agent/executor.py, tests/__init__.py,
          tests/test_agents_executor_integration.py,
          tests/test_claude_gateway.py,
          tests/test_executor_multi_provider_fallback.py,
          tests/test_executor_phase5_integration.py,
          tests/test_phase6_security.py,
          tests/test_usage_limits_integration.py,
          tests/test_voice_session.py,
          tests/test_voice_skill_integration.py,
          ARCHITECTURE.md, ROADMAP.md, CHANGELOG.md, SESSION_LOG.md,
          HANDOFF.md
```
The 8 test files beyond `tests/__init__.py` were touched only to add
`agent.history_store.HISTORY_DB` isolation to their existing
setUp/tearDown, matching their own established pattern for
`execution_history.HISTORY_FILE`/`jarvis_state.STATE_FILE`/
`usage.USAGE_FILE` — see the dedicated M4.2 section above for why this
was necessary (a real, pre-existing test-isolation gap found during this
pass, not new scope creep). `agent/executor.py` gained exactly the
capture wiring described above — no other executor behavior changed.
`CLAUDE.md` not touched. No production `history.db` created after the
isolation fix landed (it briefly, accidentally existed mid-pass before
the fix — see the M4.2 section above; deleted, confirmed absent since).
Committed as `c0d5fc5`, pushed to `origin/main`.

**Committed history**, most recent first: `cd13e2a` (Phase 9 M4.1 —
durable FTS5 conversation history store), `77dee43` (Graphify
full-rebuild reliability workflow docs), `c99e792` (Graphify G1),
`7b4d0b6` (Graphify G0), `d270dc4` (OpenClaw M2 + hardening pass),
`8502c03` (OpenClaw M1.5), `f370c00` (M1 handoff doc update), `d1eb813`
(OpenClaw M1), `4265f55` (Phase 9 Milestone 3), `8d4da44` (Phase 9
Milestone 2), `7b67bf0` (Phase 9 Milestone 1), `d0f791c` (Obsidian
vault integration), `d3481fc` (Phase 9 Milestone 0 — GitHub Actions
CI). See `CHANGELOG.md` / `git log` for full history.

## Tests recently run and their results

`python -m unittest discover -s tests -t . -v` → a clean **1417 passed,
0 failed** run (up from the 1368 baseline this pass started from),
reproduced twice in a row in the finalization pass with a healthy disk.
This is the new canonical command — see Phase 9 Reliability S1's section
above for why the `-t .` flag is required, not optional. **This Mac's
disk reached complete exhaustion mid-pass** (0 bytes free at one point,
after fluctuating around ~99%/130-200Mi free) — `tests.test_history_store`
specifically flaked with SQLite `disk I/O error`/`HistoryBusy` failures
under that pressure; confirmed environmental (the same module flipped
between clean and failing purely as free space changed), not a code
defect, and every other test module was unaffected on every run.
**Resolved** in the finalization pass by freeing disposable caches
(brew/pip/uv/npm caches, browser cache) back to 13Gi free, with zero
personal data touched — see Phase 9 Reliability S1's section for the
full before/after. Maintain reasonable free-space headroom going
forward; this also affects the real production `history.db`'s own
writes, independent of testing. No paid API calls; the
external-network firewall independently confirmed zero real external
connections were possible. No real OpenClaw installation used; no real
`graphifyy`/`graphify` CLI needed by any test. Production-store metadata
confirmed unchanged before/after, across every run including the flaky
ones (see S1's section). This number will be stale the moment new tests
are added — re-run, don't trust it blindly, and use the `-t .` form when
you do.

**Since S1's commit**: S1.1 added 6 net new tests (1417 → 1423), run
three consecutive times clean, plus `tests.test_history_store` run 10
consecutive times clean on its own (89/89 each), then committed
(`d38e794`) and CI-verified on the first attempt — see Phase 9
Reliability S1.1's section above. The specific flaky test from S1's
first CI attempt (`test_concurrent_initialization_is_safe`) is now
deterministically fixed, not just retried past.

**Since S1.1's commit (feature branch `phase9-m4.3-history-search`)**:
M4.3 added 34 net new tests on top of S1.1's 1423 (→ **1457**), run
multiple times clean locally and CI-verified on the first attempt
(GitHub Actions run `32663268361`) for commit `1519a51`.

## What still needs to be done

1. **Nothing outstanding for Phase 9 / M4 (M4.1 through M4.4)** — all
   four sub-milestones committed on `main` (`cd13e2a`, `c0d5fc5`,
   `1519a51`/`b19f042`, `c992432`/`6fbc076`), all pushed and CI-verified.
   M4.4 remains off by default; turning it on for real is an open "Next"
   item in `ROADMAP.md`, not a code task.
2. **Nothing outstanding for Phase 9 Reliability S1 or S1.1** — both
   committed on `main` (`e46f5bd`, `d38e794`), pushed and CI-verified
   (S1.1 on the first attempt).
3. **Nothing outstanding for OpenClaw M1, M1.5, or M2** — all committed
   (`d1eb813`, `8502c03`, `d270dc4`), pushed to `origin/main`,
   CI-verified.
4. **Nothing outstanding for Graphify G0, G1, or G1.1** — all committed
   (`7b4d0b6`, `c99e792`, `77dee43`), documented in `docs/GRAPHIFY.md`.
   The local graph will need a fresh full rebuild before it's trusted
   for anything touching M4.3/M4.4's files — HEAD has moved well past
   whatever commit it last reported `built_at_commit` against. Check
   `code_graph_status` first; don't assume it's current.
5. **Do not rebuild the real local graph reflexively** — refresh it only
   when actually useful before code-graph analysis on a future
   milestone, using the precision workflow in `docs/GRAPHIFY.md`'s
   "Known incremental-extraction limitation" section (clean tracked tree
   → back up the old graph outside the repo → re-extract from empty →
   validate → delete the backup) rather than a bare incremental
   `graphify extract . --code-only`.
6. **Do not configure a real OpenClaw messaging channel** (Telegram/
   Discord/WhatsApp/Slack/Signal/iMessage/...) until a specific channel
   is explicitly chosen. Do not start Graphify G2 (MCP, hooks,
   auto-rebuild — none implemented or assumed) or OpenClaw device
   capabilities/agent-routing integration until the user explicitly
   says so.
7. **Decide the `agent/memory/manager.py::search_scored()`
   `last_accessed` question** whenever it becomes a priority — should it
   remain persisted on every read, be batched, become optional, or
   become non-persistent? See "Current bugs / known issues" above and
   `ROADMAP.md`'s "Next" section. Not urgent; not a safety issue post-S1.
8. **Obsidian vault population + third-party vault UI evaluation**
   (`.relay/BACKLOG.md`) — fill `JarvisVault/` with real interlinked
   notes generated from `ROADMAP.md`/`ARCHITECTURE.md`/architectural
   decisions/conversation history (a first real use of M4.3's
   `search_conversation_history` beyond proving it works), then read
   the source of `github.com/Prompt-Surfer/obsidian-jarvis-ui` (AGPL-3.0,
   ships a local Express server) before running it — confirm what it
   exposes and that it binds to localhost only. Install it outside this
   repo (e.g. `~/jarvis-vault-ui`), never vendored in, and never wired
   into `agent/` — it must stay a separate, read-only viewer process.

## Exact recommended next steps

For the next session, in order of what's most likely to matter:

1. Re-verify this file against actual git state first (per `CLAUDE.md`'s
   NEW SESSION PROTOCOL) — confirm `git log`/`git status`/test count on
   `main` match what this file claims before trusting it. `main` should
   be at `6fbc076` (M4.4 wiring) or later if the Obsidian vault backlog
   work (below) has since landed.
2. The most likely next action is continuing (or picking back up) the
   Obsidian vault backlog work in `.relay/BACKLOG.md` — see item 8 above.
   If a relay report/plan sequence for it exists under `.relay/`
   (`report-b1.md`/`plan-b1.md` onward), read those first for exactly
   where that work left off; they are the authoritative record of that
   sub-thread, this file only summarizes it.
3. Run the clean-full Graphify refresh before relying on
   `analyze_code_impact`/`find_code_path` for anything M4.3/M4.4-
   touching — deliberately deferred past this documentation pass. Check
   `code_graph_status` first; it will report `stale` once HEAD has moved
   past the graph's last `built_at_commit`.
4. If the user wants to proceed with configuring a real OpenClaw
   messaging channel, that is a real, separate, higher-risk step (real
   external service credentials, a real live send) and should get the
   same explicit-approval treatment every OpenClaw milestone so far has
   had.
5. If a compatibility check against a newer OpenClaw release (beyond
   `2026.7.1-2`) becomes useful, the M1.5 real-Gateway smoke-test
   approach documented in `ARCHITECTURE.md`'s "Real-Gateway smoke-test
   isolation" note is the one to reuse (no `--dev`, explicit workspace
   patch, `plugins.enabled = false`) — offer this only with explicit
   confirmation, matching this project's standing real-API-cost/
   real-external-service sensitivity.
6. If/when there's real usage data for M4.4 (log volume from someone
   running with `PROACTIVE_HISTORY_ENABLED=true`), revisit whether
   500 tokens / 150ms / top-3 results are the right defaults before
   considering flipping `proactive_history_enabled` to `True` by
   default — see `ROADMAP.md`'s "Next" section for the specific
   questions worth answering first.

## Important context that would otherwise be lost

- **A security-critical assumption was caught and corrected within the
  same overall initiative, before being committed** — the original M1
  pass was explicit about its own uncertainty ("a documented assumption,
  not verified"), which is exactly what made the follow-up correction
  possible: the gap was documented, not hidden, so it could be found and
  fixed before ever reaching a real Gateway.
- **A community-sourced technical claim (GitHub issue #17571) was
  independently caught being partly wrong** during this session's own
  verification effort (stale payload-format version) — a concrete
  reminder that secondary sources, even ones that look authoritative
  (filed against the official repo, technically detailed), need
  independent verification for security-critical protocol details, not
  just for OpenClaw specifically.
- **OpenClaw is a real, external, independently-evolving project** —
  re-verify current docs/package versions before extending the bridge
  in a future session, don't assume this session's research stays
  accurate indefinitely.
- **Real API cost is a standing user concern** — OpenClaw M1 made zero
  live network calls of any kind; `npm pack` downloads used for
  verification are free, public package metadata/tarballs, not paid API
  calls.
- **The live app's actual running state is volatile and not tracked by
  git** — check `ps aux | grep CampusPilotAgent` / `grep streamlit`
  before assuming anything about what's currently running. Not
  specifically checked at the end of this session.
- **The user's real environment has a background-audio wake-word
  problem** (carried over from prior sessions) — confirmed live via
  audit-log transcripts that were clearly TV/video content, not the
  user speaking. Partially mitigated (voice-confirmation gating,
  quiet/sleep/off modes); the underlying over-sensitive wake-word
  detection itself has not been re-tuned.
- **The user explicitly deferred** linking real OpenAI/Anthropic Admin
  API keys for authoritative billing reconciliation — don't reopen this
  unprompted.
