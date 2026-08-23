# Jarvis — Changelog

Meaningful changes only, chronological. Each entry: what changed, why,
what it touched, how it was verified. Trivial fixes/refactors aren't
individually recorded — see `git log` for full commit-level history if
needed.

---

## 2026-08-23 (most recent) — Phase 9 / M4.2: deterministic history capture (built, tested, UNCOMMITTED)

**What**: The second implementation slice of Phase 9 / M4 (Conversation &
History Intelligence) — makes `agent/history_store.py` (M4.1, committed
`cd13e2a`) operational by deterministically capturing real Jarvis
interactions. New module `agent/history_capture.py` is the *only* place
that decides when a turn is written and which session it belongs to;
`agent/executor.py`'s `execute_task_stream()` calls it unconditionally at
fixed control-flow points — never behind a ToolSpec, never behind
anything a model decided. This is application infrastructure, the same
category as `agent/execution_history.py`'s existing
`record_started`/`record_completed` calls, not a new capability Jarvis
can choose to use.

**Capture points**: a user-turn capture immediately after
`RequestContext.create()` — before delegation, agent routing, or
`is_complex()`'s own planning-model call, the earliest point a real
`request_id` exists — so the fact the user asked survives even a crash
immediately after. An assistant-turn capture at every terminal path
(`execute_task_stream()` has exactly four: normal completion/
cancellation, `PartialToolExecution`, and the two provider-failure
branches), recording exactly the text chunks the caller actually
received. A plain list (`captured_chunks`) is appended to at the same
line every real chunk is already `yield`ed — the stream itself is never
buffered, delayed, or converted to a non-streaming API. A request with
zero visible output records no assistant turn — never a fabricated
transcript to keep a pair symmetrical.

**Session lifecycle**: `chat` and `voice` each get one process-lifetime
session (a module-level cache, lock-protected for the one-time
first-caller race — see the bug found and fixed below), reused across
every turn on that source for the life of the process, deliberately
never merged with each other even when both occur in the same process
(e.g. `app.py`'s Streamlit UI can emit both typed `chat` and mic-input
`voice` turns). `scheduled` gets a brand-new session on every single
top-level request, one request/one session, no caching. A small
`request_id -> session_id` map (populated by the user-turn capture,
consumed by the assistant-turn capture, bounded by in-flight request
count) guarantees both turns of one request land in the same session
even for `scheduled`.

**Failure isolation**: `capture_user_turn`/`capture_assistant_turn`
catch every exception internally and never raise. A `history_store`
failure (locked db, corrupt schema, unsupported source, anything) is
logged as a bounded `history_capture_failed`/`history_capture_skipped`
warning (operation/source/request_id/error-type only, never raw turn
content) and otherwise ignored — the real task's outcome, tool
execution, and assistant answer are never affected. Exactly one capture
attempt per logical operation, no retry loop, relying on
`history_store`'s own `(request_id, role)` idempotency.

**Bug found and fixed during development, self-caught before tests
locked it in**: the original session-cache logic checked "is there
already a cached session" and, if not, created one — but held the lock
only around the dict read and the dict write, not the SQLite call in
between. Under real concurrency this let N threads all see "no cached
session yet" and each create a genuinely separate, orphaned session row
(proven by a threaded test: 20 concurrent first-time `chat` captures
produced 20 sessions, not 1). Fixed by holding the lock across the full
check-then-create-then-cache sequence — safe because this only matters
on the very first call per source in the whole process; every later call
takes a fast, lock-brief, SQLite-free path.

**Real API-cost incident, caught and fixed within the same pass**: an
early draft of the `PartialToolExecution` regression test mocked
`claude_client` but not `build_fallback_chain`, so the real
task-classifier/router picked a different provider as the first
candidate — sending one real, unmocked request to the live OpenAI API
(which returned a 400 validation error before generating any tokens,
so no completion cost was billed, but the network call itself was real).
Fixed by explicitly pinning `build_fallback_chain` to a single
deterministic candidate in every test that exercises a real provider
failure path, matching the pattern `tests/test_executor_multi_provider_
fallback.py` already established — applied defensively to every other
real-`execute_task_stream()` test in this pass too, not just the one
that broke.

