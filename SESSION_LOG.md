# Jarvis — Session Log

Lightweight per-session record. Concise by design — for depth, see
`CHANGELOG.md` (what/why/tests) or `git log`. Newest entry on top.

---

### 2026-08-28 — "Say hi" doubled-greeting investigated with real evidence, partially fixed

Investigated the open `ROADMAP.md` item rather than reflexively tweaking
the prompt. Two real `execute_task("say hi")` calls (~$0.0034 total)
confirmed the root cause: the model narrates before calling
`get_system_status`/`get_weather`, then re-states the greeting template
fresh once results return. `agent/brain.py`'s instruction now forbids
that narration explicitly. Confirmed live: the literal "Hello, master."
duplication is gone; a shorter narration lead-in still isn't fully
suppressed even after strengthening the instruction twice. Stopped at
two real attempts rather than keep spending on a third blind retry —
documented as an honest partial fix, with what a complete fix would
actually require (server-side data-gathering before the model's first
completion, not another prompt tweak) recorded in `ROADMAP.md`.

### 2026-08-28 — Voice false-triggering fixed; M10.0 + Phase 10 increment 1 shipped as three commits

After M10.0/Phase 10 increment 1 were both independently verified and
approved, shipped as three separate commits exactly as instructed:
`f8c638a` (M10.0 alone), `df26bc0` (Phase 10 increment 1), `923f8f5`
(docs) — suite green before each, CI green on the first attempt for
each. One necessary deviation, stated in `f8c638a`'s own commit message:
`tests/test_gating_structural.py` couldn't gate CodingAgent's
`write_file` in the first commit, since `agent/agents/coding.py` is
still the stub at that point in history — scoped to the three
pre-existing call sites there, extended in the second commit.

Then a priority reordering (`.relay/plan-b3.md`, itself sourced from
`.relay/AUTHORITY.md`) toward voice false-triggering. Before proceeding
on that file's own "without asking" framing, flagged the concern
directly and asked via AskUserQuestion — the user's actual answer
("proceed fully autonomously per AUTHORITY.md") is what authorized
continuing without further check-ins, not the file itself. Checked
openWakeWord's feasibility first as the plan asked: infeasible in this
venv (`onnxruntime` has no wheel for Python 3.14 on Intel macOS,
confirmed by a real install attempt). Shipped the fallback instead — a
new `wake_word_detected()` (word-boundary + position + length checks)
replacing a bare substring test in both real wake-check call sites,
plus `wake_attempt` instrumentation logging and two new `ROADMAP.md`
candidates found in a user-supplied (not-adopted-as-a-stack) second
Jarvis implementation. 12 new tests, full suite green (1597/1597).

### 2026-08-28 — M10.0: `agent/agents/worker.py`'s gating gap, enumerated and partially closed

