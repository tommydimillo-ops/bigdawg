# HANDOFF — Jarvis current state

**Read this after `CLAUDE.md`.** This file is the single source of truth
for "what's going on right now" — it will drift out of date faster than
the other docs; if anything here contradicts the actual code or git
state, trust the code (see `CLAUDE.md`'s NEW SESSION PROTOCOL) and fix
this file.

Last updated: 2026-08-16, a session that continued directly from the
previous session's Phase 9 Milestone 2 commit (`8d4da44` — complete,
pushed, CI-verified) by implementing Phase 9 Milestone 3 (bounded
parallel coworker delegation + verification) end to end. Before
considering the milestone finished, a dedicated review pass checked the
implementation against M3's own stated goals and found two real gaps —
`ResearchAgent` bypassing Milestone 2's cost-aware routing entirely, and
cancellation only able to stop *new* coworker work rather than a
coworker subprocess already running — and closed both before this
session's commit. This same commit also records `ROADMAP.md`'s next
planned item (OpenClaw interoperability/gateway bridge) as **planned,
not implemented** — nothing OpenClaw-related exists anywhere in this
codebase; this session only documented the plan, per explicit
instruction not to build it yet.

## Current project status

**Phase 9 milestone structure**: Milestone 0 (GitHub CI) and Milestone 1
(browser-profile locking) complete and committed (`d3481fc`, `7b67bf0`).
Milestone 2 (task-aware, multi-provider model routing) complete,
committed, pushed, and CI-verified as `8d4da44`. **Milestone 3 (bounded
parallel coworker delegation + verification) is complete and lands as
this session's commit** — see "Files recently modified" below for the
exact file list. Milestone 4 (FTS5 conversation/history search) has not
started, and is now sequenced *after* the newly-planned OpenClaw
interoperability work (see `ROADMAP.md`'s "Next" section) rather than
immediately after Milestone 3.

This session's commit is the direct child of `8d4da44` — check `git log
--oneline -3` to confirm the exact SHA and that it matches
`origin/main`, since this file can't self-reference its own commit hash
at the time it was written. If `git status` shows anything uncommitted
beyond this, trust the code over this paragraph (per this file's own
opening instruction).

The working tree (as committed) contains: `execute_agents_parallel()`
(bounded to `max_parallel_agents = 3`, batches over that size rejected
outright), the new `delegate_parallel_tasks` tool (deliberately not
`parallel_safe`), required/optional subtask semantics with bounded
per-task retry, `verify_agent_result()` for evaluating coworker results
objectively, genuine mid-flight coworker-subprocess cancellation
(`Popen`-based, `SIGTERM` → bounded grace → `SIGKILL`, verified against
a real OS process), cross-process-safe audit logging, and
`ResearchAgent` routed through the same M2 `classify_task()`/
`build_fallback_chain()` primitives the outer request uses. **1024 tests
pass, 0 failures** (928 before this milestone + 96 new), no live/paid
API calls anywhere in the suite.

## What we are currently building

Nothing actively mid-task — Milestone 3 is feature-complete, reviewed,
tested, and committed as of this session. The next planned work is
OpenClaw interoperability (architecture/research audit first, per
`ROADMAP.md`'s explicit initial-scope list) — **not started**, and
should not be started without the user's explicit go-ahead, matching
this project's standing commit/build convention.

## What was completed (this session, most recent first)

1. **Gap-closing review pass** (before considering M3 done) — found and
   fixed two real gaps against M3's own stated goals:
   - **`agent/research_agent.py` bypassed M2 routing entirely**: it
     called `anthropic_client.messages.create(model=settings.default_model,
     ...)` directly, hardcoded, no fallback, no capability/health/budget
     filtering. Rewired `research()` to call `classify_task()` +
     `build_fallback_chain()` (the same primitives `agent/executor.py`
     uses) and dispatch through one of three provider-shaped loops
     (Anthropic tool loop; a new OpenAI-compatible-shaped loop for
     OpenAI/xAI; a single grounded Perplexity Agent API call, no tool
     loop). Falls through to the next candidate only on a raised
     exception — safe to restart from scratch because `ResearchAgent`'s
     own tools (`open_browser`, `read_document`) are read-only. Each
     call now records usage with `agent="research"`, real `task_type`,
     and `fallback_position` attribution, and participates in
     `agent/provider_health.py`'s cooldown tracking.
     `_call_perplexity_agent`/`_client_for_provider` are small, local
     re-implementations (not imports from `agent/executor.py`) —
     importing from there would create a real import cycle
     (`executor → agent.agents → agent.agents.research →
     research_agent → executor`); documented inline as intentional, not
     refactored further this pass. 15 new tests
     (`tests/test_research_agent.py`), plus a one-line fix to
     `tests/test_usage_limits_integration.py` (patched
     `agent.research_agent.client`, which no longer exists, → 
     `agent.research_agent.anthropic_client`).
   - **Cancellation couldn't stop an already-running coworker
     subprocess**: `execute_agent()` used `subprocess.run`, which blocks
     synchronously and never exposes the child process handle, so there
     was no way to signal it from another thread once launched.
     Investigated whether this was safely fixable without a large
     rewrite (it was — confined to one function) before building
     `_run_agent_subprocess()`, a `Popen`-based poll loop using the
     stdlib's documented-safe `communicate()`-retry pattern (avoids the
     classic pipe-buffer deadlock risk of polling `wait()`/`poll()`
     without draining output). On cancellation: `SIGTERM` first, a
     bounded ~3s grace period, `SIGKILL` only if it doesn't exit; every
     exit path (normal, timeout, cancelled, or an unexpected exception)
     reaps the child, so no orphan/zombie can result. The existing
     timeout guarantee is unchanged, not weakened. 8 new tests
     (mocked-`Popen` mechanics plus one real, separate-OS-process test
     confirming no orphan via `os.kill(pid, 0)` after cancellation).
2. **Phase 9 Milestone 3 original implementation** (same session, before
   the gap-closing review) — the bounded-parallel-delegation
   architecture itself:
   - `agent/agents/manager.py`'s `execute_agents_parallel()` — capability
     unchanged per-subtask execution via the existing `execute_agent()`;
     adds a batch-size ceiling (reject, never truncate), a cancellation
     check, a global-budget pre-flight check, bounded per-task retry,
     and `verify_agent_result()`-based `BatchStatus` computation
     (`ALL_SUCCEEDED`/`PARTIAL`/`FAILED`).
   - `tools/schemas/agents.py`'s new `delegate_parallel_tasks` tool —
     deliberately not `parallel_safe` (would otherwise let the model
     multiply concurrent subprocesses past the ceiling via
     `_run_tool_batch`'s own, unrelated concurrency mechanism for
     read-only tools). `consult_coworker_agent` (single-task) is
     completely unmodified.
   - `agent/agents/models.py` — new `AgentTaskRequest`, `AgentBatchItem`,
     `AgentBatchResult`, `BatchStatus` dataclasses. `AgentResult` itself
     was left untouched (avoided touching a widely-used, already-tested
     dataclass).
   - `agent/verification.py`'s new `verify_agent_result()` — cancellation
     → explicit failure → agent-reported `verification_status` →
     generic failure-marker check → (for `research` specifically) a
     bounded source-evidence heuristic. Deliberately not extended to
     FILES/BROWSER-shaped checks — no current coworker produces that
     shape of result yet.
   - `agent/execution_state.py` — new `active_agents`/`completed_agents`/
     `failed_agents`/`parallel_batch_size`/`verification_status` fields,
     additive alongside the pre-existing singular
     `active_agent`/`agent_task`/`agent_status`/`agents_used` (a batch
     leaves those untouched rather than overloading them).
   - `agent/audit.py` — added `fcntl.flock` around `log_action`'s append
     write, since parallel coworker subprocesses can now genuinely write
     to the shared action log concurrently (previously only one coworker
     subprocess ever ran at a time).
   - `config/settings.py` — new `max_parallel_agents = 3`,
     `max_agent_batch_retries = 1`.
   - 73 new tests at this stage (before the gap-closing review's further
     23 on top).

## What is partially completed

Nothing mid-implementation. Milestone 3 (architecture + gap-closing
review) is complete, fully tested, and committed.

## Current bugs / known issues

None discovered this session. No regressions found in the full suite.

## Current blockers

None. OpenClaw work should not start without the user's explicit
go-ahead (matching this project's standing commit convention, and this
session's explicit instruction not to begin it in the same pass as M3).

## Recent architectural decisions

- **Model judgment decides *what* subtasks might be independent; code
  decides *whether* it's safe to run them concurrently** — the same
  separation this project already applies to permissions and routing
  (skills matching, agent routing, task classification are all
  keyword/deterministic; only the *content* of a plan/batch is ever
  model-judged). `delegate_parallel_tasks`'s tool description constrains
  the model to genuinely independent work; the concurrency ceiling,
  depth guard, and budget gate are all code-enforced regardless of what
  the model asks for.
- **No second dispatch path** — `execute_agents_parallel()` is a bounded
  *caller* of the existing `execute_agent()`, not a parallel/competing
  execution mechanism. Every subtask, batched or not, still goes through
  exactly one subprocess-launch function.
- **Cost pre-flight, not a reservation engine** — `execute_agents_parallel()`
  checks `agent/provider_budget.py`'s existing `global_budget_status()`
  once before launching a batch; it does not reserve budget for calls
  already in flight. Same documented limitation M2's own per-request
  routing already has; building a full reservation engine was explicitly
  out of scope for this milestone.
- **`ResearchAgent` is the only coworker with a real LLM-routing
  question** — `MemoryAgent` makes no model call at all (pure data
  read/write); `CodingAgent` and `QAAgent`'s non-test-suite path both
  fully defer to the ordinary executor (no model call of their own to
  route). No speculative routing infrastructure was built for any of
  the three.
- **`_call_perplexity_agent`/`_client_for_provider` duplicated, not
  shared** — `agent/research_agent.py` has its own small local copies
  rather than importing `agent/executor.py`'s same-named functions,
  specifically to avoid a real import cycle. A future cleanup could
  extract a shared module; not done this pass (documented, not an
  oversight).
- **OpenClaw is planned, not implemented** — see `ROADMAP.md`'s "Next"
  section for the full initial-scope list (architecture audit first,
  separate runtime, localhost-only, allowlisted capabilities,
  permission-gated wrapper tools, no model-routing authority, no
  arbitrary callback into Jarvis's tool registry, no shared
  secrets/memory). Jarvis remains the sole orchestrator; OpenClaw is
  planned as a subordinate gateway, never a peer.
- (Carried over, still true) `MAX_AGENT_DEPTH = 1`, real coworker-agent
  execution goes through `execute_agent()` (subprocess-isolated), not
  `route_and_execute()` directly from a tool handler.
  `agent/quiet_mode.py` remains the one shared suppression mechanism.
  Root `ARCHITECTURE.md` is authoritative over `docs/ARCHITECTURE.md`.

## Files recently modified

**This session's commit** (Phase 9 Milestone 3, landing as the direct
child of `8d4da44` — confirm the exact SHA via `git log --oneline -3`):
```
modified: agent/agents/manager.py
modified: agent/agents/models.py
modified: agent/audit.py
modified: agent/execution_state.py
modified: agent/research_agent.py
modified: agent/verification.py
modified: config/settings.py
modified: tools/schemas/agents.py
modified: tests/test_agents_base_and_models.py
modified: tests/test_agents_manager.py
modified: tests/test_execution_state.py
modified: tests/test_usage_limits_integration.py
modified: tests/test_verification.py
new:      tests/test_agents_batch.py
new:      tests/test_agents_tool_batch.py
new:      tests/test_audit.py
new:      tests/test_research_agent.py
modified: ROADMAP.md
modified: ARCHITECTURE.md
modified: CHANGELOG.md
modified: SESSION_LOG.md
modified: HANDOFF.md (this file)
```

**Committed**, most recent first: this session's Milestone 3 commit,
`8d4da44` (Phase 9 Milestone 2 — task-aware multi-provider routing,
currency-reviewed), `7b67bf0` (Phase 9 Milestone 1 — Playwright
browser-profile ownership hardening), `d0f791c` (Obsidian vault
integration), `d3481fc` (Phase 9 Milestone 0 — GitHub Actions CI),
`3529737` (duplicate-scheduler fix), `96d20f5` (repository cleanup),
`1a15ac0` (menu-bar cost readout). See `CHANGELOG.md` / `git log` for
full history.

## Tests recently run and their results

`python -m unittest discover -s tests` → **1024 passed, 0 failed** (run
at the end of this session, immediately before commit). No paid API
calls: every provider/subprocess call in every test is mocked at the
client/`httpx.post`/`subprocess.Popen` boundary, except two deliberate
real-boundary tests (a real cross-process audit-log write test, and a
real separate-OS-process cancellation test) — neither makes a network
call. This number will be stale the moment new tests are added —
re-run, don't trust it blindly.

## What still needs to be done

1. **Push this session's commit** (`git push origin main`) if it hasn't
   already happened by the time this file is read, and verify GitHub
   Actions succeeds against the exact pushed SHA — see this session's
   own final report (in the conversation that produced this commit) for
   confirmation, or re-verify directly if picking this up fresh.
2. **OpenClaw interoperability** — the next planned work, explicitly
   **not started**. Begin with the architecture/research audit
   `ROADMAP.md`'s "Next" section calls for, before any code — and only
   with the user's explicit go-ahead, per this project's standing
   commit/build convention.
3. **Do not start Milestone 4** (FTS5 search) until OpenClaw work is
   either complete or explicitly deprioritized by the user — the
   sequencing (OpenClaw before M4) was an explicit instruction this
   session, not an accident of ordering.
4. Once xAI/Perplexity API keys are actually added by the user, the
   user previously said they want **tiny, cheap live smoke tests** run
   separately (not as part of any implementation pass) to confirm the
   real endpoints/model IDs actually work — still not done, still
   intentionally deferred.

## Exact recommended next steps

For the next session, in order of what's most likely to matter:

1. Re-verify this file against actual git state first (per `CLAUDE.md`'s
   NEW SESSION PROTOCOL) — confirm `git log --oneline -3` shows this
   session's Milestone 3 commit at `HEAD`, matching `origin/main`, and
   that `python -m unittest discover -s tests` still shows 1024 (or
   more) passing.
2. If the user wants to proceed with OpenClaw, start with the
   architecture/research audit — do not write integration code before
   that audit is done and reviewed, per `ROADMAP.md`'s explicit
   initial-scope ordering.
3. If the user has since added `XAI_API_KEY`/`PERPLEXITY_API_KEY`, offer
   the small live smoke test described above — cheapest real path, not
   a broad live test, and only after explicit confirmation given this
   project's standing real-API-cost sensitivity.

## Important context that would otherwise be lost

- **Real API cost is a standing user concern** — this milestone's
  ResearchAgent fix exists specifically because a coworker agent
  quietly bypassing M2's cost-aware routing defeated the whole point of
  building that routing in M2. Every cost-safety check added this
  session (the batch-level budget pre-flight, ResearchAgent's routing)
  was verified with mocked provider calls only — no live/paid API calls
  anywhere in this session's work.
- **Cancellation is now genuinely active for coworker subprocesses**,
  not just "stop starting new work" — a real, previously-accepted
  limitation from earlier in this same session's own work, investigated
  properly (per explicit instruction not to force an unsafe fix if it
  wasn't safely achievable) and confirmed safely fixable within one
  function before being built.
- **OpenClaw has zero implementation** — every mention of it anywhere in
  this codebase as of this commit is planning documentation
  (`ROADMAP.md`) or this file. Do not infer partial implementation from
  its presence in these docs.
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