**Test isolation — a second, more consequential finding**: while
verifying no test could write the real production `history.db`, the
suite polluted it anyway on the first real full-suite run (76 real rows,
2 sessions, matching the exact run's timing). Root cause: `tests/
__init__.py`'s package-level guard (the existing pattern already relied
on for `agent.usage.USAGE_FILE`) does **not** actually execute under
this project's real `python -m unittest discover -s tests -v`
invocation — confirmed with a stderr marker at that file's top level
that never printed during a real `discover` run. `discover` with no
`-t` flag imports each test file as a bare top-level module (e.g.
`test_history_capture`, not `tests.test_history_capture`), so `tests/
__init__.py` is never triggered. This is a pre-existing gap that
predates M4.2 and was invisible until now only because every test file
that touches `USAGE_FILE` already redundantly isolates it itself. Fixed
by extending that real, verified-working per-file pattern to
`agent.history_store.HISTORY_DB` in every one of the 8 existing test
files whose tests exercise a real (even if provider-mocked)
`execute_task_stream()`/`execute_task()`/`run_request()` call
(`test_claude_gateway.py`, `test_executor_multi_provider_fallback.py`,
`test_executor_phase5_integration.py`, `test_agents_executor_
integration.py`, `test_phase6_security.py`, `test_usage_limits_
integration.py`, `test_voice_session.py`, `test_voice_skill_
integration.py`), plus the new `tests/test_history_capture.py` itself.
`tests/__init__.py`'s docstring corrected to document this honestly
rather than repeat the disproven claim.

**What this pass deliberately does not do** (each its own later,
explicitly-gated milestone): no `search_conversation_history`/
`history_status` ToolSpec — `tools/registry.py`/`tools/schemas/__init__.py`
untouched; no backfill of `conversation.json`; no typed-memory change;
no proactive context injection; `app.py`/`ui/menu_bar.py`/
`agent/voice_session.py`/`agent/scheduler_daemon.py` themselves
untouched (all history writes happen inside `execute_task_stream()`
itself, which every one of them already calls — no independent capture
path anywhere).

**Verified**: new `tests/test_history_capture.py` (27 tests) covering
session lifecycle (chat reuse, voice separation, scheduled distinctness,
concurrent-scheduled distinctness, thread safety), the success pair
(same session/request_id, exact text, orphaned-assistant-without-user
case), idempotency, source validation (chat/voice/scheduled accepted,
unsupported source skipped without crashing), failure isolation
(session-creation failure, user-record failure, assistant-record
failure, each proven non-raising with a bounded content-free warning),
privacy (secret redaction in both turn roles, truncation), no-retry
behavior, and — via a real `execute_task_stream()` with only the
provider call mocked — end-to-end success, failure, cancellation, and
`PartialToolExecution` capture, each verified by reading the actual
persisted rows back out of a temporary SQLite database, not just
checking no exception was raised. Latency: ~4.6ms per capture call
(~9.3ms per full turn pair) against a temp db, negligible next to real
LLM call latency; `synchronous=FULL` was not weakened for speed. Full
suite: **1363 passed, 0 failed** (1336 M4.1 baseline + 27 new). No paid
provider calls in the final, corrected test suite. Confirmed the real
production `history.db` was not created by any test after the isolation
fix landed.

---

## 2026-08-22 — Phase 9 / M4.1 hardening: FTS5's own secure-delete layer (committed as `cd13e2a`, part of the M4.1 commit)

**What**: A review of the M4.1 slice below found one privacy gap: the
core `PRAGMA secure_delete=ON` already set on every write connection
only covers ordinary SQLite table storage — official SQLite
documentation is explicit that it does not by itself guarantee an FTS5
index's own b-tree segments stop retaining old term data after a
logical delete/update. FTS5 has its own, separate `secure-delete`
configuration option (added in SQLite 3.42.0) that covers exactly that
gap. This pass adds that second layer. Same uncommitted M4.1 slice,
same two files (`agent/history_store.py`, `tests/test_history_store.py`)
— no new files, no schema version bump (M4.1 has never shipped, so
there is nothing to migrate).

**What changed in `agent/history_store.py`**: `_create_schema_v1()` now
enables FTS5's own secure-delete immediately after creating
`history_turn_fts`, via the documented special-command insert:
`INSERT INTO history_turn_fts(history_turn_fts, rank) VALUES
('secure-delete', 1)`. Verified empirically (not assumed) that this
command actually works against the real runtime, and that the setting
is a property of the *table* (persisted in its `_config` shadow table),
not the connection — unlike the core pragma, it survives closing and
reopening the database without being set again. A new exception,
`HistoryUnsupportedRuntime(HistoryStoreError)`, is raised and fails the
whole schema-initialization transaction closed if this SQLite/FTS5
build doesn't support the command — verified via real feature probing
(attempting the actual command and catching `sqlite3.OperationalError`,
confirmed empirically to be exactly what an unrecognized FTS5 special
command raises on this runtime) rather than a brittle version-string
comparison. No history database is ever created with an FTS index
missing this invariant; there is no silent-degradation path.

**Verified**: 11 new tests in `tests/test_history_store.py` (83 total,
up from 72) covering: FTS5 secure-delete is enabled at schema init and
confirmed via the real `_config` shadow table (read-only inspection in
tests only — never exposed through the public API, verified by
source-inspection); the setting persists across a full close/reopen
with a connection that sets nothing itself; a simulated pre-3.42.0
runtime (a `sqlite3.Connection` subclass reproducing the exact
`OperationalError` an unrecognized FTS5 command raises) causes
`initialize_history_store()`/`create_session()` to fail closed with
`HistoryUnsupportedRuntime`, leaving no working schema behind
(`user_version` stays 0, no tables created); an update-privacy test
pushes a unique synthetic token through the real `record_turn()` path,
updates the canonical row through the same trigger a real update would
use, confirms the old token is no longer searchable and the new one is,
runs FTS5's own `integrity-check` special command to confirm the index
stayed internally consistent, checkpoints, and confirms the old token's
bytes are absent from the raw `.db`/`-wal`/`-shm` files while the new
content's bytes are present; a delete-privacy test does the same for a
canonical-table delete through the existing trigger, confirming both
storage layers (canonical row + FTS index) lose the row and the raw
bytes don't survive a checkpoint; and a regression test protects the
already-known per-connection trap (`secure_delete` resets on every new
connection) by intercepting every connection all four public write
functions open and asserting each one executed the pragma. Full suite:
**1336 passed, 0 failed** (1325 prior baseline + 11 new). No paid
provider calls. Confirmed the real production `history.db` was still
not created.

**Caveat honestly reported, not glossed over**: an attempt to
independently reproduce the underlying forensic-residue risk itself
(construct a database where a deleted FTS term's bytes remain
recoverable *without* the FTS5 secure-delete layer, to show a clean
before/after contrast) did not reliably succeed at the scale of a
compact test — SQLite's own automatic segment-merge behavior appears to
scrub old segments quickly enough under the write volumes a unit test
can practically generate. This does not contradict the documented risk
(which SQLite's own docs describe as index entries *may* remain
reconstructable, not that they always do, under access patterns a small
test can't easily reproduce — e.g. a mostly-idle database with sparse
deletes and no natural merge activity for a long stretch). The
protection is implemented regardless, since it's officially documented,
costs nothing at this project's write volume, and the downside of
skipping it is exactly the kind of risk that's prudent to close even
without being able to force-demonstrate it in a lab test.

---

## 2026-08-22 — Phase 9 / M4.1: durable history store + FTS5 core (committed as `cd13e2a`, pushed, CI-verified)

**What**: The first implementation slice of Phase 9 / M4 (Conversation &
History Intelligence), following the M4A audit's storage-boundary
recommendation. New module `agent/history_store.py` owns a dedicated
SQLite database at `~/Library/Application
Support/CampusPilot/history.db` — the only Jarvis-*written* SQLite
database in the project (`agent/personal_context.py`'s existing
`sqlite3` use is read-only against Apple's own Photos database). **Not
committed** — left for review per the task's own explicit instruction;
see `git status` for current state.

**Schema (`PRAGMA user_version = 1`)**: two canonical tables —
`history_session(session_id PK, source, started_at, ended_at)` and
`history_turn(turn_id PK, session_id FK, request_id, role, content,
created_at, redacted, truncated)`, indexed on `session_id`,
`request_id`, `created_at`, `role`, plus a partial unique index on
`(request_id, role) WHERE request_id IS NOT NULL` giving idempotent
turn recording without deduplicating future backfilled
(`request_id IS NULL`) rows against each other — and one derived index,
`history_turn_fts`, an external-content FTS5 table
(`content='history_turn'`, `content_rowid='turn_id'`,
`tokenize='porter unicode61'`) kept in sync purely by `AFTER
INSERT/UPDATE/DELETE` triggers. Canonical rows are the only
authoritative data; the FTS index can be lost/rebuilt without losing
real data.

**Connection policy**: stdlib `sqlite3` only (no new dependency),
connection-per-operation. Every write connection sets
`foreign_keys=ON`, `journal_mode=WAL`, a bounded `busy_timeout`,
`synchronous=FULL`, and `secure_delete=ON`. The last was verified
empirically against this project's real SQLite 3.50.4 runtime, not
assumed from documentation: a synthetic secret written then deleted
from a real file-backed temp database was unrecoverable from the raw
file bytes (even without `VACUUM`, only a `wal_checkpoint(TRUNCATE)`)
with the pragma on, and recoverable with it off — and it was confirmed
compatible with WAL mode and external-content FTS5 tables. A brand-new
database file is pre-created with `os.open(..., 0o600)` before SQLite
ever opens it, so there is no window where it exists at default
(often group/world-readable) permissions; WAL/SHM sidecar files were
verified to inherit that same 0600 mode. Read-only operations
(`history_status`/`search_history`) open via a `file:...?mode=ro` URI —
the same pattern `agent/personal_context.py` already established —
which structurally cannot create a missing database file, so those
entry points provably have no creation side effects (tested directly:
importing the module, calling `history_status()`, and calling
`search_history()` against a nonexistent path all leave no file behind).

**Redaction**: reuses `agent.memory.safety.redact_secrets()` — no
separate secret-pattern list was created. Every turn's content is
redacted, then bounded to 4000 characters, before it ever reaches
`sqlite3.execute()`; the unredacted form is never persisted.
`redacted`/`truncated` boolean flags on each row record whether either
transformation actually changed anything.

**Safe search**: `search_history()`'s query builder extracts literal
`\w+` terms from caller input, individually double-quotes each one, and
joins them as an implicit-AND FTS5 expression — verified against ten
hostile/operator-shaped inputs (bare `or`, `cats OR dogs`, `NEAR miss`,
`a NOT b`, `col:value`, parenthesized boolean expressions, embedded
quotes, wildcards, minus-exclusion syntax, pre-quoted phrases) that none
reach FTS5's `MATCH` parser as anything but literal terms. Ranking is
`bm25()` ascending (FTS5's bm25 returns more-negative values for better
matches) with `created_at DESC` as a tie-breaker only — the M4A report's
speculative `0.7*BM25 + 0.3*recency` weighted formula was deliberately
**not** implemented, since bm25 isn't naturally a normalized 0-1 score
and inventing weights without observing real retrieval quality first
would be premature.

**Public API**: `initialize_history_store()`, `create_session()`,
`close_session()`, `record_turn()`, `history_status()`,
`search_history()` — no raw-SQL or generic execute/query surface, so a
future ToolSpec can't be widened into arbitrary SQLite-file access.
Distinct exception types (`HistoryUnavailable`, `HistorySchemaError`,
`HistoryCorruption`, `HistoryValidationError`, `HistoryBusy`, all under
`HistoryStoreError`) keep "the store doesn't exist yet" distinguishable
from "your input was invalid" from "something is actually wrong with
the database" from "a write lost a lock race" — collapsing these into a
silent empty result would hide a real corruption/lock problem behind
what looks like an ordinary empty search.

**What this pass deliberately does not do** (each its own later,
explicitly-gated milestone — M4.2 through M4.4): no automatic capture
wiring into `agent/executor.py`/`app.py`/`ui/menu_bar.py`/
`agent/voice_session.py`/`agent/scheduler_daemon.py` — every write
today happens only because a caller (currently only this module's own
test suite) explicitly calls `create_session()`/`record_turn()`; no
backfill of `conversation.json`; no Jarvis-facing `search_history`/
`history_status` ToolSpec registered in `tools/registry.py`; no
proactive context injection; no automatic age-based deletion (retention
defaults to indefinite, matching the product decision already made for
this store).

**Verified**: new `tests/test_history_store.py` (72 tests) covering
schema versioning/fail-closed-on-newer-schema, session/turn CRUD and
idempotency, FK integrity, FTS sync (insert/update/delete), the
secure-delete byte-scan proof, the safe query builder against all ten
hostile inputs, search filtering/ranking/snippet-bounding, distinct
error semantics, file-permission checks (main DB + WAL + SHM sidecars),
threaded concurrency (concurrent init, concurrent distinct writes,
concurrent duplicate-request-id races), transaction/rollback integrity,
and privacy (synthetic secrets never appear in stored content, search
results, error messages, or raw DB/WAL/SHM bytes on disk). Full suite:
`python -m unittest discover -s tests -v` → **1325 passed, 0 failed**
(1253 prior baseline + 72 new). No paid provider calls. Confirmed the
real production `history.db` was not created by any test or by
importing the module; confirmed no other tracked file was modified
except `ARCHITECTURE.md`/`ROADMAP.md`/`CHANGELOG.md`/`SESSION_LOG.md`/
`HANDOFF.md` (this documentation pass) plus the two new files above.

---

## 2026-08-20 — Graphify G1.1: verified incremental-vs-full extraction audit, documented rebuild workflow

**What**: A narrow, documentation-only finalization of the G1.1
investigation (itself a reliability audit, no tracked-source change) —
recorded the verified findings in `docs/GRAPHIFY.md` and confirmed the
live local graph (already replaced with a clean full build during the
audit) is in a known-good state. No Jarvis runtime code, ToolSpec, or
test changed in this pass.

**Verified finding**: Graphify 0.9.47's incremental extraction mode
(the default behavior of `graphify extract . --code-only`, which reuses
its on-disk manifest/AST cache) can silently omit a direct per-symbol
`imports` edge when a newly-added/changed Python file imports a named
symbol from an old/unchanged (cached) module — even though the graph's
`built_at_commit` matches HEAD, the tracked working tree is clean, and
`code_graph_status` reports `fresh`. Confirmed via a controlled audit:
two isolated local clones (`git clone --local --no-hardlinks`, outside
the repo), one reproducing the exact old-commit-to-G1 incremental
transition (3157 nodes / 6655 edges), one a clean extraction with no
prior cache at the identical G1 commit (3157 nodes / 6650 edges).
`tools_schemas_graphify --imports--> tools_registry_toolspec`/
`tools_registry_register` were present only in the clean extraction;
a second, independent instance (`tests_test_graphify_tools --imports-->
agent_executor_run_tool`) confirmed the pattern generalizes, not a
one-file fluke. Four unrelated pre-existing `tools/schemas/*.py`
modules were checked as a control and showed zero difference between
incremental and full extraction. Every node, and every `contains`/
`calls`/`inherits`/`method` relation for the four new G1 files, was
100% identical between the two modes — the gap is real but narrow, not
broad graph corruption. Likely mechanism, confirmed by reading (not
modifying) the installed `graphify` package source: incremental
extraction shares "unchanged corpus" context with several call-
resolution passes (direct calls, indirect calls, member-call
resolution) but not with the plain `from X import Y` symbol-binding
pass that produces direct `imports`-relation edges — that pass's lookup
table is built only from the current incremental batch.

**Action taken during the audit (not this docs pass)**: the live local
`graphify-out/` was already replaced with a clean full rebuild (old
graph backed up outside the repo first, validated, backup then
deleted) — final graph: 3157 nodes, 6650 edges, 152 communities, built
at `c99e792`, `code_graph_status` reported `fresh` and `authoritative:
false` immediately after. This documentation pass records that finding
and workflow; it does not repeat the rebuild.

**Documentation added** (`docs/GRAPHIFY.md`): a new "Known
incremental-extraction limitation" section explaining the finding, a
terminology clarification that `fresh` means "matches current HEAD with
a clean tracked tree" and never "structurally complete" or "built via a
clean extraction," and a 5-step recommended precision rebuild workflow
(clean tracked tree → move existing `graphify-out/` to a temp backup
outside the repo → re-extract from a clean directory → validate
`built_at_commit`/`fresh`/secret-scan/ignored-status/clean-tree → only
then delete the backup). Also notes that `graphify extract --force` is
documented as skipping "the incremental manifest gate and semantic
cache reads" but this audit did not establish whether it also bypasses
the AST cache — so a genuinely empty `graphify-out/` directory remains
the verified way to force a clean full extraction, not `--force` alone.

**Why no runtime change**: `agent/code_graph.py` already marks every
result `authoritative: false` with a `limitations` list, regardless of
which extraction mode produced the graph on disk — the existing
fail-closed design already covers this finding's practical
consequence. The fix here is informing the human/future-session
workflow around rebuilding, not the tool's own trust model.

**Files affected**: `docs/GRAPHIFY.md`, `CHANGELOG.md`, `SESSION_LOG.md`,
`HANDOFF.md`, `ROADMAP.md`. No `.py` file, `requirements.txt`,
`.gitignore`, `CLAUDE.md`, or generated `graphify-out/` content
committed.

**Verification**: `git diff --check` clean; secret scan of the tracked
diff found nothing; full suite confirmed green at the unchanged
1253-test baseline (docs-only change, no regression risk).

## 2026-08-19 — Graphify G1: four narrow, read-only Jarvis code-graph tools (committed as `c99e792`)

**What**: Building on G0's evaluation (below), gave Jarvis four narrow,
read-only tools over the locally generated Graphify graph. The
`graphify`/`graphifyy` package/executable itself is still never invoked
from Jarvis runtime — `agent/code_graph.py` (new) parses
`graphify-out/graph.json` with the standard library only (`json`, `os`),
never imports `graphifyy`, never shells out to `graphify`/`graphify-mcp`.
The one subprocess call anywhere in the module is a fixed-argv,
`shell=False` `git` invocation (`git rev-parse HEAD` / `git status
--porcelain --untracked-files=no`, 5-second timeout, cwd pinned to the
repo root) used only to decide whether the on-disk graph is still
trustworthy relative to the current commit and tracked working-tree
state.

Determined the real graphify 0.9.47 schema by direct inspection of a
generated graph.json/manifest.json/.graphify_analysis.json (not assumed
from documentation): node shape (`id`/`label`/`_callable`/
`_callable_class`/`file_type`/`norm_label`/`source_file`/
`source_location`/`community`), edge shape (`source`/`target`/
`relation`/`confidence`/`confidence_score`/`context`/`weight`), and
confirmed no "version" field exists anywhere in any of the three files
— the only on-disk version hint is the `graphify-out/cache/ast/v<N>`
cache-directory name, read as a best-effort, informational-only value.

**Staleness model** (agent/code_graph.py's `CodeGraphReader.status()`):
`fresh` only when the graph parses, `built_at_commit` equals the real
current git HEAD, AND the tracked working tree is clean (`git status
--porcelain --untracked-files=no` returns nothing -- untracked files,
including gitignored `graphify-out/` itself, never count as dirty,
matching the exact spec: a graph can be stale even at `built_at_commit
== HEAD` if tracked source has since changed). `stale` on either a
commit mismatch or a dirty tracked tree (or if git itself couldn't be
queried -- never silently treated as fresh). `unavailable` if
graph.json is missing. `invalid` on malformed JSON or a graph missing
the minimal required schema (`nodes`/`links`/`built_at_commit`).
`search_code_graph`/`analyze_code_impact`/`find_code_path` all refuse to
run any traversal unless state is exactly `fresh`, returning the same
small structured refusal shape (`ok: false`, `state`, `built_at_commit`,
`current_commit`, `reason`, `rebuild_required`) instead. Nothing
auto-rebuilds a stale graph -- that stays a manual step.

**Four ToolSpecs** (`tools/schemas/graphify.py`, registered through the
normal `tools/registry.py` path, no separate dispatch mechanism): all
`permission_level=0`, `side_effect=False`, `unattended_allowed=True`,
`parallel_safe=True`, no live confirmation -- matching
`get_system_status`'s risk class exactly.
- `code_graph_status` -- no input; always reports true state regardless
  of freshness.
- `search_code_graph` -- deterministic exact/prefix/substring matching
  against node id/label/norm_label/qualified `source_file:label`,
  capped at 20 results (hard cap), explicit `ambiguous: true` when
  multiple distinct nodes share an exact bare name rather than silently
  picking one (this is exactly how the module-name-collision limitation
  from G0 stays *visible* rather than hidden -- both `tools/registry.py`
  and `agent/skills/registry.py` surface distinctly for a `registry.py`
  query).
- `analyze_code_impact` -- reverse-adjacency BFS from an exact
  `node_id` (bounded depth, hard cap 3; bounded results, hard cap 100),
  cycle-safe via a visited-set, direct (depth 1) vs indirect
  distinguished, EXTRACTED/INFERRED confidence preserved per edge,
  sorted direct-before-indirect and EXTRACTED-before-INFERRED within a
  depth tier for determinism.
- `find_code_path` -- forward BFS shortest path between two exact node
  IDs (bounded depth, hard cap 10), cycle-safe, one path returned (not
  every path), clean `found: false` with no path.

**Never authoritative, by construction**: every result carries
`authoritative: false` and G0's exact known-limitations list (module-
basename collisions, missed `register(ToolSpec(..., handler=...))`
wiring, ambiguous bare-name resolution). A result touching
`tools/registry.py`, `ToolSpec`, `tools/schemas/`, `agent/autonomy.py`,
or a permission/credential-keyword path additionally carries
`source_verification_required: true` -- deterministic path-prefix/
keyword matching (`agent/code_graph.py`'s `_is_critical`), never a model
judgment call. Impact/path results are worded as structural
relationships only ("not proof of runtime behavior" / "not proof of
runtime call order"), never a guaranteed-effect claim.

**Security**: no caller-supplied filesystem path, CLI subcommand, or raw
query surface in any of the four `input_schema`s (verified by test); no
fifth generic "run a graph command" tool; `graphifyy` remains outside
`requirements.txt` and CampusPilot's `.venv`; no MCP registration, no
`graphify install`/hooks, no `graphify-mcp` invocation, no automatic
graph regeneration; no permission/autonomy/routing decision is ever
based on graph content -- `agent/autonomy.py` and `tools/registry.py`
remain untouched and are the only real enforcement points.

**Validation**: a synthetic-fixture test suite (`tests/test_code_graph.py`,
`tests/test_graphify_tools.py`) exercises every status/search/impact/
path/security/registration scenario without needing the real
`graphifyy` package, network, or the real 11MB graph. Additionally
validated read-only against the real local `graphify-out/graph.json`:
correctly reported `stale` (built at the prior commit `d270dc4`, current
HEAD `7b4d0b6`, and -- once this implementation's own tracked
`tools/schemas/__init__.py` edit landed -- a genuinely dirty tracked
tree too), and all three analysis tools correctly refused with the
structured stale shape -- proving the fail-closed path against a real
scenario, not only a mock. The real graph was deliberately **not**
rebuilt, per explicit instruction, so this stale state is expected and
will be addressed in a separate, deliberate refresh after this pass is
reviewed.

**Files affected**: `agent/code_graph.py` (new), `tools/schemas/graphify.py`
(new), `tools/schemas/__init__.py` (registers the new module),
`tests/test_code_graph.py` (new), `tests/test_graphify_tools.py` (new),
`docs/GRAPHIFY.md`, `ARCHITECTURE.md` (tool count/list, a short
`agent/code_graph.py` paragraph), `ROADMAP.md`, `SESSION_LOG.md`,
`HANDOFF.md`. `CLAUDE.md` deliberately not modified. No commit/push in
this pass, per explicit instruction.

**Verification**: see this session's final report for exact targeted/
full-suite test counts (policy: full suite must stay green at the
pre-G1 baseline of 1176 or higher).

## 2026-08-19 — Graphify G0: development codebase graph baseline

**What**: Evaluated and approved Graphify (`graphifyy` on PyPI,
`graphify` CLI, `Graphify-Labs/graphify` on GitHub) as an optional,
local, developer-facing structural code graph for this repo — a
supplementary navigation/impact-analysis aid for a Claude Code session
working on Jarvis, explicitly **not** a Jarvis runtime subsystem: no
ToolSpec registered, no `agent/executor.py` change, no MCP integration,
no Claude Code hooks, no CLAUDE.md change, zero change to any Jarvis
source file. Confirmed the real, current release via primary sources
(PyPI JSON API, GitHub API, the actual default `v8` branch README — the
stale `main` branch README pointed at a different GitHub account's CI
badge and was not used) rather than assumed: latest stable **v0.9.47**,
Python 3.10+, `graphify extract . --code-only` / `query` / `path` /
`explain` all exist and behave as expected, no telemetry.

Installed isolated via `uv tool install graphifyy` (Homebrew-installed
`uv`, since neither `uv` nor `pipx` was already present — confirmed with
the user before installing any new system tooling) into
`~/.local/share/uv/tools/graphifyy/`, entirely separate from
CampusPilot's own `.venv`/`requirements.txt`, both confirmed untouched
before and after.

Before graphing, inspected `.gitignore`/`.git/info/exclude` and every
top-level path *not* already covered by them (`.pytest_cache`,
`.streamlit`, `.claude`, `.agents`, `documents`, `database`) for
sensitive/private/generated content — found existing `.gitignore`
coverage already sufficient (covers `.env`, `.venv/`, `__pycache__/`,
`JarvisVault/`, `logs/`, build artifacts); no in-repo OpenClaw state or
Playwright browser-profile directory exists (both live outside the repo
under `~/Library/Application Support/CampusPilot/`). No
`.graphifyignore` was created.

Ran `graphify extract . --code-only` (local tree-sitter AST parsing
only, no API key set in the environment at all, zero network activity,
~12s) then `graphify cluster-only . --no-label` (community
detection/report generation, `--no-label` specifically to avoid
`cluster-only`'s default LLM-based community-naming step) — **zero
LLM/API calls, zero paid extraction, "Token cost: 0 input · 0 output"**
per the generated report. Result: 3024 nodes, 6407 edges, 163
communities, 96% EXTRACTED / 4% INFERRED edge confidence, zero import
cycles, `built_at_commit` correctly recorded as the exact HEAD this was
built from.

Validated the generated `graphify-out/` (graph.json 3.5MB, graph.html
2.9MB, GRAPH_REPORT.md 44KB, manifest.json 40KB, cache/ 4.5MB) directly:
zero secret patterns anywhere (`.env`, private keys, tokens), zero
ignored/private paths ingested (confirmed against all 196 manifest
entries), zero absolute local paths in any substantive content (only a
25-byte internal `.graphify_root` anchor file contains one).

Manually cross-checked Graphify's `explain`/`path`/`affected` output
against real source across the 8 architectural areas requested
(orchestration, tool registry, autonomy, provider routing, coworker
system, OpenClaw, voice pipeline, memory/Obsidian) rather than trusting
it — accurate down to the exact source line in nearly every case, and it
independently corroborated three separate claims already in
`CLAUDE.md`/`ARCHITECTURE.md` (the memory-wrapper pattern, the three
`execute_task_stream` entry points, the OpenClaw profile-scoped RPC
allowlist) purely from static call-graph analysis.

Found and documented two concrete, verified limitations (full detail in
`docs/GRAPHIFY.md`): (1) a same-basename module-collision false
positive — a dependency query for `tools/registry.py` incorrectly
attributed an edge that was actually `agent/skills/registry.py`,
confirmed specific to the basename collision via a clean parallel test
against `agent/autonomy.py`; (2) a system-wide miss of the
`register(ToolSpec(..., handler=...))` registration-wiring pattern —
invisible for all 14 `tools/schemas/*.py` modules, even though the
*same* OpenClaw tool's `agent/verification.py` dict-literal
`_VERIFIERS` registration *was* correctly picked up. Because of these,
Graphify must never be trusted alone for ToolSpec-to-handler wiring or
any permission/autonomy/credential-boundary question — direct source
inspection remains authoritative there.

**Decision**: generated `graphify-out/` artifacts are gitignored and
kept **local**, not committed — this repo is public, and a committed
graph would publish a detailed, algorithmically-ranked structural map of
exactly where this project's permission/autonomy/credential-separation
logic lives, on top of being a large (3-4MB), wholesale-rewritten,
low-diff-signal blob on every future extraction. Rebuilding is free
(~12s, zero cost) whenever actually needed.

**Files affected**: `.gitignore` (`graphify-out/` entry),
`docs/GRAPHIFY.md` (new), `ROADMAP.md`, `HANDOFF.md`, `SESSION_LOG.md`.
No Jarvis source file, test file, or `requirements.txt` touched.
`CLAUDE.md` deliberately not modified — Graphify's own Claude Code
hook/automation behavior has not been approved.

**Verification**: `python -m unittest discover -s tests -v` → 1176
passed, 0 failed, both before and after graph generation (identical
result — confirming zero runtime impact). `git diff --check` clean.
Secret scan of the tracked diff (`.gitignore`, `docs/GRAPHIFY.md`,
project record docs) found nothing.

## 2026-08-19 — OpenClaw M2 hardening/review pass (still uncommitted, no real channel)

**What**: A narrow hardening pass on the still-uncommitted M2 diff below,
prompted by a review that found several issues in the original design.
No new milestone, no real channel, no real message sent, nothing
committed or pushed.

1. **Removed the automatic same-key retry on uncertain delivery.** The
   original entry below treated the Gateway's in-memory dedupe cache as
   sufficient justification for one bounded same-key retry. Review
   established that justification doesn't hold across a Gateway process
   restart (the cache is in-memory, single-process, and gone on restart)
   -- so a same-key resend is not provably safe, and could send a real
   duplicate message if the Gateway restarted between successfully
   delivering the original message and Jarvis receiving the response.
   `agent/openclaw_messaging.py`'s `send_message()` now makes AT MOST ONE
   transmission per logical send; an `OpenClawUncertainDelivery` is
   reported as `delivery_status: "uncertain"` and left there, never
   auto-retried. The `idempotencyKey` is still generated and sent (
   protocol correctness / defense-in-depth against the Gateway's own
   retry machinery), just no longer used to justify a second attempt
   from this side.
2. **Added a dedicated post-action verifier.** `agent/verification.py`'s
   generic string check (a failure-marker scan) does not correctly read
   this tool's JSON result -- a body like `{"sent": false,
   "delivery_status": "uncertain", ...}` contains none of the
   `FAILURE_MARKERS` words and would otherwise pass. Added
   `_verify_send_message_via_openclaw`, registered in the existing
   `_VERIFIERS` dict: `confirmed` → `ok=True`; `failed` → `ok=False`
   with the error detail; `uncertain` → `ok=False` with a note
   explicitly stating delivery is uncertain, must not be claimed
   successful, and must not be auto-retried; anything malformed/
   unrecognized fails closed (`ok=False`).
3. **Enforced the closed profile set by identity, not equality.**
   `agent/openclaw_gateway.py`'s `_call()` documented but didn't enforce
   that only `_READ_PROFILE`/`_MESSAGE_PROFILE` are valid. Since
   `_Profile` is a `NamedTuple`, a forged instance with identical field
   values would compare `==` equal to a real one -- `_call()` now checks
   `profile is _READ_PROFILE or profile is _MESSAGE_PROFILE` (Python
   identity) as the very first thing it does, before any network work.
4. **Renamed `send_raw()` to `_send_raw()`.** The old public-looking name
   was a second, undocumented side-effecting transport surface that
   bypassed `agent/openclaw_messaging.py`'s allowlist/validation policy
   if called directly. Now private, documented as sanctioned for use
   only by that module; still no registered raw-RPC tool, generic RPC
   dispatcher, or caller-selected method anywhere in the project.
5. **Corrected the write/read-scope documentation wording.** The
   original entry's "a compromised messaging credential never carries
   read-identity authority" overstated the guarantee -- this project's
   own earlier verification found `operator.write` already satisfies an
   `operator.read` check server-side (`operatorScopeSatisfied`).
   Documentation (this file, `ARCHITECTURE.md`, `HANDOFF.md`,
   `agent/openclaw_gateway.py`'s module docstring) now distinguishes
   three separate claims: credential isolation (true), Jarvis's own RPC
   confinement (true, structurally enforced), and server-side scope
   semantics (asymmetric -- don't overstate the write→read direction).
6. **Narrowed `account_id`/`thread_id` out of the public surface.** Both
   are optional in the real `SendParamsSchema`, so neither was strictly
   required for the basic single-account direct-message path this first
   release targets, and neither has its own independent channel/target-
   style allowlist yet. Removed from `send_message()`'s signature and
   the `send_message_via_openclaw` ToolSpec's `input_schema` entirely.
   `required` stays `["channel", "target", "message"]`.
7. ToolSpec risk metadata (`permission_level=3`, `side_effect=True`,
   `unattended_allowed=False`, `requires_live_confirmation=True`,
   `parallel_safe=False`) and the general autonomy model
   (`agent/autonomy.py`) were deliberately left untouched -- out of scope
   for this pass.

**Files affected**: `agent/openclaw_gateway.py`, `agent/
openclaw_messaging.py`, `agent/verification.py`, `tools/schemas/
openclaw.py`, `tests/test_openclaw_gateway.py` (+6 tests: forged-profile
rejection ×3, legitimate-profile pass-through, no-public-raw-send-
surface ×2), `tests/test_openclaw_messaging.py` (retry-proving tests
replaced with single-transmission-proving tests; account_id/thread_id
tests replaced; +2 executor-integration tests), `tests/
test_verification.py` (+10 tests for the new verifier).

**Verification**: targeted OpenClaw/verification/executor test files
plus a full `python -m unittest discover -s tests` run (see this
session's final report for exact counts). Nothing committed or pushed;
no real channel configured; no real outbound message sent.

## 2026-08-17 — OpenClaw M2: outbound text messaging bridge (implementation + tests, no real channel yet)

**What**: A new, narrow capability on top of the M1/M1.5 read-only
bridge — sending a plain-text outbound message through an
operator-configured OpenClaw channel. Implementation and tests only;
no real channel was configured, no real message was sent, and nothing
was committed or pushed as part of this pass.

Re-verified the real `send` RPC contract directly against
`openclaw@2026.7.1-2`'s compiled server source (the same stable target
as every other verification in this project): `send` is a genuine,
distinct, top-level Gateway RPC method (`server-methods-*.js`'s method
table), with its own real `SendParamsSchema` (`to`/`idempotencyKey`
required; `message`/`channel`/`accountId`/`threadId` optional and
exactly what this bridge uses; `additionalProperties: false`) --
identical between this stable release and the newer `2026.8.1-beta.2`
protocol package, no drift. Confirmed `send` requires `operator.write`
(the real core method-scope descriptor table:
`{name: "send", scope: "operator.write"}`), and that `operator.write`
already satisfies an `operator.read` check server-side
(`operator-scope-compat-*.js`'s `operatorScopeSatisfied`), so the new
messaging identity requests only `operator.write`, never both.
Confirmed `chat.send` (`ChatSendParamsSchema` requires a `sessionKey`,
part of OpenClaw's own agent/session execution surface) and
`message.action` (a broader CLI action-dispatch RPC) are genuinely
different, wider surfaces this bridge never uses -- the user's own
explicit architectural mandate ("use `send`, never `chat.send`, so an
OpenClaw agent loop never processes Jarvis's outbound message") is
directly validated by real source, not just followed on instruction.
Also confirmed the Gateway maintains a real, in-memory,
`idempotencyKey`-keyed dedupe cache (`server-request-context-*.js`'s
`context.dedupe`, a plain `Map`; 5-minute TTL confirmed via
`server-maintenance-*.js`'s cleanup loop, `now - v.ts > 3e5`) that
caches both success and failure results and replays them verbatim on a
same-key repeat within that window, against the SAME running Gateway
process. A subsequent hardening/review pass (same day, still
uncommitted) identified that this does not establish durable
exactly-once delivery: the cache is in-memory and does not survive a
Gateway process restart, so if the Gateway delivers the message and then
dies/restarts before Jarvis receives the response, a same-key resend is
no longer provably deduplicated and could send a real duplicate. As a
result, `agent/openclaw_messaging.py` does NOT automatically retry an
uncertain delivery at all -- see that pass's own entry below for the
corrected design. Verified, not assumed, per this project's standing
rule not to guess at safety-critical retry behavior -- including,
crucially, this project's own earlier guess about it.

**`agent/openclaw_gateway.py`** (connection/transport layer, extended):
added a small, closed `_Profile` NamedTuple type -- exactly two
instances exist at module scope (`_READ_PROFILE`, unchanged from M1;
`_MESSAGE_PROFILE`, new), each bundling its own device-identity
secrets, scopes, and RPC allowlist. No public API constructs a third
profile or accepts a caller-supplied scope list. `_connect_and_call`/
`_call`/`_load_or_create_device_identity`/`_clear_device_token` all now
take a required `profile` argument instead of using fixed module
constants, so every call site is explicit about which identity it's
using; `_READ_PROFILE`'s behavior is unchanged from M1 (`get_status`/
`get_node_list` pass it explicitly now). Added a new
`OpenClawUncertainDelivery` exception, raised only for `method ==
"send"` and only when the request frame was confirmed transmitted (the
`ws.send()` call itself succeeded) but no trustworthy response arrived
-- every other failure mode (auth, validation, pre-transmission
timeout/connection-refused) remains a definitive failure, unchanged
from M1's existing exception hierarchy. Added `_send_raw()` (private --
see hardening pass below), a thin pass-through to `_call("send", params,
profile=_MESSAGE_PROFILE)` that deliberately does NOT normalize/catch
errors the way `get_status()`/`get_node_list()` do, so the messaging
policy layer above it can react to each distinct failure mode.

**`agent/openclaw_messaging.py`** (new): the messaging POLICY layer --
channel/target allowlist enforcement, message validation, idempotency-
key generation (a fresh UUID per logical send, never caller-supplied),
and result normalization into three `delivery_status` shapes
(`confirmed`/`failed`/`uncertain`). Makes AT MOST ONE transmission per
logical send -- an `OpenClawUncertainDelivery` is never automatically
retried (see hardening pass below for why this changed from the retry
originally implemented in this entry).
Makes no transport/auth decisions -- never opens a socket directly.
Text-only: never emits any media/voice/poll/reaction field the real
`send` schema supports. `MAX_MESSAGE_LENGTH = 4000` (conservative,
channel-neutral; oversized input is rejected, never truncated).
Messaging `PAIRING_REQUIRED` is normalized into a result shape that's
naturally distinct from `get_status()`'s (different key set entirely),
never auto-approved, never reuses the read-only device token.

**`config/settings.py`**: `openclaw_messaging_enabled` (default
`False`, a separate opt-in from `openclaw_enabled` -- enabling the
read-only bridge does not also enable messaging), `openclaw_
allowed_channels`/`openclaw_allowed_targets` (default empty,
comma-separated exact-match allowlists -- no wildcards, no regex, no
OpenClaw-side name/directory resolution; a human-friendly alias layer,
if ever built, would need to resolve deterministically to one of these
exact configured pairs through Jarvis's own contacts layer).

**`tools/schemas/openclaw.py`**: one new tool, `send_message_via_
openclaw` (`permission_level=3`, `side_effect=True`,
`unattended_allowed=False`, `requires_live_confirmation=True`,
`parallel_safe=False` -- matching `send_email`'s existing external-
communication convention exactly). Input: exactly `channel`/`target`/
`message`, all required; never a raw RPC method, device/Gateway token,
OpenClaw session identifier, `account_id`, or `thread_id` (the latter
two are optional in the real `SendParamsSchema` but were deliberately
narrowed out of the public surface -- see hardening pass below).

**Why**: Sending a message to a real person through a real channel is
the same risk class this project already gates `send_email`/
`confirm_login` for -- the same permission level, the same live-
confirmation requirement, and (new here) the same "verify against real
primary source before trusting a retry is safe" discipline this
project has applied to every OpenClaw milestone so far. The separate
device identity exists so the read and messaging credentials are
independently revocable/scoped secrets; see the hardening pass below
for the precise, non-overstated boundary between that credential
isolation, Jarvis's own RPC confinement, and the real Gateway's
server-side scope semantics (which are asymmetric, not symmetric).

**Files affected**: `agent/openclaw_gateway.py` (profile abstraction,
`OpenClawUncertainDelivery`, `_send_raw`), `agent/openclaw_messaging.py`
(new), `config/settings.py` (3 new settings), `tools/schemas/
openclaw.py` (new tool), `tests/test_openclaw_gateway.py` (updated for
the profile abstraction; `TestSecurityAllowlist` restructured to cover
both profiles independently), `tests/test_openclaw_tool.py` (new
registration + dispatch tests), `tests/test_openclaw_messaging.py`
(new, 46 tests).

**Verification**: `npm pack openclaw@2026.7.1-2` (already downloaded
from the M1.5 pass) re-inspected for the `send`/`chat.send`/
`message.action` RPC definitions, `SendParamsSchema`, the core
method-scope descriptor table, the operator-scope-compat hierarchy
check, and the real dedupe-cache implementation. `python -m unittest
discover -s tests` → 1160 passed, 0 failed (up from 1098). Nothing
committed or pushed; no real messaging channel configured; no real
outbound message sent.

---

## 2026-08-17 — OpenClaw M1.5: real loopback Gateway smoke test, two real bugs found and fixed

**What**: Every prior OpenClaw M1 verification pass (below) was source-
reading and local-fake-server testing — this pass ran an actual OpenClaw
Gateway for the first time. Installed `openclaw@2026.7.1-2` (the exact
compatibility target, not `@latest`) into an isolated npm prefix under
`/tmp`, generated a random test token stored via `agent/secrets.py` into
the real Keychain as `OPENCLAW_GATEWAY_TOKEN`, and started a loopback-
only Gateway on a free test port.

The first attempt used OpenClaw's `--dev` flag and immediately exposed a
real isolation gap: `--dev`'s "dev workspace" ignored the
`OPENCLAW_STATE_DIR` override and wrote five template files under the
real `~/.openclaw/workspace-dev`, and the auto-loaded default plugin set
included `bonjour`, which broadcast the temporary Gateway (with the real
machine's device name) on the LAN via mDNS within seconds — even though
the WebSocket listener itself stayed correctly loopback-only
(`127.0.0.1`/`::1`, confirmed via `lsof`) the entire time. This was
caught and the process killed within ~8 seconds, before any Jarvis call
was made; the accidental `~/.openclaw` was removed with explicit user
approval. The corrected approach — no `--dev`; `OPENCLAW_STATE_DIR` set
AND `agents.defaults.workspace` explicitly patched via
`openclaw config patch`; `plugins.enabled = false` (eliminating the
whole plugin set in one setting, not just `bonjour`) — produced a
Gateway with 0 plugins loaded and no further `~/.openclaw` writes.

The real, load-bearing test: Jarvis's actual `openclaw_status` tool,
invoked through the same `tools.registry.dispatch()` path normal
execution uses (not the OpenClaw CLI's own health command), against the
live Gateway. This immediately surfaced two real bugs neither source-
reading nor the local fake server had caught: (1) the real
`ConnectParams.client` schema requires `platform`
(`protocol.schema.json`: `"required": ["id", "version", "platform",
"mode"]`) — Jarvis never sent it, and the real Gateway rejected connect
with `INVALID_REQUEST`. (2) After fixing that, the real Gateway then
rejected connect with "device signature invalid" — the real server
reconstructs the signed payload's `deviceFamily` component from
`connectParams.client.deviceFamily`
(`resolveDeviceSignaturePayloadVersion`, real compiled server source),
but Jarvis had been signing `deviceFamily="jarvis"` into the V3 payload
without ever actually including `client.deviceFamily` on the wire, so
the server's independent reconstruction used an empty string and
verification failed. Both fixed: `client.platform`
(`sys.platform`) and `client.deviceFamily` now appear in both the wire
`client` block and the signed payload.

With both fixes applied, the real smoke test succeeded end to end:
`openclaw_status` → `{"configured": true, "available": true, "protocol":
4}`; `openclaw_list_nodes` → `{"configured": true, "available": true,
"nodes": []}` (empty, expected — no real device paired). The real
Gateway auto-approved the device pairing itself (this specific
dev/loopback config's own default behavior, not anything Jarvis or this
session did) rather than returning `PAIRING_REQUIRED`, so that specific
code path wasn't exercised this time. Independently confirmed via the
OpenClaw CLI's own `devices list` command (not just Jarvis's self-
report): `Roles: operator | Scopes: operator.read` — nothing more.
Cleanly shut down afterward: process killed, port released, temporary
`/tmp` installation (~363MB) removed, and the two smoke-test-only
Keychain secrets (`OPENCLAW_GATEWAY_TOKEN`, `OPENCLAW_DEVICE_TOKEN` —
both tied to the now-deleted temporary Gateway's own state) deleted;
`OPENCLAW_DEVICE_PRIVATE_KEY` (Jarvis's persistent device identity,
independent of any specific Gateway) preserved.

**Why**: This project's standing rule to verify against primary source
extends naturally to verifying against the real running thing at least
once — source-reading and a careful local fake server, however
thorough, cannot substitute for it. Both bugs found here are the exact
same class of mistake (signing a field into the payload without also
including it on the wire), and neither the extensive prior source-
reading nor the fake test server's own signature verification (which
reconstructed payloads from expected constants rather than the actual
captured wire values) could have caught them. The fake server was
corrected to do the latter, so this class of regression is now caught
locally without needing a real Gateway every time.

**Files affected**: `agent/openclaw_gateway.py` (`_CLIENT_PLATFORM`
constant added; `client` block and V3 payload construction both fixed;
module docstring extended with the full real-Gateway findings trail),
`tests/test_openclaw_gateway.py` (2 new regression tests; the main fake-
server signature-verification path now reconstructs from captured wire
values instead of duplicate constants — the change that would have
caught both bugs originally).

**Verification**: Real `openclaw@2026.7.1-2` process, real Ed25519
device-identity handshake, real WebSocket connection, real RPC calls —
not simulated. `python -m unittest discover -s tests` → 1098 passed, 0
failed (up from 1096).

---

## 2026-08-16 — OpenClaw M1 stable-compatibility pass: auth-field bug fixed for real, device-ID confirmed

**What**: The previous re-verification pass (below) had fixed an
auth-field bug by giving Jarvis's shared `OPENCLAW_GATEWAY_TOKEN` its own
dedicated wire field, `auth.bootstrapToken` — based on the beta
`@openclaw/gateway-protocol` package's `ConnectParams.auth` schema
having a field by that name. That fix was itself wrong: a schema proves
a field CAN be sent, not what it MEANS, and the actual, authoritative
answer lives in the Gateway SERVER's own connect-auth resolution logic,
not a client-side schema. This pass re-verified against the real
current STABLE `openclaw` npm app package (`openclaw@2026.7.1-2`,
`dist-tags.latest`) rather than the separately-published client/protocol
packages — which, it turns out, have **no stable release at all**, only
an intentionally-empty `0.0.0` placeholder and `-beta.N` prereleases
(`npm view <pkg> versions`; their own CHANGELOG.md: "Publish the
reference Gateway WebSocket client for the first time"). Downloaded and
inspected the stable app's own ~87MB bundle directly (`npm pack
openclaw@2026.7.1-2`), reading its real server-side connect-auth
resolution (`resolveSharedConnectAuth`, `resolveDeviceTokenCandidate`,
`resolveConnectAuthDecisionCore`) verbatim.

Confirmed: `auth.token` (+ `auth.password`) is checked against the
Gateway's own configured SHARED secret — this is what
`OPENCLAW_GATEWAY_TOKEN` actually is, conceptually. `auth.bootstrapToken`
is verified via a wholly separate path
(`verifyBootstrapToken(deviceId, publicKey, token, role, scopes)`) meant
for a genuinely distinct device-pairing/setup credential Jarvis does not
hold — the previous pass's fix incorrectly used this field, now
corrected. `auth.deviceToken` is checked via a third, separate path
(`verifyDeviceToken`), and its use (rather than reusing `auth.token`)
for a stored device credential is not just a style preference but
required: a rejection there reports `AUTH_DEVICE_TOKEN_MISMATCH`
(`candidateSource === "explicit-device-token"`), while reusing
`auth.token` for a stale device token would instead surface as
`AUTH_TOKEN_MISMATCH`, silently breaking `_connect_and_call`'s stale-
token clear-and-retry logic. Fixed: the shared credential now always
goes under `auth.token`; a stored device token now always goes under
`auth.deviceToken`; `auth.bootstrapToken` is never populated.

The same pass re-confirmed `signedAt` is safe as-is despite a real
stable/beta implementation difference: the stable app's own client uses
plain `Date.now()` unconditionally (no `challengeTs` concept exists
anywhere in its bundle — grepped the entire extracted `dist/`, zero
matches), while the beta client prefers the challenge's own `ts`. Both
are compatible with both server versions because the Gateway SERVER's
actual freshness check (`message-handler`'s real compiled source) is
`Math.abs(Date.now() - device.signedAt) > DEVICE_SIGNATURE_SKEW_MS`
(120 seconds) — a wall-clock skew check against the server's OWN clock,
never an exact-match comparison against the challenge's `ts`. No change
made.

Also confirmed, and no longer an assumption: device-ID derivation. The
stable bundle contains a literal `deriveDeviceIdFromPublicKey` function
(`src/infra/device-identity.ts`) doing exactly `SHA-256(raw 32-byte
Ed25519 public key).hexdigest()`, and the Gateway server independently
re-derives and compares this value against the client-claimed
`device.id` on every connect (`message-handler`'s real compiled source)
— an exact match to this bridge's implementation. The "unverified
assumption" language has been removed from the module docstring,
`_load_or_create_device_identity`'s docstring, and project docs;
`DEVICE_AUTH_DEVICE_ID_MISMATCH` handling is kept as defense-in-depth,
not because of remaining doubt.

**Why**: A schema listing a field is necessary but not sufficient
evidence for what that field means on the wire — the server's own
interpretation is authoritative, and only inspecting it directly (not
just the client package that merely proves a field exists) catches a
bug like this. This is the same "verify against the actual primary
source, not an adjacent one" discipline this project has applied
repeatedly this session (a GitHub issue, then a beta-only package, now
a client-side schema) — each layer looked authoritative until checked
against something closer to the actual, enforced behavior.

**Files affected**: `agent/openclaw_gateway.py` (`_connect_and_call`'s
`auth_field` logic corrected; module docstring rewritten with the full
stable-vs-beta comparison; `_load_or_create_device_identity`'s docstring
updated to CONFIRMED), `tests/test_openclaw_gateway.py` (3 net new
tests — 57 total, up from 54: correct wire field for the shared token,
correct wire field for a stored device token, a payload/wire-value
consistency check, and a known-answer test for the device-ID algorithm;
existing device-token tests' fake server updated for the corrected field
semantics).

**Verification**: `npm pack openclaw@2026.7.1-2` used to download and
extract the actual current stable app package (~87MB unpacked) directly;
`dist/src-DZzKBMa7.js` (`GatewayClient.assembleConnectParams`,
`selectConnectAuth`, `buildDeviceConnectParams`) and
`dist/message-handler-CzwI6JjW.js` (`resolveSharedConnectAuth`,
`resolveDeviceTokenCandidate`, `resolveConnectAuthDecisionCore`, the
device-ID/signature/nonce/skew verification block) read directly;
`dist/device-identity-UW4cZXf5.js` (`deriveDeviceIdFromPublicKey`,
`derivePublicKeyRaw`) read directly for the device-ID confirmation.
`python -m unittest discover -s tests` → 1096 passed, 0 failed (up from
1093). All of this remains protocol-level verification against a local
fake Gateway server (`tests/test_openclaw_gateway.py`'s
`websockets.sync.server`-based fixture, real Ed25519 signature
verification) plus direct primary-source reading of the real published
packages — not a live test against a real, installed OpenClaw Gateway
process, which does not exist on this machine.

---

## 2026-08-16 — OpenClaw M1 re-verification: signedAt confirmed correct, auth-field bug fixed

**What**: A claim surfaced that `device.signedAt` must always be the
client's current wall-clock time and must never be copied from the
`connect.challenge` event's own `ts`. Rather than apply it on say-so,
it was checked directly against a freshly re-pulled npm release
(`@openclaw/gateway-client@2026.8.1-beta.2`, newer than the release
inspected during the auth-correction pass below) — both the real
CLI/backend `GatewayClient.buildConnectPlan` and the real browser
`GatewayBrowserDeviceAuthLifecycle.buildPlan` implementations compute
`signedAtMs = challengeTs ?? Date.now()`: they PREFER the challenge's
own timestamp, falling back to wall-clock time only when the challenge
omits one (the CLI/backend client actually throws if a device identity
is configured and the challenge has no `ts` at all). This is the
opposite of the claim, and exactly what `agent/openclaw_gateway.py`
already did — **no change was made to `signedAt` handling.** Two
regression tests were added instead
(`test_signed_at_uses_the_connect_challenge_timestamp_not_wall_clock`,
`test_signed_at_falls_back_to_wall_clock_when_challenge_omits_timestamp`)
so a future session can't "fix" this into the wrong behavior.

The same re-verification pass, while re-checking auth-token/device-token
selection against the same real source, found a genuine, separate,
previously-unverified bug: the real `ConnectParams.auth` object
(`protocol.schema.json`, `additionalProperties: false`) has distinct
`token`/`bootstrapToken`/`deviceToken`/`password`/
`approvalRuntimeToken`/`agentRuntimeIdentityToken` fields, and the real
client's `buildGatewayConnectAuth` places a credential under the field
naming which kind it is — never a generic `token` field for a bootstrap
or device credential. `_connect_and_call` previously always sent
`{"token": auth_token}` regardless of which credential it held; this
likely would have caused real auth failures (or at minimum, the wrong
`AUTH_*` error code) against an actual Gateway. Fixed: it now sends
`{"deviceToken": ...}` or `{"bootstrapToken": ...}` depending on which
credential is in use.

**Why**: This project's standing rule — verify security-critical
protocol details against primary source rather than a single claim —
applies symmetrically. It was already used to catch a stale GitHub
issue during the previous pass; here it was used to check an incoming
correction request itself, and it caught the request being wrong while
also surfacing a real defect the request didn't mention.

**Files affected**: `agent/openclaw_gateway.py` (`_connect_and_call`'s
`auth` block, plus an expanded module docstring section),
`tests/test_openclaw_gateway.py` (3 new tests: 54 total, up from 51;
the existing device-token fake-server tests were also updated to check
the correct, now-distinct `deviceToken`/`bootstrapToken` fields instead
of one shared `token` key). No other OpenClaw M1 files changed.

**Verification**: `npm pack`/`npm view` used again to pull the newer
`2026.8.1-beta.2` release directly (rather than relying on the earlier
inspection); `dist/index.mjs` (`GatewayClient.buildConnectPlan`,
`assembleConnectParams`, `buildDeviceConnectParams`) and
`dist/session-subscriptions-*.mjs` (`GatewayBrowserDeviceAuthLifecycle
.buildPlan`, `selectGatewayConnectAuth`, `buildGatewayConnectAuth`)
read directly; `protocol.schema.json`'s `ConnectParams.auth`/`device`
definitions read directly for field-level confirmation.
`python -m unittest discover -s tests` → 1093 passed, 0 failed (up from
1090).

---

## 2026-08-16 — OpenClaw M1 correction: real Ed25519 device-identity auth

**What**: The initial OpenClaw M1 pass (previous entry, below) shipped a
shared-token-only auth design, explicitly flagged there as "a documented
assumption, not verified against a real Gateway." A follow-up review
disproved that assumption against current official OpenClaw behavior:
normal third-party operator clients are expected to hold a persistent
Ed25519 device identity and complete a challenge-signed handshake, not
just present a shared secret. Rather than trust either docs.openclaw.ai
(which doesn't cover this flow at all) or a GitHub issue claiming to
describe it (issue #17571 — whose own payload-format claim turned out to
cite a stale "v1" scheme), this was verified against the actual
published `@openclaw/gateway-client` and `@openclaw/gateway-protocol`
npm packages: downloaded via `npm pack` and inspected directly (real
compiled `.mjs` source, not a paraphrase).

Confirmed verbatim from that real source: the exact V3 device-auth
payload format (`buildDeviceAuthPayloadV3` in `device-auth.ts`); that a
client MUST wait for a `connect.challenge` event before sending
anything (from `protocol-client.handshake.test.ts`'s real tests); the
complete real `ConnectErrorDetailCodes` enum (`PAIRING_REQUIRED`,
`AUTH_SCOPE_MISMATCH`, `AUTH_DEVICE_TOKEN_MISMATCH`, every
`DEVICE_AUTH_*` code, etc., from `connect-error-details.mjs`); and the
real, closed `GATEWAY_CLIENT_IDS`/`GATEWAY_CLIENT_MODES` enums (`"cli"`
chosen as the closest legitimate, non-reserved identity — `"backend"`/
`"gateway-client"` are OpenClaw's own reserved internal identity and are
never used, confirmed against `client-info.mjs`). Also confirmed: the
actual Ed25519 signing/key-generation implementation is genuinely NOT
part of either published package — both expose it only as an injected
dependency, stubbed as a no-op in the default export — meaning it lives
inside the main `openclaw` application's own unpublished source. One
detail remains genuinely unverified despite this effort: the exact
device-ID hash algorithm. This implementation uses SHA-256 of the raw
32-byte Ed25519 public key (the one candidate found, from the same
GitHub issue whose other claim was already proven stale — treated with
real skepticism, not as confirmed) — deliberately low-risk if wrong,
since the real Gateway has a dedicated error code for exactly that case
(`DEVICE_AUTH_DEVICE_ID_MISMATCH`), handled as a clean `OpenClawAuthError`,
never a crash.

`agent/openclaw_gateway.py` was substantially rewritten: persistent
Ed25519 device identity (`OPENCLAW_DEVICE_PRIVATE_KEY`, PEM/PKCS8, via
`agent/secrets.py`, generated once and reused); the real V3 payload
builder and Ed25519 signing; a `connect.challenge`-first handshake
(previously the bridge sent `connect` immediately, which was never
correct); post-`hello-ok` verification that `operator.read` was
actually granted, failing closed if not (`OpenClawScopeError`, new); a
new `OpenClawPairingRequired` error (never auto-approved — a human runs
`openclaw devices approve <requestId>`); device-token persistence
(`OPENCLAW_DEVICE_TOKEN`) and reuse, with exactly one bounded fallback-
to-bootstrap-token retry on `AUTH_DEVICE_TOKEN_MISMATCH`, mirroring the
real client's own verified "cleared stale device-auth token" behavior —
never looping further. `OPENCLAW_GATEWAY_TOKEN` remains the bootstrap/
shared credential, now distinct in role from the paired-device token.
New dependency: `cryptography==50.0.0` (Ed25519 key generation/signing)
— verified to install and work correctly on Intel macOS + Python 3.14
(compiles from source; no pre-built wheel existed yet for this exact
platform/interpreter combination at verification time). The RPC
allowlist, scope ceiling (`operator.read` only, never requested above
that), and the two read-only tools
(`openclaw_status`/`openclaw_list_nodes`) are unchanged from the
original M1 pass.

**Why**: Shipping an auth design already flagged as unverified, when the
real requirement was findable through enough primary-source effort,
would have meant M1 likely failing against any real Gateway the moment
it was actually used — the whole point of building this milestone with
a real, working foundation first (rather than the messaging/device-
capability milestones that depend on it) is undermined if that
foundation doesn't actually authenticate correctly.

**Key decisions**: Extraordinary verification effort (downloading and
directly inspecting two real published npm packages, not just reading
docs/issues) was chosen over guessing from a community issue's claims,
specifically because that issue's own technical details were already
caught being partly wrong (payload version) during this same
verification pass — a clear signal not to trust it uncritically for the
parts that couldn't be independently checked either. The one residual
unverified detail (device-ID hash) is documented prominently in three
places (module docstring, `ARCHITECTURE.md`, this entry) rather than
either hidden or used as a reason to not ship the rest of a now-far-more-
correct implementation.

**Files affected**: `agent/openclaw_gateway.py` (substantially rewritten),
`requirements.txt` (added `cryptography==50.0.0`),
`tests/test_openclaw_gateway.py` (substantially rewritten — real Ed25519
signature verification in the fake test server, not a "non-empty string"
stub check). `tools/schemas/openclaw.py` and `tests/test_openclaw_tool.py`
unchanged — the tool-level interface and its tests were never coupled to
the auth mechanism underneath.

**Tests**: 15 new (1090 total, up from 1075), full suite passing, zero
live/paid API calls, no real OpenClaw installation used. The fake
Gateway test server now performs genuine cryptographic signature
verification against the public key the real client code actually
sends — reconstructing the exact payload with the module's own real
payload-builder function and calling `Ed25519PublicKey.verify()`, not
asserting a signature string is merely non-empty.

---

## 2026-08-16 — OpenClaw M0 (research audit) + M1 (read-only Gateway bridge)

**What**: A separate, intermediate initiative (not a Phase 9 milestone),
sequenced between Phase 9 Milestone 3 (complete) and Milestone 4 (not
started). OpenClaw (github.com/openclaw/openclaw, docs.openclaw.ai) is a
real, independently-developed, MIT-licensed open-source personal-AI-
assistant/messaging-gateway project — not a Jarvis subsystem.

**M0 (research/architecture audit, no code changes)**: researched
OpenClaw against its current official documentation. Findings that
directly shaped M1's design: Intel x86_64 macOS is supported (native
binaries, this development Mac's own architecture); the Gateway's
documented local default is an *authenticated loopback WebSocket*
(`ws://127.0.0.1:18789`), not TLS; current stable release
`openclaw 2026.7.1-2`, current protocol version 4; a 7-scope operator
authorization model where `health`/`status`/`node.list` need only the
minimal `operator.read` scope while `node.invoke`/`chat.send` need
`operator.write`; OpenClaw plugins execute with full host privileges, no
sandboxing ("treat plugin installs like running code," per OpenClaw's
own docs) — the reason M1 depends on zero third-party OpenClaw plugins,
core Gateway RPCs only.

**M1 (read-only Gateway bridge, real implementation)**:
`agent/openclaw_gateway.py` (new) — a narrow, optional bridge. Makes no
Jarvis policy decisions of its own: no tool-permission logic, no model
routing, no autonomy/confirmation checks, no OpenClaw agent/session use.
One-shot connections via `websockets.sync.client` (the synchronous API,
not async — fits Jarvis's existing synchronous tool-execution
architecture with zero asyncio adapter needed). A fixed, hard-coded RPC
allowlist (`{"health", "status", "node.list"}` only) rejects every other
method, including `node.invoke`, `chat.send`, `config.*`, `exec.*`,
`approval.*`, `plugin.*`, and any unknown method string — there is no
`openclaw_raw_rpc` and no path to one. Five normalized error types
(`OpenClawUnavailable`/`OpenClawAuthError`/`OpenClawProtocolError`/
`OpenClawTimeout`/`OpenClawUnsupportedCapability`) so a tool call never
surfaces a raw exception or a stack trace. Node-list results are
strictly minimized: explicit field-by-field whitelisting
(id/display_name/platform/connected/capability-*names*-only) — auth
material, signatures, network identifiers, and full plugin/skill
descriptor objects are structurally excluded, not merely omitted by
convention.

Two new tools (`tools/schemas/openclaw.py`): `openclaw_status`,
`openclaw_list_nodes` — both `permission_level=0`, read-only,
`unattended_allowed=True`, `parallel_safe=True` (matching
`get_system_status`'s existing precedent for this exact tool shape),
neither taking any input parameter, so neither can ever be handed a
caller-chosen RPC method. Both flow through the ordinary
`tools.registry.dispatch()` path — no second dispatch mechanism.

`config/settings.py` gained `openclaw_enabled` (default `False`),
`openclaw_gateway_url` (default `ws://127.0.0.1:18789`),
`openclaw_timeout_seconds` (default `10.0`, bounding the entire
connect+handshake+RPC+close lifecycle of one call). New secret:
`OPENCLAW_GATEWAY_TOKEN`, via the existing `agent/secrets.py` Keychain-
first store — no new secrets mechanism. New dependency:
`websockets==16.1.1`, pinned explicitly (was previously only an
incidental transitive dependency of `streamlit`, not something Jarvis's
own code relied on directly).

**Why**: The next planned OpenClaw increment (M2, messaging) and future
ones (device capabilities) need a working, tested connection/auth/
protocol-negotiation foundation first — building that narrowly and
read-only, before any capability with a real side effect, matches this
project's standing "smallest clean mechanism first" approach (the same
reasoning Phase 9 Milestone 3 already applied to bounded parallel
delegation).

**Key decisions**: `websockets.sync.client`, not the async API — avoids
introducing any asyncio event-loop management into Jarvis's synchronous
tool-execution model. One-shot connections, no persistent connection
manager — correctness/isolation over reuse, appropriate for two
low-volume read-only tools; revisit only if call volume grows enough to
justify the added complexity. The connect handshake uses OpenClaw's
simpler shared-token auth path (`auth.token`), not the full
cryptographic device-pairing flow (nonce challenge + signature) — a
documented assumption suited to a lightweight, non-paired operator
client, not independently verified against a real Gateway in this pass
(M1's automated tests use a local fake WebSocket server, per explicit
instruction not to require or install a real OpenClaw installation for
this milestone).

**Files affected**: `agent/openclaw_gateway.py` (new),
`tools/schemas/openclaw.py` (new), `tools/schemas/__init__.py`,
`config/settings.py`, `requirements.txt`, plus new tests
`tests/test_openclaw_gateway.py`, `tests/test_openclaw_tool.py`.

**Tests**: 51 new (1075 total, up from 1024 before this pass), full
suite passing, zero live/paid API calls, no real OpenClaw installation
used or required. Includes one real-boundary test class (a genuine local
WebSocket server on an ephemeral loopback port, not a mocked client) —
the same "at least one real-boundary test" rigor this project's other
cross-process/cross-boundary fixes already established (real subprocess
cancellation test, real cross-process audit-log write test).

---

## 2026-08-16 — Phase 9 Milestone 3: bounded parallel coworker delegation + verification

**What**: Added `agent/agents/manager.py`'s `execute_agents_parallel()` —
Jarvis can now hand 2+ genuinely independent coworker subtasks to a
bounded worker pool instead of only ever delegating one task at a time.
Hard-capped at `settings.max_parallel_agents = 3`: a batch larger than
that is rejected outright (zero subprocesses spawned), never silently
truncated. Every subtask still runs through the existing, unmodified
`execute_agent()` — same subprocess isolation, same per-task depth/
registered/enabled/cancellation checks — so this adds no second dispatch
path, only a bounded caller on top of the one that already existed. The
only new entry point is a new tool, `delegate_parallel_tasks`
(deliberately **not** `parallel_safe`, so the model can't multiply
concurrent subprocesses past the ceiling by calling it twice in one
turn); the existing single-task `consult_coworker_agent` tool is
completely untouched. Subtasks default to `required=True`; a failed
`required=False` subtask degrades the batch to `PARTIAL` instead of
`FAILED`. A failed (non-cancelled) subtask gets one bounded retry
(`settings.max_agent_batch_retries = 1`). Results come back as a
structured `AgentBatchResult` (new dataclasses: `AgentTaskRequest`,
`AgentBatchItem`, `BatchStatus`).

`agent/verification.py` gained `verify_agent_result()` — evaluates
whether a coworker's result actually holds up (cancellation, explicit
failure, an agent-reported `verification_status` of `"failed"` — e.g.
QAAgent's own test-suite check — all override a nominal `success=True`),
plus a bounded, objective source-evidence heuristic for ResearchAgent
specifically (does the answer mention a URL/source at all). Deliberately
not extended to FILES/BROWSER-shaped checks — no current coworker
produces that shape of result yet; documented as future work, not built
speculatively.

`execute_agent()`'s subprocess launch was rebuilt on `Popen` (a new
`_run_agent_subprocess()` helper) instead of `subprocess.run`,
specifically so a parent request cancelled mid-flight can terminate an
already-running coworker subprocess, not just refuse to start a new one
— graceful `SIGTERM` first, a bounded ~3s grace period, `SIGKILL` only
if it doesn't exit, every exit path reaping the child so no orphan can
result. The existing timeout guarantee (`SIGKILL` on
`settings.agent_timeout_seconds`) is unchanged, not weakened.

`agent/research_agent.py` — the only coworker that directly calls a
model — now routes through `agent/task_classifier.py`'s `classify()` and
`agent/model_router.py`'s `build_fallback_chain()`, the same M2
primitives the outer request uses, instead of always calling Anthropic's
default model directly. It inherits capability/health/budget filtering,
cost-aware tiering, and cross-provider fallback; each call records usage
with `agent="research"`, the real `task_type`, and `fallback_position`
attribution, and participates in `agent/provider_health.py`'s failure-
cooldown tracking like any other caller.

`agent/audit.py`'s `log_action` gained an `fcntl.flock` around its
append write — parallel coworker subprocesses can now genuinely write to
the shared action log at the same instant, a scenario that didn't really
exist before this milestone (only one coworker subprocess ever ran at a
time previously).

`agent/execution_state.py` gained `active_agents`/`completed_agents`/
`failed_agents`/`parallel_batch_size`/`verification_status` (batch-level,
additive) alongside the pre-existing singular `active_agent`/
`agent_task`/`agent_status`/`agents_used` fields, which a batch leaves
untouched rather than overloading with a synthetic value. New
observability events: `agent_batch_started`, `agent_batch_completed`,
`agent_batch_failed`, `agent_batch_rejected`, `agent_retry_started`.

**Why**: `ROADMAP.md`'s "Next" item — the goal was making Jarvis better
at decomposing genuinely-independent work, delegating it concurrently,
verifying the combined result, and staying bounded in cost/concurrency/
depth/retries throughout, without weakening any existing safety
invariant (`MAX_AGENT_DEPTH = 1`, subprocess isolation, the timeout
guarantee, tool-registry-gated permissions). A pre-commit review of the
original implementation found two real gaps against that goal — coworker
inference bypassing M2's cost-aware routing entirely, and cancellation
only preventing new work rather than stopping work already in flight —
and both were closed before this milestone was considered finished
rather than shipped as known limitations.

**Key decisions**: Deciding *which* subtasks are independent enough to
batch stays the model's judgment (constrained by
`delegate_parallel_tasks`'s own tool description) — same precedent
`agent/planner.py`'s existing step decomposition already set. Deciding
*whether* it's safe and *how many* can run — the concurrency ceiling,
the depth guard, the budget pre-flight gate — stays fully code-enforced,
never model-decided, the same separation this project already applies to
permissions and routing. The cost pre-flight check
(`agent/provider_budget.py`'s existing `global_budget_status()`) is a
single check before launching a batch, not a spend-reservation engine —
refuses to START a batch that would obviously worsen an already-over-
budget day, but doesn't reserve budget for calls already in flight, the
same limitation M2's own per-request routing already has.
`_call_perplexity_agent`/`_client_for_provider` were duplicated locally
inside `agent/research_agent.py` rather than imported from
`agent/executor.py`, specifically to avoid a real import cycle
(`executor → agent.agents → agent.agents.research → research_agent →
executor`) — a documented, intentional technical-debt note, not an
oversight.

**Files affected**: `agent/agents/manager.py`, `agent/agents/models.py`,
`agent/audit.py`, `agent/execution_state.py`, `agent/research_agent.py`,
`agent/verification.py`, `config/settings.py`, `tools/schemas/agents.py`,
plus new/updated tests across `tests/test_agents_batch.py` (new),
`tests/test_agents_tool_batch.py` (new), `tests/test_audit.py` (new),
`tests/test_research_agent.py` (new), `tests/test_agents_manager.py`,
`tests/test_agents_base_and_models.py`, `tests/test_execution_state.py`,
`tests/test_verification.py`, `tests/test_usage_limits_integration.py`.

**Tests**: 96 new (1024 total, up from 928 before this milestone), full
suite passing, zero live/paid API calls. Mid-flight subprocess
cancellation is verified against a real, separate OS process (not a
mocked `Popen`) confirming no orphan remains
(`tests/test_agents_manager.py`'s `TestRunAgentSubprocessRealProcess`) —
the same "at least one real-boundary test" rigor this project's other
cross-process fixes already established (`tests/test_browser_lock.py`,
`tests/test_audit.py`'s own cross-process write test).

---

## 2026-08-15 — Phase 9 Milestone 2: task-aware, multi-provider model routing

**What**: Replaced the original static Claude-primary/OpenAI-fallback
routing with task-aware, multi-provider routing. `agent/task_classifier.py`
(new) deterministically classifies each request (task type, vision/
current-web/large-context/tool needs, latency/quality/cost priority) with
no model call — keyword/pattern matching only, consistent with this
project's "never let a model decide a routing outcome" rule.
`agent/model_router.py`'s new `build_fallback_chain()` filters candidate
providers through a fixed, load-bearing order — capability, then
configured/health (`agent/provider_health.py`'s new `xai_configured()`/
`perplexity_configured()` plus an in-memory per-provider failure-cooldown
tracker), then daily budget (`agent/provider_budget.py`, new — same-day
per-provider and global spend ceilings, reusing `agent/usage.py`'s
existing `cost_since()`/`cost_today()` aggregation) — before ranking by
cost/quality and returning an ordered candidate list.
`agent/executor.py`'s `execute_task_stream` was generalized from a
hardcoded two-tier cascade into a real loop over however many candidates
the router returns, trying each in order and falling through to the next
on failure (never calling multiple providers simultaneously). Two new
working (not scaffolded) optional providers: xAI (OpenAI-API-compatible,
`agent/chat.py`'s new `xai_client`) and Perplexity, via its Agent API
rather than Chat Completions (a narrow, dedicated single-shot call path,
`agent/executor.py`'s `_call_perplexity_agent`/`_run_perplexity_agent_loop`
— Perplexity is never handed Jarvis's tool registry, so it can't become a
second orchestrator).

Immediately before this milestone's commit, a dedicated pre-commit
currency/cost review re-checked every router provider default against
current official documentation (rather than trusting names chosen
earlier in the same implementation) and found real, dated problems:
Perplexity's Sonar Chat Completions endpoint (M2's original integration
target) is officially deprecated with support ending 2026-09-27, so the
integration was migrated to the Agent API before ever shipping; OpenAI's
`gpt-5` default was stale, superseded by the GPT-5.6 family, and replaced
with three tiers (`openai_economy_model` = `gpt-5.6-luna`, `fallback_model`
= `gpt-5.6-terra`, `openai_quality_model` = `gpt-5.6-sol`); xAI's `grok-4`
placeholder wasn't a real model ID and was replaced with
`xai_economy_model` (`grok-4.3`) and `xai_quality_model` (`grok-4.6`);
`agent/usage.py`'s `_PRICING` table had genuinely stale, billing-relevant
entries (Sonnet 5 priced at Sonnet 4.6's old rate, Haiku 4.5 at Haiku
3.5's) and was rebuilt against each provider's official rate card. A
second, narrower follow-up pass then caught `vision_model`
(`tools/vision.py`'s separate, hardcoded, non-routed OpenAI vision
assignment) still on the same stale `gpt-5` default the router tiers had
already been fixed away from, and updated it to `gpt-5.6-terra` — the
balanced GPT-5.6 tier, confirmed image-input-capable and already
confirmed callable via `chat.completions.create()`. `transcription_model`/
`tts_model` (a different OpenAI product line entirely) were not part of
either review pass and remain untouched, hardcoded per-call-site
assignments outside this milestone's scope.

**Why**: `agent/model_router.py`'s `select()` had reserved an unused
`context` parameter since Phase 2 specifically for this. Real API cost
has been a standing, previously-painful concern for this project (Phase 8
exists because of a real, confusing cost overrun) — the pre-commit
currency review existed specifically so this milestone didn't ship any
stale/mispriced model default, checked against official docs rather than
assumption, with zero live/paid API calls made anywhere in the process
(every provider call in every test is mocked at the client/`httpx.post`
boundary).

**Key decisions**: Filter order (capability → configured/healthy →
budget → cost/quality ranking) is fixed and never reordered, so routing
can't discover mid-call that the cheapest candidate simply can't do the
task. Falls back to the original static `[anthropic, openai]` chain if
`task_aware_routing_enabled` is off or every candidate gets filtered out
— never an empty list. The original Phase 1 interface
(`primary_choice()`/`fallback_choice()`/`select()`) is untouched.
Perplexity's `perplexity_model` setting holds an Agent API *preset*
string (`"low"`), not a flat-rate model ID — the one settings field that
differs in kind from every sibling `*_model` field, documented inline as
such. `agent/chat.py`'s `perplexity_client` is kept (not removed as dead
code) despite the live call bypassing it entirely, because it still
supplies `agent/provider_health.py`'s `check_providers()` diagnostic
`initialized` field, kept structurally uniform with the other three
providers' identical role there.

**Files affected**: `agent/task_classifier.py` (new),
`agent/provider_budget.py` (new), `agent/provider_health.py`,
`agent/model_router.py`, `agent/executor.py`, `agent/chat.py`,
`agent/usage.py`, `config/settings.py`, `tests/test_task_classifier.py`
(new), `tests/test_provider_budget.py` (new), `tests/test_chat_providers.py`
(new), `tests/test_executor_multi_provider_fallback.py` (new),
`tests/test_model_router.py`, `tests/test_provider_health.py`,
`tests/test_settings.py`, `tests/test_usage.py`,
`tests/test_phase6_security.py`.

**Tests**: 96 new (928 total, up from 832), full suite passing, no
regressions, zero live/paid API calls (confirmed by code inspection and
by the suite's ~8-second total runtime). Verified via a real
(unmocked-router) end-to-end test that behavior with only Anthropic/
OpenAI configured — this project's original, and still the default,
state — is byte-for-byte unchanged from before this milestone.

---

## 2026-08-15 (before that) — Phase 9 Milestone 1: Playwright browser-profile ownership hardening

**What**: Fixed a previously-documented lifecycle risk — Streamlit, the
menu-bar app, and a scheduled task could each independently try to launch
Chrome against the same shared, persistent on-disk profile directory
(`tools/browser.py`'s `PROFILE_DIR`), with nothing on Jarvis's side
coordinating across processes; only Chrome's own internal profile lock
would have arbitrated, with no clean handling on Jarvis's side. Added
`agent/browser_lock.py` (new): a non-blocking, kernel-managed
`fcntl.flock(LOCK_EX | LOCK_NB)` on a dedicated lock file
(`~/Library/Application Support/CampusPilot/chrome-profile.lock`) — the
same primitive `agent/scheduler_lock.py` already uses for the analogous
scheduler race, adapted for a different lifetime: acquired once when the
browser context is first created and held open for as long as that
context is alive, rather than reacquired every poll tick.
`tools/browser.py` now acquires the lock before launching a persistent
context and releases it on shutdown/dead-context discard; a process that
loses the race gets a new, clean `BrowserBusyError` ("Another Jarvis
process is already using the browser. Try again in a moment.") instead
of racing Chrome's own profile lock or getting an opaque Playwright/
Chrome error.

**Why**: A real, previously-documented risk (`ROADMAP.md`'s "Known
lifecycle risks") with no coordination mechanism at all before this.

**Key decisions**: An additional in-process `threading.Lock` (the same
pattern `agent/voice_state.py`'s `_busy_lock` already uses) closes a
narrower race the scheduler lock never had to worry about — two threads
in the same process both finding no context yet and both racing to
open+flock the lock file before either one's ownership is recorded.
Kernel-managed release either way — a crash or `SIGKILL` releases the
lock automatically, no stale-lock detection needed, the same reasoning
already applied to `agent/scheduler_lock.py`.

**Files affected**: `agent/browser_lock.py` (new), `tools/browser.py`,
`tools/autofill.py`, `tests/test_browser_lock.py` (new),
`tests/test_browser.py` (new).

**Tests**: 25 new, full suite passing. Cross-process contention proved
with a real subprocess and a hard `SIGKILL`, the same rigor
`agent/scheduler_lock.py`'s own test suite established for the analogous
case.

**Note**: Closes the "Playwright profile contention" item previously
listed under `ROADMAP.md`'s "Next" section.

---

## 2026-08-15 — Duplicate-scheduler fix (cross-process lock)

**What**: Fixed the duplicate-scheduler lifecycle risk documented since
the Phase 2 lifecycle review: `agent/scheduler_daemon.py` (standalone,
manually started) and `ui/menu_bar.py`'s built-in poller (a background
thread, running whenever the menu-bar app is — effectively always, via
its LaunchAgent) each independently poll the same `scheduled_tasks.json`
and used to both execute every due task if run at the same time.

Added `agent/scheduler_lock.py`: a non-blocking, kernel-managed
`fcntl.flock(LOCK_EX | LOCK_NB)` on a dedicated lock file
(`~/Library/Application Support/CampusPilot/scheduler.lock`), re-attempted
on every poll tick. Whichever process wins the lock for that tick may
process due tasks; the loser skips the tick entirely (no execution, no
`mark_run`, no UI/voice-state interaction) and logs a
`scheduler_lock_deferred` diagnostic. `fcntl.flock` ties the lock to the
open file description, so it's released automatically the instant the
holding process exits or is killed — unlike `ui/menu_bar.py`'s own
PID-file single-instance lock, no stale-lock detection logic was needed.

**Why**: User-approved after a dedicated investigation phase (traced
task registration, every scheduler entry point, how each is started,
confirmed both pollers are an intentional design for different
deployment modes — not an oversight — and evaluated three fix options:
this cross-process lock, a static config-driven ownership flag, and
consolidating into one canonical process). The lock was chosen over the
other two for the best failure/recovery behavior (self-heals within one
poll interval if the lock-holder dies, vs. the static-flag approach's
silent-starvation risk) and the smallest, most conservative change (vs.
consolidating the two pollers into one, which would have contradicted
this project's "don't rewrite a working subsystem to add one feature"
rule and removed `scheduler_daemon.py`'s documented, deliberate role as
a headless fallback).

**Key decisions**:
- The lock lives in its own new module and its own lock file, not
  extended onto `agent/scheduled_tasks.py`'s existing `TASKS_FILE.lock`
  — that lock is a *blocking* shared/exclusive lock for safe JSON
  read/modify/write, a different concept from a *non-blocking* per-tick
  ownership-arbitration lock; combining the two purposes on one file
  risked subtle deadlocks.
- Neither poller's own due-task/mark_run logic was touched —
  `agent/scheduler_daemon.py`'s `_run_due_tasks()` and
  `ui/menu_bar.py`'s `_run_due_scheduled_tasks()` are byte-for-byte
  unchanged, including their pre-existing behavioral difference (the
  daemon marks a task run even after an execution error; the menu-bar
  poller does not, so it retries next tick instead). The lock only wraps
  each poller's outer per-tick call site (`scheduler_daemon.py`'s new
  `_poll_once()`; `ui/menu_bar.py`'s new `_scheduler_tick()`).

**Files affected**: `agent/scheduler_lock.py` (new),
`agent/scheduler_daemon.py`, `ui/menu_bar.py`,
`tests/test_scheduler_lock.py` (new), `tests/test_scheduler_daemon.py`
(new), `tests/test_phase6_security.py` (new `TestMenuBarSchedulerLock`
class).

**Tests**: 22 new (775 total, up from 753), full suite passing, no
regressions. Cross-process contention specifically proved with a real
subprocess (not threads or same-process file handles standing in for
it): `tests/test_scheduler_lock.py`'s `TestCrossProcessContention` spawns
an actual child OS process that acquires the lock, confirms the parent
process's own attempt fails while the child holds it, then `SIGKILL`s
the child (not a graceful exit) and confirms the parent can immediately
acquire it afterward — proving the release is genuinely kernel-managed,
not dependent on any application-level cleanup code running. One test in
`tests/test_phase6_security.py` binds the real `_run_due_scheduled_tasks`
method to a mock instance via `types.MethodType` specifically to prove
the actual production call path (`_scheduler_loop` ->
`_scheduler_tick` -> `_run_due_scheduled_tasks`) executes a due task end
to end, after an initial version of these tests was found (during this
same session) to be silently vacuous — a bare `MagicMock` standing in
for `self` turns `self._run_due_scheduled_tasks()` into a no-op stub
call, not a real invocation.

**Note**: these changes are staged but intentionally **not committed**
— per this project's "only commit when explicitly asked" convention.

---

## 2026-08-15 (later) — HANDOFF sync fix, repository cleanup

**What**: Two pieces of work at the start of a new session, done in
order:
1. Corrected `HANDOFF.md`, which still described the menu-bar cost
   readout as uncommitted and "awaiting approval" even though it had
   already landed as `1a15ac0` in the prior session — the working tree
   was clean and `git log` showed the commit, but the prose hadn't been
   reconciled after the commit happened. Fixed throughout (status
   summary, file list, outstanding-work list, recommended next steps).
2. Repository cleanup: removed three files/directories after verifying
   each was genuinely unreferenced (code, launch agents, scripts, tests,
   docs) rather than deleting on the assumption that "old-looking" meant
   safe:
   - Root `memory.json` — `database/memory.py` hardcodes an absolute
     `~/Library/Application Support/CampusPilot/`-based path; nothing
     references the relative repo-root filename. Git-tracked, removed
     via `git rm`.
   - `CampusPilotAgent.app.old-handbuilt/` — a pre-py2app, hand-built
     `.app` bundle. The live LaunchAgent
     (`~/Library/LaunchAgents/com.tommy.campuspilot.plist`) points at
     the current py2app-built `CampusPilotAgent.app`, never this one.
     Not git-tracked, removed via `rm -rf`.
   - `docs/old-launchagent-backups/com.tommy.campuspilot.v3.bak.plist` —
     a backup LaunchAgent config pointing at a defunct prior-project
     path (`~/CampusPilot_v3`, Python 3.9). The "ported from
     CampusPilot_v3" comments in `voice/listen.py`/`voice/speak.py`/
     `ui/menu_bar.py` are attribution prose only, not a dependency on
     this file. Git-tracked, removed via `git rm`.

**Why**: (1) is a documentation-accuracy fix — this project's own
protocol requires trusting the code over stale docs and fixing the docs
to match. (2) was `ROADMAP.md`'s "Next" → Cleanup item.

**Files affected**: `HANDOFF.md`, `ARCHITECTURE.md`, `ROADMAP.md`
(documentation); `memory.json`,
`CampusPilotAgent.app.old-handbuilt/`,
`docs/old-launchagent-backups/` (removed).

**Tests**: 753 passing both before and after the cleanup — none of the
removed files were exercised by the test suite. No new tests needed
(deletion of genuinely dead files, not a behavior change).

**Note**: the git-tracked deletions (`memory.json`, the backup plist)
are staged but intentionally **not committed** — per this project's
"only commit when explicitly asked" convention.

---

## 2026-08-15 (earlier) — Commits landed, tool-count fix, menu-bar cost readout

**What**: Three pieces of follow-up work in a new session, done in order:
1. Committed the two batches of prior-session work that had sat
   uncommitted: `602bd03` (Phase 8 post-release hardening — voice-
   confirmation gating, sentence-chunked TTS, timed quiet/sleep/off
   modes, their tests, the `config/settings.py` comment fix) and
   `262bf2b` (the persistent documentation system itself).
2. Fixed a doc-accuracy gap found during review: `CLAUDE.md`,
   `ARCHITECTURE.md`, and `ROADMAP.md` all said "~45 tools" — the real
   registered count (`tools.registry._REGISTRY` after `tools.schemas`
   import) is 53. Committed as `f3fd416`. `CHANGELOG.md`'s own Phase 1
   entry (below) keeps its original `~45` — that's a historical record
   of tool count *at Phase 1*, not current state, and correcting it
   would misstate history rather than fix a stale fact.
3. Built the menu-bar cost readout (`ROADMAP.md`'s "Next" item),
   Option B: a lazily-computed "Estimated Cost" item in `ui/menu_bar.py`'s
   existing dropdown (alongside Recent Notes/Tasks/Actions), not an
   always-visible title figure (Option A, considered and explicitly not
   chosen — see `ROADMAP.md`).

**Why**: (1)-(2) were a clean-baseline pass requested before starting
new roadmap work. (3) directly extends Phase 8's cost-visibility
priority using data that already existed (`usage_history.json`) —
no new provider/API integration.

**Files affected**: `agent/usage.py` (new `cost_since(cutoff)`/
`cost_today()` — returns `None`, not `0.0`, if usage data can't be
read/parsed, so a caller can tell "no usage yet" apart from "data
unavailable" and fail safely), `ui/menu_bar.py` (new "Estimated Cost"
menu item + `show_cost` handler, following the same click-to-`rumps.
alert` pattern as the existing Recent Notes/Tasks/Actions items —
computed on click, no polling timer, title/state-icon system untouched),
`tests/test_usage.py` (+6 tests: multi-record sum, empty history,
missing file, corrupt JSON, malformed record shape, midnight cutoff),
`tests/test_phase6_security.py` (+5 tests: correct display, multi-record
sum, empty-history display, corrupt-data fail-safe display, no
interference with conversation/voice-state machinery).

**Decisions**: `cost_since()`/`cost_today()` deliberately live in
`agent/usage.py`, not inline in `ui/menu_bar.py`, specifically so
`pages/1_Dashboard.py` (which already sums the same `get_since()`
records for its own today/by-provider/by-operation breakdowns) and any
future always-visible title indicator can reuse the identical
aggregation instead of each reimplementing it.

**Tests**: 753 passing (742 + 11 new), full suite, no regressions.

---

## 2026-08-15 (earliest) — Persistent session/handoff documentation system

**What**: Added `CLAUDE.md`, `HANDOFF.md`, `ARCHITECTURE.md` (root,
supersedes `docs/ARCHITECTURE.md`), `ROADMAP.md`, `CHANGELOG.md`,
`SESSION_LOG.md`.

**Why**: So a new Claude Code session can read `CLAUDE.md` + `HANDOFF.md`
and continue this project without the prior conversation's context —
requested directly after a very long single session made clear how much
implicit context had accumulated.

**Files affected**: New root-level docs only; no application code
changed except one stale comment fix in `config/settings.py` (said
`agent/usage_limits.py`, the logic actually lives in `agent/usage.py` —
found while cross-checking the architecture doc against real code).

**Tests**: Full suite re-run after the comment fix — 742 passing, no
behavior change.

---

## 2026-08-14 (late) — Post-Phase-8 live production fixes

Three fixes made directly in response to real, observed behavior on the
running app, not a planned phase.

**1. Voice-confirmation gating extended to `open_browser`/
`consult_coworker_agent`**
- *Why*: Live incident — background audio (a TV, not the user) was being
  transcribed as commands. `add_reminder` was already protected by a
  voice-specific always-confirm rule; `open_browser` and
  `consult_coworker_agent` (the real entry point into ResearchAgent's
  own un-gated internal browsing) were not, and were observed making
  real tool calls from misheard audio.
- *Files*: `agent/autonomy.py` (`_VOICE_ALWAYS_CONFIRMS` set extended),
  `tests/test_autonomy.py` (+4 tests).
- *Tests*: 714 passing after this change.

**2. Sentence-chunked TTS**
- *Why*: User-reported ~3 second gap between Jarvis finishing "typing" a
  response and starting to speak it — root cause: `voice/speak.py`
  synthesized the entire response in one OpenAI TTS call before any
  playback could start.
- *What*: Split response text into sentences (`_split_into_sentences`),
  synthesize and play each sequentially instead of the whole reply at
  once. Interruption (`stop_speaking()`) now checked between chunks via
  `_play_and_track`'s returned exit code (negative = killed by signal).
- *Files*: `voice/speak.py`, `tests/test_voice_speak.py` (+10 tests).
- *Tests*: Live-measured — synthesizing just the first sentence of a
  3-sentence reply took ~1.1s vs. ~3.0s for the whole reply in one call.
  724 passing after this change.

**3. Timed quiet modes ("sleep" / "off")**
- *Why*: User asked for a way to fully suppress Jarvis for a bounded
  window without needing to remember a wake phrase — directly related to
  the same voice-misfire problem as fix #1.
- *What*: Extended `agent/quiet_mode.py` (previously indefinite-only)
  with an optional auto-expiring `until` timestamp — "sleep" = 10
  minutes, "off" = 30 minutes, either phrase upgradable from plain
  "quiet," still cancellable early by an explicit wake phrase. Wired into
  both `ui/menu_bar.py` (spoken confirmation, unlike silent indefinite
  quiet mode) and `app.py` (was a real gap: without this, saying "sleep"
  in the Streamlit chat would have been sent to the model as an ordinary
  message).
- *Files*: `agent/quiet_mode.py`, `ui/menu_bar.py`, `app.py`,
  `tests/test_quiet_mode.py` (+16 tests), `tests/test_phase6_security.py`
  (+3 tests).
- *Tests*: 742 passing after this change. Live-verified against the real
  `quiet_mode.json` state file, then manually reset before finishing.

All three deployed to the live running app (`CampusPilotAgent.app`
restarted after each), verified via clean startup logs.

---

## 2026-08-14 — Phase 8: Observability, Cost Control & Agent Runtime Hardening

**What**: Real per-call token/cost accounting; a cost dashboard;
configurable per-request usage limits; genuine subprocess-based agent
process isolation; `request_id` correlation via contextvars; regression/
security tests for all of it.

**Why**: A real, confusing cost overrun (22M tokens / 849 requests /
$18.47 in one month) with no way to attribute it to a specific
tool/agent/operation. The user's own prioritized fix list, in order:
cost attribution, agent timeout architecture, request-ID propagation.

**Key decisions**:
- Discovered mid-build that the *live* `consult_coworker_agent` tool
  path called an agent's `execute()` directly, in-process, with **no
  timeout at all** — the existing `ThreadPoolExecutor`-based timeout in
  `agent/agents/manager.py`'s `route_and_execute()` was dead code on the
  real path (only reachable from tests). Fixed by adding a new,
  separate `execute_agent()` that the real tool calls, subprocess-
  isolated with a genuine `subprocess.run(timeout=...)` SIGKILL.
  `route_and_execute()` was deliberately left untouched (its own test
  suite depends on in-process fake agents a subprocess couldn't see).
- Found and fixed a real test-hygiene bug: several executor-integration
  test files were writing zero-cost artifact records into the *real*
  `~/Library/.../usage_history.json` (245 → 299 records observed across
  one session) because they isolated `HISTORY_FILE`/`STATE_FILE` but not
  the newer `USAGE_FILE`. Fixed in 7 test files; cleaned 297 artifact
  records back out of the real file (kept the 2 genuine ones,
  identified by nonzero token counts — a signature no mocked response
  can produce).
- Found and fixed a pre-existing, unrelated bug while verifying the new
  dashboard section: `pages/1_Dashboard.py` referenced an undefined
  `active_executions` variable, crashing the entire page on load.

**Files affected**: `agent/usage.py` (new), `agent/agents/worker.py`
(new), `agent/agents/manager.py`, `tools/schemas/agents.py`,
`agent/request_context.py`, `agent/executor.py`, `agent/research_agent.py`,
`agent/planner.py`, `agent/deep_reasoning.py`, `tools/computer_use.py`,
`tools/vision.py`, `voice/listen.py`, `voice/speak.py`,
`pages/1_Dashboard.py`, `config/settings.py`, plus ~10 test files
(new and modified).

**Tests**: Grew from 663 to 710 tests during the phase (later 742 after
the same-day follow-up fixes above). Live-verified: a real subprocess
running this project's own ~5-7s test suite was killed at 1.01s by a
1-second timeout, confirmed no orphaned process remained.

---

## 2026-08-14 — Make `open_application` robust to filler words and near-miss app names

**What**: `open_application` now strips filler words ("the", "app", etc.)
and fuzzy-matches against actually-installed `.app` bundles before
giving up.

**Why**: User-reported live issue — Jarvis had trouble opening apps and
running the calculator from voice commands with natural phrasing.

**Files affected**: `tools/computer.py`.

---

## 2026-08-14 — Phase 7: Agent Manager + Coworker Agents

**What**: `agent/agents/` package — a registry of coworker agents
(research, memory, coding-stub, qa) plus a deterministic, keyword-scored
router (`agent/agents/router.py`) and a real execution tool
(`consult_coworker_agent`).

**Why**: Formal specification (sections 0-32) to give Jarvis specialist
sub-agents for research/coding/QA/memory work instead of one model doing
everything, while keeping Jarvis as the orchestrator.

**Key decision**: An early design called the *executing* agent-manager
function unconditionally on every request inside `execute_task_stream`
(for attribution). The first full test run made a real, live, unmocked
Anthropic API call — an existing test's request text happened to match
the research routing keyword, and the manager actually ran
`ResearchAgent.execute()`. Redesigned so `execute_task_stream` only ever
calls the *pure*, read-only `route()` function; real execution happens
exclusively through the permission-gated `consult_coworker_agent` tool,
which is naturally mockable in tests the same way every other tool is.

**Files affected**: `agent/agents/*` (new package), `tools/schemas/agents.py`
(new), `agent/execution_state.py`, `agent/execution_history.py`,
`pages/1_Dashboard.py`, `agent/executor.py`, plus 10 new test files
(87 new tests).

**Tests**: Grew to 663 passing. Live smoke-tested (including catching
and correcting one accidental real memory write during testing).

---

## 2026-08-14 — Live TCC / voice-reliability debugging arc

A sequence of ~15 commits fixing a real, live outage: the menu-bar app
was crashing on launch (macOS TCC `SIGABRT` — missing
`NSSpeechRecognitionUsageDescription`), then, once fixed, voice stopped
working partway through (wake-word threshold miscalibrated, iPhone
Continuity Camera mic being selected instead of the real mic, on-device
speech recognition tasks leaking CPU indefinitely because
`SFSpeechRecognitionTask.cancel()` doesn't actually stop the underlying
work). Resolved by:
- Packaging Jarvis as its own code-signed `.app` bundle
  (`setup_app.py`, py2app alias mode) so it has its own TCC identity —
  editing the shared system Python launcher's Info.plist in place had
  been tried and reverted after it broke code signing and Keychain
  access.
- Running on-device transcription in a genuine subprocess
  (`voice/local_transcribe.py` + `voice/_local_transcribe_worker.py`)
  so a hard timeout can actually kill it, instead of relying on the
  broken `cancel()` API.
- Lowering the wake-word calibration threshold and explicitly skipping
  iPhone/iPad Continuity Camera input devices.

**Files affected**: `voice/listen.py`, `voice/local_transcribe.py`,
`voice/_local_transcribe_worker.py`, `ui/menu_bar.py`, `setup_app.py`
(new), `requirements.txt`.

**Tests**: Verified live end-to-end (voice actually working again on the
running app) in addition to the unit-test suite.

---

## 2026-08-14 — Phase 6.5: Claude Skills + Cowork integration layer

**What**: `agent/skills/` — data-only skill bundles (`SKILL.md` files)
Jarvis can attach as prompt context for a matching request; an honest
stub for a future Cowork integration (`agent/cowork_gateway.py`) that
reports itself unavailable rather than faking a working connection.

**Why**: Give Jarvis structured workflow guidance for research/data-
analysis/document-creation-shaped requests without building a second way
to execute code or call a tool.

**Files affected**: `agent/skills/*` (new package), `skills/research/`,
`skills/document_creation/`, `skills/data_analysis/` (new `SKILL.md`
files), `agent/delegation.py` (new), `agent/cowork_gateway.py` (new),
`agent/brain.py`.

---

## 2026-08-13 — Phase 6: Voice-first Jarvis

**What**: Native macOS menu-bar app (`ui/menu_bar.py`, `rumps`) as a
first-class, voice-first interface — wake-word detection, native TTS
(`voice/speak.py`), background scheduler loop, click-to-ask, plus
cross-process cancellation hardening.

**Files affected**: `ui/menu_bar.py` (new), `voice/listen.py` (new),
`voice/speak.py` (new), `agent/voice_session.py` (new),
`agent/voice_state.py` (new), `agent/tts_control.py` (new).

---

## 2026-08-13 — Phase 5: Persistent execution history, cancellation, cross-interface state

**What**: `agent/execution_history.py` (bounded, persistent record of
past requests), `agent/jarvis_state.py` (cross-process "what is Jarvis
doing right now" snapshot), `agent/cancellation.py` (formal cancellation
API built on `agent/execution_state.py`'s active-execution registry).

**Files affected**: `agent/execution_history.py` (new),
`agent/jarvis_state.py` (new), `agent/cancellation.py` (new),
`agent/execution_state.py`, `agent/executor.py`.

---

## 2026-08-13 — Phase 4: Planning, autonomy, verification, retry, cancellation

**What**: `agent/planner.py` (structured multi-step plans for complex
requests), `agent/autonomy.py` (real, tested autonomy levels controlling
which tools require confirmation), `agent/verification.py` (post-action
checks for side-effect tools), `agent/retry_policy.py` (bounded,
deterministic tool-failure retry).

**Files affected**: `agent/planner.py` (new), `agent/autonomy.py` (new),
`agent/verification.py` (new), `agent/retry_policy.py` (new),
`agent/executor.py`.

---

## 2026-08-13 — Phase 3: Unified memory system

**What**: `agent/memory/` — one typed `Memory` model (type, confidence,
importance, supersession) replacing three separate raw-string-list
systems (lessons, patterns, notes) with thin backward-compatible
wrappers over the new store so existing callers didn't need to change.
Migrates legacy data once, automatically, on first read.

**Files affected**: `agent/memory/*` (new package), `agent/lessons.py`,
`agent/patterns.py`, `agent/memory_agent.py` (rewritten as wrappers),
`agent/context.py` (new — relevance-ranked retrieval), `agent/brain.py`.

---

## 2026-08-13 — Phase 2: Typed config, request context, structured logging, model router

**What**: `config/settings.py` (one typed, frozen, env-overridable
`Settings` dataclass replacing scattered hardcoded literals and
duplicated constants), `agent/request_context.py` (per-request
correlation), `agent/observability.py` (structured JSON-lines
diagnostics logging), `agent/model_router.py` (formalized the existing
static Claude-primary/OpenAI-fallback choice behind a real interface).

**Files affected**: `config/settings.py` (new), `agent/request_context.py`
(new), `agent/observability.py` (new), `agent/model_router.py` (new),
widespread call-site updates to use them.

---

## 2026-08-13 — Phase 1: Tool registry extraction

**What**: `tools/registry.py` — single source of truth for every tool
(schema, permission level, handler), replacing three separately-
maintained copies of the same ~45 tools (schemas in `agent/brain.py`,
permission levels in `agent/permissions.py`, dispatch in an if/elif
chain in `agent/executor.py`). Also fixed the memory store's path (was
relative, silently depended on process working directory) and removed
dead code.

**Files affected**: `tools/registry.py` (new), `tools/schemas/*` (new
package), `agent/brain.py`, `agent/permissions.py` (became a thin
re-export wrapper), `agent/executor.py`, `database/memory.py`.

---

## 2026-08-09 — Initial CampusPilot foundation

Initial project: Streamlit chat UI, first tool set, Claude/OpenAI clients.
