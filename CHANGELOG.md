# Jarvis — Changelog

Meaningful changes only, chronological. Each entry: what changed, why,
what it touched, how it was verified. Trivial fixes/refactors aren't
individually recorded — see `git log` for full commit-level history if
needed.

---

## 2026-08-15 (most recent) — Phase 9 Milestone 2: task-aware, multi-provider model routing

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