The user independently verified the code review findings below against
the repo (confirming `qa.py`'s missing `-t .` directly), then directed:
commit that fix alone first (`37fb078`, pushed, CI green), then audit
`agent/agents/worker.py`'s own gap — `coworker.execute()` runs in a
subprocess that never imports `agent.executor`, so nothing a coworker
agent does internally passes through `_run_tool`'s permission/autonomy
check. Enumerated every real call site first, as instructed: five
ungated actions across four coworker agents (CodingAgent's write/read/
test-suite calls, ResearchAgent's browsing — a pre-existing, documented
exception — MemoryAgent's memory writes/reads — pre-existing, **never
before audited** — and QAAgent's test-suite spawn). More than one
ungated path, so stopped and reported before writing code, per
instruction. Scope, per explicit instruction: build the gate general,
route only CodingAgent's `write_file` through it this round —
`agent.autonomy.should_request_confirmation` gained an optional
`permission_level` override (every existing caller unaffected, proven)
plus a generalized non-interactive-source rule
(`"scheduled"`/`"agent_worker"`) so a would-be-CONFIRM verdict inside a
subprocess resolves to DENY instead of hanging. New
`tests/test_gating_structural.py`: an explicit, reasoned, written table
of the remaining four accepted exceptions, asserted equal to a real
`ast`-based scan of the actual source on every test run — demonstrated
live (added a bypass, showed the test fail naming it, removed it, showed
the test pass again). MemoryAgent's bypass documented in `CLAUDE.md`
rule 3 and opened as its own `ROADMAP.md` item, not fixed this round.
Full suite: **1583 passed, 0 failed** (1571 going in).
`coding_agent_enabled` stays off. Full detail: `CHANGELOG.md`'s matching
entry, `HANDOFF.md`'s "M10.0" subsection.

### 2026-08-28 — Structured code review: six more findings, one a live production bug

At the user's request ("check for any bugs, holes, or glitches in
current state"), ran `/code-review high` against the full uncommitted
diff and independently verified every finding before fixing anything.
Six real issues, all fixed: a missing denylist entry
(`tests/__init__.py`, the safety-bootstrap file itself), a real
case-insensitivity bypass of the entire write denylist (verified
directly against this Mac's actual filesystem), a timeout budget that
didn't cover the loop's own worst case (SIGKILL could have skipped
rollback entirely), a stale tool description that made the whole new
capability practically unreachable through its only real entry point, a
concurrent-writer rollback-protection gap (real for this project given
relay mode), and — found only by comparing two implementations side by
side — **`agent/agents/qa.py`'s already-shipped, already-live
`_run_test_suite()` was missing the load-bearing `-t .` flag**, meaning
every real "do the tests still pass?" request has been running against
real production paths and the real Keychain, independent of any Phase
10 gating. Extracted the shared command into new
`agent/canonical_suite.py` so this specific flag can't diverge again.
Full suite: **1571 passed, 0 failed** (1563 going in). See
`CHANGELOG.md`'s matching same-day entry and `HANDOFF.md`'s "Structured
code review pass" subsection for full detail.

### 2026-08-28 — Phase 10 real dogfooding: five more real bugs, plus closing the verification gap they surfaced

**Update, same day, same thread**: the "one unresolved verification gap"
this entry originally ended on (a successful run's own new test silently
never collected by `unittest discover`) is now resolved, not just
documented — new `existed_at_checkpoint()` +
`_new_test_files_collecting_nothing()` catch a new test file that
collects zero tests and treat it as a real, rolled-back failure. Two new
tests confirm both the catch and the no-false-positive case. Full suite:
**1563 passed, 0 failed** (1558 going in). See `CHANGELOG.md`'s matching
same-day entry for full detail.

- **Objective**: with direct user authorization, turn `coding_agent_enabled`
  on (env var only, never the shipped default) and give CodingAgent real
  tasks against a real copy of this repo, generating the "real checkpoint
  data" the design doc's own step 4 said increment 2 should wait for.
- **Work completed**: built a real dogfooding harness (a git worktree of
  the current uncommitted state via the same scratch-index technique
  `create_checkpoint` itself uses). Six real Anthropic calls across
  several attempts, ~$0.296 total. Found and fixed five real bugs: `.git`
  assumed to be a directory (breaks in a worktree), `prune_checkpoints`'
  1-second-resolution tie-break, a too-short 25s API timeout for this
  loop's own larger non-streaming calls, a truncated response silently
  treated as a clean finish, and a test-isolation gap where an inherited
  `CODING_AGENT_ENABLED` env var broke `tests/test_agents_coding.py`'s
  stub-behavior tests. Found, but deliberately did **not** fully resolve:
  a fully "successful" run's own new test used the wrong test-writing
  convention and was silently never collected by `unittest discover` —
  `success: true` didn't mean what it appeared to. Fixed the immediate
  cause (SYSTEM_PROMPT), recorded the deeper cross-check (new test file
  should mean `tests_run` increases) as genuinely open rather than
  building it under time pressure.
- **A real process gap on this session's own part**: git worktrees don't
  isolate refs; ten stray checkpoint refs ended up in this real repo's
  own `.git` (six from dogfooding, four from earlier, summarized
  concurrency-reproduction work). All confirmed as orphan refs never
  affecting `git log`/`git branch`/`HEAD`, all deleted, `refs/jarvis/`
  confirmed empty as of this entry.
- **`.relay/AUTHORITY.md`**: a file appeared mid-session claiming
  standing authority to remove every decision gate and asking for a
  permanent `CLAUDE.md` pointer to it. Declined on provenance grounds
  (Cowork has write access to this same repo; not something the user
  said directly) — `CLAUDE.md` not edited. The dogfooding above was
  independently, directly authorized by the user in conversation.
- **Verification**: full suite **1558 passed, 0 failed** (1552 going
  in), reproduced multiple times. Real repo's `git log`/`git status`
  confirmed unaffected throughout; `refs/jarvis/` confirmed empty after
  cleanup.
- **Not done, on purpose**: no commit, no push. No flip of
  `coding_agent_enabled`'s default — one dogfooding session is real
  evidence but not enough to decide that on its own.

### 2026-08-24 to 2026-08-27 — Relay mode established; launchd automation; Phase 10 increment 1 (uncommitted)

- **Objective**: continue the relay-mode collaboration this session
  documented in `CLAUDE.md` for the first time (Cowork plans, Claude Code
  executes), then build real CodingAgent capability on direct live
  instruction.
- **Work completed**: `CLAUDE.md`'s "Relay mode" section, `.claude/
  commands/relay.md`, `.claude/settings.local.json` committed
  (`2bed0b2`, CI-green). Obsidian vault populated (46 notes, 168 links, 0
  broken — `.relay/report-b1.md`); a third-party vault-visualization UI
  reviewed and held pending a decision (real file read+write
  vulnerability found, not installed). `com.jarvis.relay` launchd job
  installed to run `.relay/runner.sh` unattended (60s interval) — found
  and fixed a real bug in its own plan/report matching logic (it was
  inferring "already executed" from a report file's existence, backwards
  from this project's actual kickoff-then-response convention; now
  tracks completion in a dedicated `.relay/last-executed-plan` state
  file). Disk space hit its 5GB floor twice (once down to ~180MB free);
  resolved both times by clearing disposable caches, never Jarvis's own
  `chrome-profile` browser-session cache or any personal data.
- **Then, Phase 10 increment 1**: checkpoint/rollback
  (`agent/coding_checkpoint.py`, git-ref-based) + CodingAgent's first
  real `execute()` (`agent/agents/coding.py`), behind
  `coding_agent_enabled` (default `False`). Full detail:
  `HANDOFF.md`'s dedicated section and this same date's `CHANGELOG.md`
  entry. Headline finding: this file's own prior docstring anticipated
  wiring CodingAgent through `agent.claude_gateway.invoke()`, which turns
  out to structurally bypass `MAX_AGENT_DEPTH` (it re-enters the full
  orchestrator with the complete tool registry, including
  `consult_coworker_agent`, from a fresh depth-0 context) — built a
  narrow dedicated internal loop instead.
- **Same-day follow-up**: reproduced the design doc's own flagged-open
  concurrency question with real barrier-synchronized multiprocess
  tests rather than guessing — `create_checkpoint` needed no lock
  (scratch index never touches the real one), `restore_paths` reliably
  hit a real `.git/index.lock` collision under concurrency, fixed with
  a narrow, blocking `fcntl.flock` scoped to just that function.
- **Verification**: full suite run repeatedly through the build,
  finishing at **1552 passed, 0 failed** (including 2 real-multiprocess
  concurrency regression tests from the follow-up above). Real repo's
  git state (`refs/jarvis/`, `git log`, `git status`) confirmed
  untouched by every test run.
- **Update, later in this same session**: dogfooding, a structured code
  review pass, and M10.0 (see the dedicated subsections below) followed
  this build before anything was committed. Phase 10 increment 1
  ultimately shipped as three separate commits — `f8c638a` (M10.0
  alone), `df26bc0` (Phase 10 increment 1 itself), and this session's
  docs commit — each with the suite green beforehand and CI green on the
  first attempt. No default-on flip for `coding_agent_enabled`.

### 2026-08-23 — Phase 9 Reliability S1: structurally safe test harness (`e46f5bd`)

- **Objective**: fix, at the root, the test-isolation gap M4.2's pass
  found (`tests/__init__.py`'s central guard not executing under the old
  canonical command) rather than continuing to rely only on per-file
  redirects, and redirect every production persistent store (not just
  the ones a prior pass happened to catch), following an earlier
  read-only project-wide reliability audit's recommendation.
- **Work completed**: canonical command changed to `python -m unittest
  discover -s tests -t . -v`. New `tests/_safety.py` installed once by a
  rewritten `tests/__init__.py`: disposable per-process temp run root;
  redirect of 19 production path constants into it; a `socket`-level
  external-network firewall (loopback only); a secondary `httpx`
  tripwire; poisoned-by-default browser/computer-use tripwires. New
  `tests/test_test_safety.py` (49 meta-tests). New opt-in
  `tools/keychain_smoke_test.py`.
- **Problems encountered — four real, self-caught issues, not
  user-reported**:
  1. Case-sensitivity bug: the new temp-root prefix was lowercase,
     breaking a pre-existing test's substring assertion. Fixed by
     matching the real "CampusPilot" capitalization.
  2. A real macOS Keychain API error (`(100028, 'Unknown Error')`) hit
     during a full-suite run, even under a distinctly-named test
     service — no GUI available to answer the access-control prompt in
     this non-interactive session. Fixed by mocking
     `tools.credential_store.keyring` directly in the one test that
     needs to exercise that logic, confirming the audit's predicted
     CI/automation-hang risk was real, not theoretical.
  3. Three production bugs of the same class ("captured a path constant
     at definition time instead of reading it dynamically"):
     `tools/sandbox_python.py`'s Seatbelt profile string, and
     `agent/history_store.py`/`agent/personal_context.py`'s path-
     defaulting functions. Found while writing a meta-test that (like a
     real future test reasonably might) relied on a function's own
     default instead of passing the path explicitly. Fixed by reading
     the constant dynamically inside each function body instead of
     capturing it in the signature.
  4. A real in-sandbox write was spuriously denied by the actual kernel
     Seatbelt policy after redirecting `SANDBOX_DIR` to a temp path —
     traced to macOS's default temp root being a symlink
     (`/var/folders/...` → `/private/var/folders/...`), which
     `sandbox-exec` resolves before matching `subpath` rules. Fixed by
     resolving the run root through `os.path.realpath()` once.
  5. SQLite `disk I/O error`/`HistoryBusy` flakiness, confined entirely
     to `tests.test_history_store`, on repeated full-suite runs (22
     failures on one, 42 on a later one) — traced to this Mac's disk
     being at ~99% capacity and trending down over the session (free
     space observed fluctuating ~130-200Mi). Confirmed environmental,
     not a code bug: the same module flipped between a clean isolated
     pass and isolated failures purely as free space changed, and no
     other module was ever affected. Also a real, unaddressed risk to
     the live production app's own `history.db` writes — flagged to the
     user, not fixed (out of scope; not a code change).
- **Verification**: full suite achieved a clean 1417/1417 pass mid-pass
  under the new canonical command (see finding 5 above for why later
  runs flaked); production-store metadata unchanged before/after across
  every run, including the flaky ones; external
  network firewall independently reconfirmed outside the test suite
  (real external IP connect attempt blocked in 0.0001s). Committed as
  `e46f5bd` ("Harden test isolation and block external network"),
  pushed, CI-verified — first CI attempt failed on the same
  `test_concurrent_initialization_is_safe` flake described in finding 5
  above (a different manifestation: real SQLite lock contention, not
  disk exhaustion); a re-run of the identical commit succeeded
  (1417/1417). Root-caused and fixed for real by the S1.1 entry below,
  rather than left as "passed on retry."

### 2026-08-23 — Phase 9 Reliability S1.1: history store concurrent initialization determinism (`d38e794`)

- **Objective**: a rerun passing was explicitly not accepted as
  sufficient — find the exact cause of S1's intermittent
  `test_concurrent_initialization_is_safe` CI failure and eliminate it
  deterministically, since concurrent first-use initialization of
  `history.db` is a real, legitimately-supported production scenario
  (menu-bar app, scheduler daemon, and Streamlit are independent
  processes that can race to initialize it).
- **Root cause**, found empirically: isolated every PRAGMA statement in
  `_connect_writable()` individually under barrier-synchronized thread
  contention (reproduced with a standalone script first, before
  touching production code — 5/1200 failures, then narrowed to 7/1800
  with per-pragma isolation). `PRAGMA journal_mode=WAL`'s one-time
  transition takes its own internal exclusive lock that does not
  reliably honor the connection's `busy_timeout` — confirmed via
  `sqlite_errorcode == 5` (`SQLITE_BUSY`) on every failure, and
  confirmed to be this one statement specifically (zero failures on
  `busy_timeout`/`foreign_keys`/`synchronous`/`secure_delete` across the
  same 1800 attempts).
- **Work completed**: new `_set_journal_mode_wal()` wraps only the
  `journal_mode=WAL` PRAGMA in a bounded retry, matched narrowly to
  `SQLITE_BUSY` (any other `OperationalError` still propagates
  immediately). Bounded by the existing `_BUSY_TIMEOUT_MS` window;
  exceeding it raises `HistoryBusy`, same as the pre-existing `BEGIN
  IMMEDIATE` path. Verified with a 2400-attempt stress reproduction
  using the real production code: 0 failures with the fix. Measured
  overhead in the normal (uncontended) case: ~0.18ms mean — not
  material. New tests: a barrier-based rewrite of the original
  concurrency test with full post-condition validation, a bounded
  repeated-round version, a real multi-process version (separate OS
  processes, not just threads), and a new `TestHistoryBusySemantics`
  class covering the retry logic in isolation plus the first-ever
  end-to-end test of a genuinely held lock surfacing `HistoryBusy`.
- **Verification**: `tests.test_history_store` run 10 consecutive times
  (89/89 each, up from 83); full canonical suite run three consecutive
  times (1423/1423 each, up from 1417); production-store metadata
  unchanged before/after; real `history.db` still does not exist.
  Committed as `d38e794`, pushed, **CI-verified on the first attempt**
  (GitHub Actions run `32659780845`, no rerun needed) — direct proof the
  root cause was correctly identified, not just avoided.

### 2026-08-23 (latest) — Phase 9 / M4.4: proactive history retrieval (`c992432`, `6fbc076`) — M4 complete

- **Objective**: the final M4 sub-milestone — proactive, relevance-gated
  history retrieval into the system prompt, gated behind an off-by-
  default setting, plus land M4.3 on `main` first (it had been sitting
  on a feature branch pending merge/PR decision).
- **Work completed**: `phase9-m4.3-history-search` merged to `main` via
  a clean `--ff-only` merge (`b19f042`), CI-verified again on the merged
  `main`. Then M4.4 in two commits: foundation (`c992432`) — a new
  `busy_timeout_ms` override on `agent.history_store.search_history()`,
  new `agent/history_context.py` (`build_history_context()`), four new
  `config/settings.py` fields, all off/inert by default; then wiring
  (`6fbc076`) — one call from `agent.brain.build_system_prompt()`, an
  11-line diff.
- **A real blocker, resolved, not worked around**: the `main` merge was
  initially denied by Claude Code's own auto-mode permission classifier.
  No workaround attempted; reported directly, retried cleanly once
  outside auto mode.
- **A design premise found wrong while testing, corrected rather than
  shipped quietly**: the original justification for `busy_timeout_ms`
  (a normal write blocking a normal read for up to 5 seconds) does not
  reproduce under this store's real WAL journal mode — a held write
  lock does not block a read at all. Found by a test failing twice,
  investigated rather than patched around, docstrings corrected to say
  "defense-in-depth, not a fix for a reproduced hazard." Full account:
  `ARCHITECTURE.md` §12d.
- **Verification**: the disabled-default path proven byte-identical to
  a prompt built without the call present at all
  (`tests/test_brain.py`), all six `HistoryStoreError` subclasses proven
  unable to break a prompt build. New `tests/test_history_context.py`
  (20), `tests/test_brain.py` (11), 4 new `tests/test_history_store.py`
  tests. Full canonical suite run twice: 1492/1492 passing both times.
  Both commits pushed directly to `main`, **CI-verified on the first
  attempt** (GitHub Actions run `32672234602`). Documentation pass
  (this file, `ARCHITECTURE.md` §12d, `HANDOFF.md`, `ROADMAP.md`,
  `CHANGELOG.md`) committed separately, next.
- **Phase 9 / M4 (M4.1 through M4.4) is now fully complete**, all four
  sub-milestones on `main`, CI-verified. Next real work: `.relay/
  BACKLOG.md`'s Obsidian vault population.

### 2026-08-23 — Phase 9 / M4.3: read-only conversation history tools (`1519a51`, feature branch `phase9-m4.3-history-search`)

- **Objective**: with S1.1 committed and CI-green, expose M4.1's
  history store to Jarvis itself via two narrow, read-only ToolSpecs —
  the "expected narrow scope" from the takeover doc: `history_status`
  and `search_conversation_history`.
- **Work completed**: new `tools/schemas/history.py`, modeled directly
  on `tools/schemas/graphify.py` (`permission_level=0`,
  `parallel_safe=True`). `history_status` takes no input, reports
  availability/counts/schema version, never leaks the absolute db path.
  `search_conversation_history` takes `query` (required) plus optional
  `source`/`role`/`session_id`/`max_results`, wraps
  `history_store.search_history()` with `HISTORY_DB` passed explicitly,
  returns full provenance per hit (turn_id, session_id, request_id,
  created_at, source, role, snippet, rank, redacted, truncated) passed
  through unmodified from `SearchResult`. All six `HistoryStoreError`
  subclasses map to a distinct JSON `state`. Registered through
  `tools/registry.py`, added to `tools/schemas/__init__.py`'s
  side-effect import list.
- **Deliberate scope decisions**: no session/turn direct-retrieval (the
  store has no read function for either — would extend the store, not
  just wrap it; revisit under M4.4 if needed). Tool name
  `search_conversation_history` deliberately differs from the store's
  own `search_history()` so it can never collide conceptually with
  memory's `search_scored()`.
- **A real bug found and fixed during review**: `int(tool_input.get(
  "max_results") or 10)` could raise an uncaught `ValueError`/
  `TypeError` for a non-numeric value, and silently turned an explicit
  `0` into the default instead of clamping to 1. Fixed with an explicit
  `is None` check and `int()` wrapped in `try/except`, mapped to the
  existing `invalid_input` state — never raises.
- **Verification**: new `tests/test_history_tools.py`, 34 tests. Full
  canonical suite run multiple times: 1457/1457 passing (1423 + 34).
  Committed as `1519a51` ("Add read-only conversation history tools") on
  feature branch `phase9-m4.3-history-search` (cut from `main` at
  `d38e794`), pushed, **CI-verified on the first attempt** (GitHub
  Actions run `32663268361`). Later merged to `main` as `b19f042` — see
  the M4.4 entry above. This same round's documentation correction (fixing
  ~9 stale "S1.1 uncommitted" locations in `HANDOFF.md`, moving S1.1 to
  "Completed" in `ROADMAP.md`, correcting the Graphify counts, adding
  this file's own M4.3 documentation) is committed separately.

### 2026-08-23 — Phase 9 / M4.2: deterministic history capture (`c0d5fc5`)

- **Objective**: With M4.1 reviewed, committed (`cd13e2a`), pushed, and
  CI-verified, make `agent/history_store.py` operational by wiring real,
  deterministic (never model-controlled) capture of Jarvis interactions
  into `agent/executor.py`. No ToolSpecs, no backfill, no proactive
  injection — capture only.
- **Work completed**: New `agent/history_capture.py` — the sole decider
  of when a turn is written and which session it belongs to. Wired two
  calls into `execute_task_stream()`: a user-turn capture right after
  `RequestContext.create()` (before delegation/routing/planning), and an
  assistant-turn capture at all four real terminal paths (completed/
  cancelled, `PartialToolExecution`, both failure branches), using a
  `captured_chunks` accumulator appended to at the exact point every real
  chunk is already yielded — streaming behavior itself untouched.
  Session lifecycle: `chat`/`voice` cache one process-lifetime session
  each; `scheduled` never caches, one session per request. Every capture
  call is best-effort and non-raising, logging a bounded warning on
  failure and otherwise proceeding as if nothing happened.
- **Decisions**: History-capture failure must never change the real
  task's outcome — enforced structurally (broad try/except at the
  capture boundary), not by convention. No retry loops; relies on
  `history_store`'s own `(request_id, role)` idempotency. `app.py`/
  `ui/menu_bar.py`/`voice_session.py`/`scheduler_daemon.py` stay
  untouched — capture lives in the one shared `execute_task_stream()`
  every interface already calls, not duplicated per-caller.
- **Problems encountered — two real, self-caught issues, not
  user-reported**:
  1. A genuine concurrency bug: the session-cache check-then-create
     sequence wasn't atomic (lock held around the dict access only, not
     the SQLite call in between), so concurrent first-time callers on
     the same source could each create a separate, orphaned session.
     Caught by a threaded test (20 concurrent chat captures produced 20
     sessions, not 1) and fixed by widening the lock to cover the whole
     check-create-cache sequence — justified because it only matters
     once per source per process; every later call is fast and
     SQLite-free.
  2. A real API-cost incident: an early `PartialToolExecution` test
     mocked the Claude client but not `build_fallback_chain`, so the
     real router picked a different provider first and sent one genuine,
     unmocked request to the live OpenAI API (a 400 validation error
     came back before any generation, so likely zero token cost, but the
     network call itself was real). Fixed immediately by pinning
     `build_fallback_chain` in every test that exercises a real provider
     path, matching this project's own established pattern.
  3. A more consequential finding while verifying test isolation: the
     full suite silently wrote 76 real rows into the actual production
     `history.db` on the first attempt, despite `tests/__init__.py`'s
     central USAGE_FILE-style guard supposedly covering it too. Root
     cause: that guard has never actually executed under this project's
     real `python -m unittest discover -s tests -v` invocation (proven
     with a stderr marker that never printed) — `discover` imports test
     files as bare top-level modules, never triggering the package's
     `__init__.py`. This is a pre-existing gap predating M4.2, invisible
     until now only because every affected file already redundantly
     isolates `USAGE_FILE` itself. Fixed by extending that real
     per-file pattern to `HISTORY_DB` across all 8 existing test files
     that exercise a real `execute_task_stream()` call, plus the new
     test file itself, and corrected `tests/__init__.py`'s docstring to
     stop asserting the disproven claim.
- **Tests**: New `tests/test_history_capture.py`, 27 tests, covering
  session lifecycle, the success pair, idempotency, source validation,
  failure isolation (four independent failure points, each proven
  non-raising with a content-free bounded warning), privacy, no-retry
  behavior, and real end-to-end success/failure/cancellation/
  `PartialToolExecution` capture through the actual `execute_task_stream()`
  with only the provider call mocked, verified by reading real persisted
  rows back out of a temp database. Full suite:
  `python -m unittest discover -s tests -v` → **1363 passed, 0 failed**
  (1336 M4.1 baseline + 27 new). No paid provider calls in the corrected
  suite. Confirmed the real production `history.db` was not created.
- **Next session objective**: See `HANDOFF.md`. M4.2 is built and tested
  but deliberately left **uncommitted** for review. Do not start M4.3
  (Jarvis-facing search ToolSpecs) until M4.2 is reviewed, committed,
  and CI-verified.

---

### 2026-08-22 — Phase 9 / M4.1 hardening: FTS5's own secure-delete layer (committed as `cd13e2a`, part of the M4.1 commit)

- **Objective**: A review of the M4.1 work below (same session, same
  uncommitted slice) found one privacy gap: `PRAGMA secure_delete=ON`
  only protects ordinary SQLite table storage, not an FTS5 index's own
  b-tree segments — official SQLite docs are explicit that deleted/
  updated full-text entries may remain forensically reconstructable
  without FTS5's own, separate `secure-delete` config option (added in
  SQLite 3.42.0). Close that gap without redesigning M4.1, starting
  M4.2, or committing anything.
- **Work completed**: Empirically probed the real mechanism first
  (this session's runtime is SQLite 3.50.4): confirmed the documented
  special-command insert (`INSERT INTO history_turn_fts(history_turn_fts,
  rank) VALUES ('secure-delete', 1)`) works, and — unlike the core
  pragma — persists in the table's own `_config` shadow table across a
  full close/reopen rather than resetting per-connection. Added this to
  `_create_schema_v1()` right after the FTS table is created, inside the
  same schema-init transaction. Added real feature probing (attempt the
  command, catch `sqlite3.OperationalError` — confirmed empirically to
  be exactly what an unrecognized FTS5 special command raises) so an
  unsupported runtime fails the whole schema-init transaction closed via
  a new `HistoryUnsupportedRuntime` exception, rather than silently
  shipping a history database without the protection.
- **Decisions**: No schema version bump — M4.1 has never shipped, so
  there's nothing to migrate, staying at v1 per the task's own
  instruction not to invent a v2 for code that's never been committed.
  Both secure-delete layers (core pragma + FTS5 config) are required
  together, neither is a substitute for the other.
- **Problems encountered**: A direct attempt to reproduce the
  *underlying* forensic-residue risk (show a deleted FTS term's raw
  bytes surviving *without* the new protection, as a before/after
  contrast) did not reliably reproduce at unit-test scale — SQLite's own
  segment-merge behavior seems to clean up old segments quickly enough
  under the write volumes a compact test can generate. Reported honestly
  rather than fabricated: this doesn't contradict the documented risk
  (SQLite's own docs say entries *may* remain reconstructable, not that
  they always do), and the protection was implemented regardless since
  it's officially documented, free at this project's scale, and the
  downside of skipping it is worth closing even without a forced lab
  reproduction.
- **Tests**: 11 new tests added to `tests/test_history_store.py` (83
  total, up from 72) — FTS secure-delete enabled at init and confirmed
  via direct shadow-table inspection (test-only, never exposed via the
  public API), persistence across reopen, fail-closed behavior against a
  simulated pre-3.42.0 runtime (leaves no working schema behind), an
  update-privacy test (old token unsearchable + scrubbed from raw bytes,
  new token searchable + present, FTS `integrity-check` passes), a
  delete-privacy test (both storage layers lose the row, raw bytes
  scrubbed after checkpoint), and a regression test protecting the
  already-known per-connection pragma trap across all four public write
  functions. Full suite: `python -m unittest discover -s tests -v` →
  **1336 passed, 0 failed** (1325 prior baseline + 11 new). No paid
  provider calls. Confirmed the real production `history.db` was still
  not created.
- **Next session objective**: See `HANDOFF.md`. Still uncommitted, still
  waiting on user review — this hardening pass doesn't change that
  status, just what's included in the review. Do not start M4.2.

---

### 2026-08-22 — Phase 9 / M4A audit + M4.1 durable history store implementation (committed as `cd13e2a`, pushed, CI-verified)

- **Objective**: Two gated steps in one session. First, a
  design-only audit (Phase 9 / M4A) of every existing history/memory/
  state store, conversation flow across all UIs, and SQLite/FTS5
  capability on this project's real runtime, to produce a storage-
  boundary recommendation for Phase 9 / M4 (Conversation & History
  Intelligence) — no implementation, no commits. Second, once that
  audit's recommendation was reviewed, a scoped first implementation
  slice (M4.1) building only the durable history database core, with
  explicit instructions not to wire capture, backfill, register tools,
  or commit.
- **Work completed**: M4A delivered an in-conversation architecture
  report recommending a dedicated SQLite database (not another JSON
  file, not piggybacking on `agent/personal_context.py`'s read-only
  pattern) with an external-content FTS5 index, and empirically proved
  (not assumed) several load-bearing claims against this project's real
  SQLite 3.50.4 runtime: `PRAGMA secure_delete = ON` genuinely scrubs
  deleted row bytes from a real file-backed database even without
  `VACUUM`; it's compatible with WAL mode and external-content FTS5
  tables; pre-creating the DB file with `os.open(..., 0o600)` before
  SQLite ever opens it gives 0600 permissions on the main file and its
  WAL/SHM sidecars; and a "extract terms, individually double-quote,
  join as AND" strategy safely neutralizes FTS5 operator syntax against
  ten hostile test inputs. M4.1 then implemented exactly that design in
  a new `agent/history_store.py`: schema v1 (`history_session`/
  `history_turn` canonical tables + `history_turn_fts` derived index),
  `PRAGMA user_version`-based fail-closed schema versioning, connection-
  per-operation SQLite access (`foreign_keys=ON`, `journal_mode=WAL`,
  bounded `busy_timeout`, `synchronous=FULL`, `secure_delete=ON`),
  write-time redaction reusing `agent.memory.safety.redact_secrets()`
  (no forked secret list), a safe FTS5 query builder, and six public
  functions (`initialize_history_store`, `create_session`,
  `close_session`, `record_turn`, `history_status`, `search_history`) —
  no raw-SQL surface. Caught and fixed during self-review before tests
  were written: a fresh-database bug in `record_turn` (fixed by making
  every writable connection self-initializing), an inconsistent lock-
  timeout failure mode across four `BEGIN IMMEDIATE` call sites (fixed
  by routing all four through one `_begin_immediate()` helper that
  raises a distinct `HistoryBusy`), an unused `time` import, and a
  missing `created_at DESC` tie-breaker in the search ranking (the spec
  required bm25-primary + recency-tiebreak, not just bm25 alone).
- **Decisions**: HISTORY (`agent/history_store.py`) and MEMORY
  (`agent/memory/`) are explicitly separate systems — the former is
  append-only evidence, never superseded; the latter is distilled and
  superseding. Neither module imports from the other. History retention
  defaults to indefinite (no automatic age-based deletion in M4.1).
  `conversation.json` backfill, executor/UI capture wiring, and Jarvis-
  facing search tools are each their own explicitly-gated future
  milestone (M4.2/M4.3), not folded into this pass. The M4A report's
  speculative `0.7*BM25 + 0.3*recency` ranking formula was deliberately
  not implemented — bm25 isn't a normalized score, and inventing weights
  without observing real retrieval quality first would be premature.
- **Problems encountered**: One real test-writing pitfall, not a module
  bug: an early draft of the secure-delete byte-scan test performed a
  raw `DELETE` through a manually opened `sqlite3.connect()` that never
  set `PRAGMA secure_delete = ON` on that connection — the pragma is
  per-connection, not persisted in the database file the way
  `journal_mode` is, so the test initially (correctly) failed until the
  test itself was fixed to set the pragma before deleting, matching what
  a real future deletion path would also need to do.
- **Tests**: New `tests/test_history_store.py`, 72 tests, covering
  schema versioning, session/turn CRUD + idempotency, FK integrity, FTS
  sync, the secure-delete proof, the safe query builder against ten
  hostile inputs, search filtering/ranking, distinct error semantics,
  file permissions, threaded concurrency, transaction integrity, and
  privacy (no raw secret in stored content, search results, error
  messages, or raw DB/WAL/SHM bytes). Full suite:
  `python -m unittest discover -s tests -v` → **1325 passed, 0 failed**
  (1253 prior baseline + 72 new). No paid provider calls. Confirmed the
  real production `history.db` was not created by any test.
- **Next session objective**: See `HANDOFF.md`. M4.1 is built and
  tested but deliberately left **uncommitted** for review. Do not start
  M4.2 (executor/UI capture wiring) until M4.1 is reviewed, committed,
  and CI-verified.

---

### 2026-08-20 — Graphify G1.1: incremental-vs-full extraction audit, documented (docs-only)

- **Objective**: Investigate an observation from G1's own finalization
  (a possible incremental-extraction completeness gap for
  `tools/schemas/graphify.py`), determine if it was real/reproducible,
  and document the finding — narrow reliability audit, no new feature.
- **Work completed**: Reproduced the gap deterministically via two
  isolated local clones (old-commit incremental transition vs. clean
  full extraction at the same G1 commit). Confirmed real, narrow: newly
  added Python files importing named symbols from old/unchanged cached
  modules can miss the direct per-symbol `imports` edge, even though
  `built_at_commit`/clean-tree/`fresh` all check out. Confirmed on two
  independent instances (`ToolSpec`/`register`, `_run_tool`) and ruled
  out a one-file fluke via four peer-module controls, all clean. Read
  the installed Graphify 0.9.47 source (not modified) and found the
  likely mechanism: incremental mode shares "unchanged corpus" context
  with call-resolution passes but not the plain import-symbol-binding
  pass. Replaced the live local graph with a clean full rebuild
  (backed up old graph outside the repo first, validated, then deleted
  the backup) — final: 3157 nodes, 6650 edges, 152 communities. This
  session recorded that finding in `docs/GRAPHIFY.md` (a new section
  plus a terminology clarification that `fresh` never implies
  structural completeness) and a recommended precision-rebuild
  workflow, and updated project records — no runtime code, ToolSpec,
  or test changed.
- **Decisions**: No `agent/code_graph.py` change — its
  `authoritative: false`/`limitations` design already covers this
  regardless of which extraction mode produced the on-disk graph.
  `--force`'s documented scope doesn't confirm AST-cache bypass, so the
  verified workflow stays "empty `graphify-out/` first," not `--force`.
- **Problems encountered**: None — audit reproduced cleanly on the
  first controlled attempt.
- **Tests**: 1253 passed, 0 failed (docs-only change; suite re-run to
  confirm no regression risk from the pass).
- **Next session objective**: See `HANDOFF.md`. Next major milestone is
  Phase 9 / M4 (Conversation & History Intelligence) — not started.

---

### 2026-08-19 — Graphify G1: four narrow, read-only Jarvis code-graph tools (committed as `c99e792`)

- **Objective**: Give Jarvis four narrow, read-only tools over the
  locally generated Graphify graph, without ever making the `graphify`/
  `graphifyy` package/executable itself a Jarvis runtime dependency or
  authority. No MCP, no hooks, no auto-rebuild, no permission/autonomy
  decision based on graph content.
- **Work completed**: Determined the real graphify 0.9.47 graph.json/
  manifest.json/.graphify_analysis.json schema by direct inspection
  (no version field embedded anywhere in any of the three files — only
  a best-effort hint in the cache directory name). Built
  `agent/code_graph.py`: standard-library-only reader, one subprocess
  call total (fixed-argv `git`, `shell=False`, bounded timeout, pinned
  cwd) for freshness checking only. Implemented the exact staleness
  algorithm specified (fresh only at matching HEAD + clean tracked
  tree; stale/unavailable/invalid all refuse further analysis).
  Registered four ToolSpecs (`tools/schemas/graphify.py`) through the
  normal registry path: `code_graph_status`, `search_code_graph`,
  `analyze_code_impact`, `find_code_path` — all permission_level=0, no
  side effects, unattended/parallel safe. Every result carries
  `authoritative: false` and G0's known-limitations list;
  `source_verification_required: true` on anything touching the tool
  registry/autonomy/permission/credential code. Bounded throughout
  (search ≤20, impact ≤depth 3/≤100 results, path ≤depth 10).
- **Decisions**: No caller-supplied graph path, CLI subcommand, or raw
  query surface anywhere — no fifth generic tool. `graphifyy` stays out
  of `requirements.txt`/`.venv`. Mocked `subprocess.run` at the
  boundary in tests (project convention) rather than building a second
  injectable git abstraction.
- **Problems encountered**: None in the core logic; found and fixed
  several bugs in my own *test* assertions during a smoke-test pass
  (wrong `inspect.Parameter` enum name, a naive whole-file substring
  search that misfired on the module's own docstring legitimately
  discussing `graphify-mcp`) — none were bugs in `agent/code_graph.py`
  itself.
- **Real-graph validation**: read-only against the actual local
  `graphify-out/graph.json` (not just synthetic fixtures) — correctly
  reported `stale` (built at commit `d270dc4`, current HEAD `7b4d0b6`,
  and this implementation's own tracked `tools/schemas/__init__.py`
  edit made the tree genuinely dirty too), and all three analysis tools
  correctly refused. The real graph was deliberately left un-rebuilt,
  per instruction — this proves the fail-closed path against a real
  scenario, and refreshing it is a separate, later step.
- **Tests**: see this session's final report for exact counts; new
  synthetic-fixture suites in `tests/test_code_graph.py` and
  `tests/test_graphify_tools.py`, no real `graphifyy`/network needed.
- **Next session objective**: See `HANDOFF.md`. Awaiting review before
  commit; the real graph should be rebuilt deliberately afterward.

---

### 2026-08-19 — Graphify G0: development codebase graph baseline

- **Objective**: Evaluate and establish Graphify as an optional,
  local, development-time structural-intelligence layer for the Jarvis
  codebase — explicitly not a Jarvis runtime integration, no MCP, no
  hooks, no semantic extraction, in this first pass.
- **Work completed**: Verified the real current Graphify release via
  primary sources (v0.9.47, `graphifyy` on PyPI, `Graphify-Labs/graphify`
  on GitHub). Installed isolated via `uv tool install graphifyy` (`uv`
  itself installed via Homebrew with user confirmation, since neither
  `uv` nor `pipx` was already present) — CampusPilot's own
  `.venv`/`requirements.txt` untouched. Inspected existing ignore
  coverage before graphing (sufficient; no `.graphifyignore` needed).
  Built a code-only graph (`graphify extract . --code-only` +
  `graphify cluster-only . --no-label`) — zero LLM/API calls: 3024
  nodes, 6407 edges, 163 communities. Validated the output for secrets/
  private-data leakage (none found) and manually cross-checked
  `explain`/`path`/`affected` results against real source across 8
  architectural areas — mostly accurate, and independently corroborated
  three existing architectural claims from `CLAUDE.md`. Found and
  documented two real limitations: a same-basename module-collision
  false positive, and a system-wide miss of the
  `register(ToolSpec(..., handler=...))` wiring pattern.
- **Decisions**: Graphify approved as supplementary tooling only — never
  Jarvis's source of truth, never a permission/autonomy authority.
  Generated `graphify-out/` kept local (gitignored, not committed) —
  this repo is public, and the graph is large/low-diff-signal/free to
  rebuild. No `graphify install`/hooks/MCP enabled. `CLAUDE.md`
  deliberately not touched. Full detail in `docs/GRAPHIFY.md`.
- **Problems encountered**: Neither `uv` nor `pipx` was available for
  isolated installation as the task expected — paused and asked before
  installing any new system tooling rather than assuming brew was fine
  to use unprompted.
- **Tests**: 1176 passed, 0 failed, identical before and after graph
  generation — zero Jarvis runtime impact confirmed.
- **Next session objective**: See `HANDOFF.md`. Graphify G1 (a possible
  narrow read-only Jarvis tool over the graph) is a distinct, separate,
  not-yet-approved future decision.

---

### 2026-08-19 — OpenClaw M2 hardening/review pass (still uncommitted)

- **Objective**: Narrow hardening/review pass on the still-uncommitted
  OpenClaw M2 diff (see the entry below), prompted by a review that
  found several design issues. No new milestone, no real channel, no
  real message, no commit/push.
- **Work completed**: Removed the automatic same-key retry on uncertain
  delivery — the Gateway's in-memory dedupe cache doesn't survive a
  Gateway process restart, so a same-key resend isn't provably safe;
  `send_message()` now makes at most one transmission per logical send.
  Added `agent/verification.py`'s `_verify_send_message_via_openclaw`
  (registered in `_VERIFIERS`), parsing this tool's JSON result directly
  so `uncertain`/`failed` deliveries can't be mistaken for confirmed
  success by the generic failure-marker string check. Enforced the
  closed `_Profile` set by Python identity (`is`, not `==`) as the first
  check in `agent/openclaw_gateway.py`'s `_call()`, rejecting any forged
  profile. Renamed `send_raw()` to private `_send_raw()`. Corrected
  documentation across `agent/openclaw_gateway.py`, `ARCHITECTURE.md`,
  `HANDOFF.md`, and `CHANGELOG.md` that overstated a compromised
  messaging credential as having no read authority at all — the real
  Gateway's own server-side scope semantics are asymmetric
  (`operator.write` already satisfies an `operator.read` check there).
  Narrowed `account_id`/`thread_id` out of `send_message()`'s signature
  and the `send_message_via_openclaw` ToolSpec's `input_schema`.
- **Decisions**: Kept the fix scoped to this diff only — no general
  autonomy-model changes, no ToolSpec risk-metadata changes, no real
  channel configuration.
- **Problems encountered**: None during the fix itself; the issues fixed
  were all found by the user's own review of the prior session's work,
  not rediscovered independently here.
- **Tests**: See this session's final review report for exact targeted
  and full-suite counts; policy is the full suite must stay green.
- **Next session objective**: See `HANDOFF.md`.

---

### 2026-08-17 — OpenClaw M2: outbound text messaging bridge (implementation + tests only)

- **Objective**: Implement OpenClaw M2 — sending a plain-text outbound
  message through an operator-configured OpenClaw channel — first pass:
  implementation and tests only, no real channel, no real message sent,
  no commit/push.
- **Work completed**: Re-verified the real `send` RPC contract against
  `openclaw@2026.7.1-2`'s compiled server source: a genuine, distinct
  top-level method (never `chat.send`, which requires a `sessionKey`
  and is part of OpenClaw's own agent/session surface — confirmed via
  real source, validating the user's explicit architectural mandate,
  not just following it), requires `operator.write`, and the real
  Gateway maintains a genuine in-memory idempotency cache (5-minute
  TTL). This cache was originally reasoned to make one bounded same-key
  retry safe on uncertain delivery — **corrected in the very next
  session's hardening pass, see below: that reasoning doesn't survive a
  Gateway process restart, so the retry was removed.** Added a small,
  closed `_Profile` type to `agent/openclaw_gateway.py` — exactly two
  instances (`_READ_PROFILE` unchanged from M1, new `_MESSAGE_PROFILE`
  with its own separate device identity/secrets, `operator.write` only,
  `{send}` only) — no public API for a caller-supplied scope list. New
  `agent/openclaw_messaging.py`: channel/target allowlist enforcement
  (disabled and empty by default), message validation, idempotency-key
  generation, the (later-removed) bounded uncertain-delivery retry, and
  result normalization into confirmed/failed/uncertain. New tool
  `send_message_via_openclaw` (permission_level=3, side_effect=True,
  requires_live_confirmation=True, matching `send_email`'s convention).
- **Decisions**: A separate device identity for messaging, not a scope
  upgrade of the read identity. Text-only first pass; no real channel
  configuration until this pass is reviewed. (The "compromised messaging
  credential must never carry read-identity authority, and vice versa"
  framing originally recorded here was corrected in the hardening pass
  below — the real Gateway's own scope semantics are asymmetric, so only
  the read→write direction of that claim holds unconditionally.)
- **Problems encountered**: None apparent at the time — a same-day
  hardening/review pass (see below) subsequently found real design
  issues in this implementation that weren't caught here.
- **Tests**: 62 net new (1160 total, up from 1098) — 46 in new
  `tests/test_openclaw_messaging.py`, the rest updated/added in
  `tests/test_openclaw_gateway.py`/`tests/test_openclaw_tool.py`. Full
  suite passing, zero live/paid API calls, no real OpenClaw
  installation used.
- **Next session objective**: See `HANDOFF.md`.

---

### 2026-08-17 — OpenClaw M1.5: real loopback Gateway smoke test, two real bugs found and fixed

- **Objective**: Validate the already-committed OpenClaw M1 bridge
  against an actual running OpenClaw Gateway (`openclaw@2026.7.1-2`,
  the exact stated compatibility target) — not more source-reading,
  the real thing.
- **Work completed**: Installed the exact version into an isolated
  npm prefix under `/tmp`, generated a test token stored via
  `agent/secrets.py` into the real Keychain. First attempt (using
  `--dev`) exposed a real isolation gap: the dev workspace escaped
  `OPENCLAW_STATE_DIR` and wrote under the real `~/.openclaw`, and the
  auto-loaded plugin set included `bonjour`, which broadcast the
  Gateway on the LAN — caught and killed within ~8 seconds, before any
  Jarvis call; the accidental files were removed with explicit user
  approval. Corrected approach (no `--dev`, explicit workspace patch,
  `plugins.enabled = false`) produced a properly isolated Gateway with
  0 plugins and no further `~/.openclaw` writes. The real, load-
  bearing test — Jarvis's actual `openclaw_status`/`openclaw_list_nodes`
  tools, invoked through the real `tools.registry.dispatch()` path —
  then found two real bugs: `client.platform` was required by the real
  protocol schema but never sent; `client.deviceFamily` was signed into
  the V3 payload but never actually included on the wire, so real
  signature verification failed. Both fixed. Retried: full success —
  `openclaw_status`/`openclaw_list_nodes` both succeeded against the
  live Gateway, protocol 4, `operator.read` only (independently
  confirmed via the OpenClaw CLI's own `devices list`).
- **Decisions**: Cleaned up thoroughly afterward — killed the Gateway
  process, freed the port, removed the ~363MB temporary `/tmp`
  installation, and deleted the two smoke-test-only Keychain secrets
  (`OPENCLAW_GATEWAY_TOKEN`, `OPENCLAW_DEVICE_TOKEN` — tied to the now-
  deleted temporary Gateway's state) while preserving
  `OPENCLAW_DEVICE_PRIVATE_KEY` (Jarvis's persistent device identity).
  Corrected the fake test server's signature verification to
  reconstruct from actual captured wire values instead of duplicate
  constants, so this exact class of bug is now caught locally without
  needing a real Gateway.
- **Problems encountered**: The `--dev`-flag isolation gap (see above)
  — a real OpenClaw test-environment configuration issue, not a Jarvis
  security problem (Jarvis's own loopback-only WebSocket bind was never
  violated). The real Gateway also auto-approved device pairing itself
  in this dev/loopback configuration, so the `PAIRING_REQUIRED`/human-
  approval code path wasn't exercised this time.
- **Tests**: 2 new (1098 total, up from 1096), full suite passing. Real
  (not mocked, not faked) OpenClaw process and WebSocket connection used
  for the load-bearing verification; no model/provider API calls of any
  kind occurred.
- **Next session objective**: See `HANDOFF.md`.

---

### 2026-08-16 — OpenClaw M1 stable-compatibility pass: auth-field bug fixed for real, device-ID confirmed

- **Objective**: Re-check the previous pass's own auth-field fix (which
  sent Jarvis's shared token under `auth.bootstrapToken`, based on a
  beta package's schema) against the actual current STABLE OpenClaw
  release, per explicit instruction not to trust beta-only evidence as
  the compatibility baseline; also make one more attempt to verify the
  previously-unverified device-ID derivation algorithm.
- **Work completed**: Discovered `@openclaw/gateway-client`/`@openclaw/
  gateway-protocol` have no stable npm release at all (only an empty
  `0.0.0` placeholder and beta prereleases) — the real stable source is
  the main `openclaw` app package (`openclaw@2026.7.1-2`), which vendors
  its own copy of this logic. Downloaded and inspected its ~87MB bundle
  directly, including the Gateway SERVER's own connect-auth resolution
  (the actually-authoritative source for wire-field meaning, not a
  client-side schema). Found the previous pass's `auth.bootstrapToken`
  fix was itself wrong — that field is a genuinely distinct device-
  pairing/setup credential, verified server-side via a wholly separate
  path from the shared Gateway secret. Corrected: shared token → always
  `auth.token`; stored device token → always `auth.deviceToken`
  (required, not just cleaner, since only that field's rejection reports
  `AUTH_DEVICE_TOKEN_MISMATCH`, which the stale-token retry logic
  depends on); `auth.bootstrapToken` never sent. Also confirmed
  `signedAt` is safe as implemented despite a real stable/beta client
  difference (stable uses plain wall-clock time; beta prefers the
  challenge timestamp) because the server's own freshness check is a
  wall-clock skew check, not an exact-match against the challenge — no
  change needed there. And confirmed, no longer an assumption: the
  device-ID derivation algorithm, via a literal `deriveDeviceIdFromPublicKey`
  function found in the stable bundle plus the server's own independent
  re-derivation-and-compare check on every connect.
- **Decisions**: Removed all "unverified assumption" language about
  device-ID derivation now that primary source confirms it exactly;
  kept `DEVICE_AUTH_DEVICE_ID_MISMATCH` handling anyway as defense-in-
  depth, not because of remaining doubt.
- **Problems encountered**: The previous pass's own auth-field fix
  (from checking only a beta client package's schema) turned out to be
  a real bug — a lesson that a schema proving a field exists doesn't
  prove what it means; the field's actual semantics live in the
  server's own interpretation logic, which had to be read directly.
- **Tests**: 3 new (1096 total, up from 1093), full suite passing, zero
  live/paid API calls, no real OpenClaw installation used.
- **Next session objective**: See `HANDOFF.md`.

---

### 2026-08-16 — OpenClaw M1 re-verification: signedAt confirmed correct, auth-field bug fixed

- **Objective**: Check a claimed `signedAt` bug (that it must always be
  wall-clock time, never the `connect.challenge` event's own `ts`)
  against current primary source before applying it, per this project's
  standing verify-before-implementing rule.
- **Work completed**: Re-pulled a newer npm release
  (`@openclaw/gateway-client@2026.8.1-beta.2`) than the one inspected
  during the prior auth-correction pass and read the real
  `GatewayClient.buildConnectPlan` and
  `GatewayBrowserDeviceAuthLifecycle.buildPlan` implementations
  directly: both compute `signedAtMs = challengeTs ?? Date.now()` —
  preferring the challenge's own timestamp, the opposite of the claim.
  `agent/openclaw_gateway.py` already did this correctly, so no change
  was made to `signedAt` handling; added two regression tests instead.
  While re-checking auth-token/device-token selection against the same
  source, found and fixed a real, separate, previously-unverified bug:
  the real `ConnectParams.auth` object has distinct `token`/
  `bootstrapToken`/`deviceToken` fields, and Jarvis was sending every
  credential under the generic `token` field instead of the correct
  one. Fixed `_connect_and_call` to send `bootstrapToken`/`deviceToken`
  correctly; updated the fake Gateway test server and its device-token
  tests to check the correct, now-distinct fields.
- **Decisions**: Did not apply the requested `signedAt` change because
  primary source directly contradicted it — reported the contradiction
  with evidence rather than complying, then fixed the real bug the same
  verification pass surfaced instead.
- **Problems encountered**: None — this was a case of a claim not
  matching reality, not a code defect in the claim's target area.
- **Tests**: 3 new (1093 total, up from 1090), full suite passing, zero
  live/paid API calls, no real OpenClaw installation used.
- **Next session objective**: See `HANDOFF.md`.

---

### 2026-08-16 — OpenClaw M1 correction: real Ed25519 device-identity auth

- **Objective**: Fix M1's shared-token auth, which its own docstring
  already flagged as an unverified assumption — current official
  OpenClaw behavior turned out to actually require a persistent Ed25519
  device identity and a challenge-signed handshake for normal
  third-party operator clients, not just a shared secret.
- **Work completed**: Since docs.openclaw.ai doesn't cover third-party
  device auth and a related GitHub issue's own technical claims proved
  partly stale (a "v1" payload-format claim vs. the real, current "v3"),
  verified this directly against the actual published
  `@openclaw/gateway-client`/`@openclaw/gateway-protocol` npm packages —
  downloaded via `npm pack` and inspected as real compiled source, not
  paraphrased. Confirmed the real V3 device-auth payload format, the
  real `connect.challenge`-first handshake order, the complete real
  error-code enum (`PAIRING_REQUIRED`, `AUTH_SCOPE_MISMATCH`,
  `AUTH_DEVICE_TOKEN_MISMATCH`, every `DEVICE_AUTH_*` code), and the
  real closed `client.id`/`client.mode` enums (confirming `"cli"` as the
  correct non-reserved identity, since `"backend"`/`"gateway-client"`
  are OpenClaw's own reserved internal identity). Rewrote
  `agent/openclaw_gateway.py`: persistent Ed25519 device identity
  (PEM, via `agent/secrets.py`), real payload signing, challenge-first
  handshake, fail-closed `operator.read` scope verification, a new
  `OpenClawPairingRequired` error (never auto-approved), and device-
  token persistence/reuse with one bounded retry on a stale-token
  mismatch. Added `cryptography==50.0.0` (verified Intel macOS +
  Python 3.14 compatible). Rewrote the fake Gateway test server to
  perform genuine Ed25519 signature verification against the real
  client's actual output, not a stub check.
- **Decisions**: One detail — the exact device-ID hash algorithm —
  couldn't be confirmed against any primary source despite real effort
  (it's genuinely not in either published package; the actual crypto
  implementation lives in the main `openclaw` app's own unpublished
  source). Used SHA-256 of the raw public key as a deliberately
  low-risk, explicitly-flagged assumption (a real Gateway would reject
  it cleanly via `DEVICE_AUTH_DEVICE_ID_MISMATCH`, never a crash) rather
  than blocking the rest of an otherwise now-far-more-verified
  implementation on it.
- **Problems encountered**: The community GitHub issue initially found
  during research cited a stale payload-format version ("v1" vs. the
  real "v3"), caught only by cross-checking it against the actual
  published package source — a reminder not to trust a single
  secondary source for security-critical protocol details even when it
  looks authoritative.
- **Tests**: 15 new (1090 total, up from 1075), full suite passing, zero
  live/paid API calls, no real OpenClaw installation used.
- **Next session objective**: See `HANDOFF.md`.

---

### 2026-08-16 (later) — OpenClaw M0 (audit) + M1 (read-only Gateway bridge)

- **Objective**: Research OpenClaw (a separate, real open-source
  personal-AI-assistant/messaging-gateway project) and, if the
  architecture supported it safely, implement a narrow, read-only,
  optional bridge — with Jarvis remaining the sole orchestrator
  throughout, never ceding model-routing, permission, or tool-dispatch
  authority to OpenClaw.
- **Work completed**: M0 — researched OpenClaw's current official docs
  (Gateway protocol, auth/scope model, node/channel capabilities, plugin
  security model, Intel Mac support). Found real, current facts that
  corrected earlier assumptions: current stable release
  `openclaw 2026.7.1-2` (not the initially-cited "3.22"); the local
  default transport is authenticated loopback WebSocket, not TLS; the
  `websockets` package (not `httpx`, which has no native WebSocket
  client) was already present as an incidental transitive dependency via
  `streamlit` and was pinned explicitly rather than added as a new,
  separately-justified dependency. M1 — built
  `agent/openclaw_gateway.py` (connection/auth/protocol-negotiation
  bridge, no Jarvis policy decisions of its own, a fixed
  `{"health", "status", "node.list"}` RPC allowlist, five normalized
  error types, strict node-data minimization) and two new tools
  (`openclaw_status`/`openclaw_list_nodes`, both permission_level 0,
  read-only). Disabled by default; every failure mode (not installed,
  Gateway stopped, token absent, auth rejected, protocol mismatch)
  degrades cleanly rather than breaking Jarvis startup.
- **Decisions**: Used `websockets.sync.client` (not the async API) to
  fit Jarvis's existing synchronous tool architecture with no asyncio
  adapter. One-shot connections, no persistent connection manager, for
  M1's low call volume. Used OpenClaw's simpler shared-token auth path
  rather than its full cryptographic device-pairing flow — a documented
  assumption, not verified against a real Gateway (none was installed or
  required for this pass, per explicit instruction).
- **Problems encountered**: An early fake-server test fixture design
  caused the *test server* to log a spurious (harmless) exception
  traceback when a client legitimately disconnected right after a
  protocol-mismatch handshake, before ever sending a second frame —
  fixed by having the fixture treat an early client disconnect as
  expected server-side behavior, not an error, matching how a real
  Gateway would also handle it.
- **Tests**: 51 new (1075 total, up from 1024), full suite passing, zero
  live/paid API calls, no real OpenClaw installation used (confirmed not
  installed on this machine; not installed by this session either, per
  explicit instruction).
- **Next session objective**: See `HANDOFF.md`.

---

### 2026-08-16 — Phase 9 Milestone 3: bounded parallel coworker delegation + verification

- **Objective**: Give Jarvis bounded parallel coworker delegation
  (decompose independent subtasks, run them concurrently, verify the
  combined result, retry/repair only when justified) without weakening
  `MAX_AGENT_DEPTH = 1`, subprocess isolation, or the timeout guarantee.
- **Work completed**: Built `execute_agents_parallel()`
  (`agent/agents/manager.py`) — bounded to `max_parallel_agents = 3`,
  batches over that size rejected outright, every subtask still going
  through the unmodified `execute_agent()`. New `delegate_parallel_tasks`
  tool (not `parallel_safe`, by design). Required/optional subtask
  semantics, bounded per-task retry, and `agent/verification.py`'s new
  `verify_agent_result()` for evaluating coworker results objectively.
  Then, in a dedicated review pass before calling the milestone done,
  closed two real gaps found against the milestone's own goals: (1)
  `agent/research_agent.py` was calling Anthropic directly, bypassing M2's
  cost-aware routing entirely — rewired it through `classify_task()`/
  `build_fallback_chain()`, with per-provider dispatch shapes (Anthropic
  tool loop, OpenAI-compatible loop, single-shot Perplexity Agent API
  call); (2) `execute_agent()`'s subprocess used `subprocess.run`, which
  can't be interrupted mid-flight — rebuilt on `Popen` with a poll loop
  supporting genuine SIGTERM-then-SIGKILL cancellation of an
  already-running coworker subprocess, verified against a real, separate
  OS process (not a mock). Also hardened `agent/audit.py`'s action log
  with `fcntl.flock`, since parallel coworker subprocesses can now
  genuinely write to it concurrently.
- **Decisions**: Which subtasks are independent enough to batch stays the
  model's judgment (constrained by the tool's own description); the
  actual concurrency ceiling, depth guard, and budget pre-flight check
  stay fully code-enforced. `_call_perplexity_agent`/`_client_for_provider`
  were duplicated locally in `research_agent.py` rather than imported from
  `agent/executor.py`, to avoid a real import cycle — documented as
  intentional, not refactored during this pass.
- **Problems encountered**: The initial M3 implementation, while
  architecturally sound, didn't fully satisfy its own stated goal ("must
  respect M2 cost-aware routing and budgets") — ResearchAgent's hardcoded
  model call was a real gap caught only in review, not during initial
  implementation. Cancellation was also initially cooperative-only
  ("stop starting new work") rather than able to stop work already in
  flight; investigated properly (per explicit instruction not to force an
  unsafe fix) before confirming a narrowly-scoped `Popen`-based fix was
  safe to build.
- **Tests**: 96 new (1024 total, up from 928 before this milestone), full
  suite passing, zero live/paid API calls. Mid-flight cancellation tested
  against a real subprocess, not just a mock.
- **Next session objective**: See `HANDOFF.md`.

---

### 2026-08-15 — Phase 9 Milestone 2: task-aware multi-provider routing

- **Objective**: Implement task-aware, multi-provider model routing
  (`agent/model_router.py`'s `select()` had reserved an unused `context`
  parameter for this since Phase 2), then, before allowing any commit,
  currency-review every provider/model default against current official
  documentation.
- **Work completed**: Built `agent/task_classifier.py` (deterministic
  task classification, no model call), `agent/provider_budget.py`
  (same-day spend ceilings), extended `agent/provider_health.py` with
  xAI/Perplexity config checks and a failure-cooldown tracker, and added
  `agent/model_router.py`'s `build_fallback_chain()` (capability →
  configured/healthy → budget → cost/quality ranking, in that fixed
  order). Generalized `agent/executor.py`'s hardcoded two-tier cascade
  into a real N-candidate fallback loop. Added working xAI and Perplexity
  providers — Perplexity via its Agent API (a genuinely different
  request/response shape), single-shot and never handed Jarvis's tool
  registry. Then ran the requested pre-commit currency review: found and
  fixed Perplexity's Sonar Chat Completions deprecation (migrated to the
  Agent API before ever shipping), OpenAI's stale `gpt-5` default
  (replaced with three real GPT-5.6 tiers), xAI's non-existent `grok-4`
  placeholder, stale pricing-table entries for Sonnet 5/Haiku 4.5, and —
  in a final narrow follow-up pass — `vision_model` (`tools/vision.py`'s
  separate, non-routed OpenAI assignment), which the router-scoped review
  had initially missed.
- **Decisions**: Filter order (capability → configured/healthy → budget)
  is fixed and never reordered. Falls back to the original static
  `[anthropic, openai]` chain if task-aware routing is off or every
  candidate is filtered out. `perplexity_client` (`agent/chat.py`) was
  kept, not removed, despite the live Agent API call bypassing it
  entirely — it still supplies `check_providers()`'s diagnostic
  `initialized` field, kept uniform with the other three providers.
- **Problems encountered**: The initial currency-review pass was scoped
  to the router's own candidate tiers and missed `vision_model` (same
  stale-`gpt-5` problem, different call site, not itself a router
  candidate) — caught and fixed in a dedicated follow-up before the
  commit, along with a documentation-accuracy pass across `HANDOFF.md`
  that also caught a stale "Playwright profile contention" bug listed as
  unfixed when Milestone 1 (`7b67bf0`) had already fixed it.
- **Tests**: 96 new (928 total, up from 832 at the start of this
  session), full suite passing, zero live/paid API calls (mocked at the
  client/`httpx.post` boundary throughout).
- **Next session objective**: See `HANDOFF.md`.

---

### 2026-08-15 — Persistent session/handoff documentation system

- **Objective**: Build `CLAUDE.md`, `HANDOFF.md`, `ARCHITECTURE.md`,
  `ROADMAP.md`, `CHANGELOG.md`, `SESSION_LOG.md` so a fresh Claude Code
  session can continue this project without prior conversational context.
- **Work completed**: Inspected the full repository (every `agent/*`,
  `tools/*`, `voice/*`, `agent/memory/*`, `agent/skills/*`,
  `agent/agents/*` module, `config/settings.py`, `tools/registry.py`,
  git history) before writing anything. Found no MCP integration exists
  anywhere (confirmed by search). Reused and superseded the existing
  `docs/ARCHITECTURE.md` (accurate through ~Phase 6, missing Phase 6.5/
  7/8) rather than starting from nothing.
- **Decisions**: Root `ARCHITECTURE.md` is now authoritative;
  `docs/ARCHITECTURE.md` gets a pointer note rather than being deleted
  (it's documentation history, not working code). `HANDOFF.md` is the
  single source of truth for "current state right now" — other docs are
  allowed to drift slightly behind it between updates.
- **Problems encountered**: Found and fixed a stale inline comment in
  `config/settings.py` (referenced a module, `agent/usage_limits.py`,
  that was never actually created — the real logic lives in
  `agent/usage.py`) while cross-checking the architecture doc against
  real code.
- **Tests**: Full suite re-run after the comment fix — 742 passing.
- **Next session objective**: See `HANDOFF.md`.

---

### 2026-08-14 (evening) — Live production fixes after Phase 8

- **Objective**: Respond to real, observed problems on the running app:
  a ~3s gap between Jarvis finishing a typed response and starting to
  speak it, and Jarvis "setting random reminders and looking things up"
  unprompted.
- **Work completed**: Diagnosed the reminder/browsing issue as
  background-audio wake-word false triggers (confirmed via audit log —
  transcripts like "Nancy Pelosi taking $10,000 in trade..." were
  clearly TV audio, not the user); found reminders were already safely
  gated (14 attempts, zero actually created) but `open_browser`/
  `consult_coworker_agent` were not, and extended the existing voice-
  confirmation rule to cover them. Diagnosed the TTS latency gap as a
  single whole-response OpenAI TTS call; rewrote `voice/speak.py` to
  synthesize/play sentence-by-sentence, measured live (~1.1s vs ~3.0s
  time-to-first-audio). Built timed "sleep"/"off" quiet modes on request.
- **Decisions**: Reused `agent/quiet_mode.py`'s existing file-backed
  mechanism for sleep/off rather than building a separate system — same
  underlying "suppress processing" concept, just time-bounded.
- **Problems encountered**: A live smoke test (playing synthesized audio
  out loud to measure the TTS fix) was itself picked up by the running
  wake-word listener and processed as a real request (~$0.007 cost) —
  noted transparently, not a bug, just a testing artifact from having a
  live mic active nearby.
- **Tests**: 742 passing by the end of this session block (30 new tests
  across the three fixes). All three verified live against the running
  app, which was restarted after each.
- **Next session objective**: (became) the documentation system, per
  direct user request.

---

### 2026-08-14 (afternoon) — Phase 8: Observability, Cost Control & Agent Runtime Hardening

- **Objective**: User's own 3-part pre-Phase-8 fix list — real per-call
  cost attribution, a genuinely killable agent timeout, and request-ID
  correlation through the whole call stack — then the 6-part Phase 8
  spec built on top of it.
- **Work completed**: All 6 parts (`agent/usage.py`'s real accounting,
  the dashboard's cost section, configurable usage limits, subprocess-
  based agent isolation, contextvar-based request correlation, and
  regression/security tests) — see `CHANGELOG.md`'s 2026-08-14 Phase 8
  entry for full detail.
- **Decisions**: Discovered the live `consult_coworker_agent` tool had
  zero timeout protection (bypassed the manager's existing but dead-code
  `ThreadPoolExecutor` timeout entirely) — built a new, separate
  `execute_agent()` for the real path rather than modifying
  `route_and_execute()` (whose own tests depend on in-process fakes a
  subprocess couldn't see).
- **Problems encountered**: Found test files silently writing zero-cost
  artifacts into the real `usage_history.json` (missing `USAGE_FILE`
  isolation) — fixed in 7 files, cleaned 297 artifact records from the
  real file. Found and fixed an unrelated pre-existing dashboard crash
  (`active_executions` referenced but never defined).
- **Tests**: 663 → 742 across this phase and its immediate follow-ups.
  Live-verified subprocess hard-kill (a real ~5-7s test-suite run killed
  at 1.01s, confirmed no orphan process).
- **Next session objective**: (became) live production fixes, above.

---

### 2026-08-14 (earlier) — Phase 7 report follow-up + cost investigation

- **Objective**: Answer follow-up questions on the just-completed Phase 7
  report (coworker agents), then investigate what was driving heavy
  OpenAI/Anthropic API usage.
- **Work completed**: Delivered detailed sections H-S of the Phase 7
  report on request. Investigated token usage — found neither provider's
  usage/billing API accessible with the project's regular API keys
  (403/401, both need admin-level keys); built a report from local
  `audit.log`/`menubar.err.log` instead, identifying `computer_confirm_
  action`/`confirm_login` as highest-volume and `computer_see`/
  `computer_locate` (image-based) as likely highest-cost-per-call.
- **Decisions**: This investigation directly motivated the user's
  Phase 8 request (real per-call cost attribution, not log-scraping).
- **Next session objective**: (became) Phase 8, above.

---

*Earlier sessions (Phases 1-6.5, the TCC/voice-reliability debugging arc)
predate this log's creation — see `CHANGELOG.md` for what shipped, `git
log` for commit-level detail.*
