# HANDOFF — Jarvis current state

**Read this after `CLAUDE.md`.** This file is the single source of truth
for "what's going on right now" — it will drift out of date faster than
the other docs; if anything here contradicts the actual code or git
state, trust the code (see `CLAUDE.md`'s NEW SESSION PROTOCOL) and fix
this file.

Last updated: 2026-08-15, a session that fixed this file's stale
commit-status language, did a repository cleanup pass, then fixed the
duplicate-scheduler lifecycle risk with a cross-process lock.

## Current project status

Phase 8 (Observability, Cost Control & Agent Runtime Hardening) is
complete and committed. The three post-Phase-8 live-production fixes and
the documentation system are committed as `602bd03` and `262bf2b`. A
doc-accuracy fix (tool count) landed as `f3fd416`. The menu-bar cost
readout is **committed** as `1a15ac0`. A repository cleanup (dead
`memory.json`, `CampusPilotAgent.app.old-handbuilt/`,
`docs/old-launchagent-backups/`) and the duplicate-scheduler fix
(`agent/scheduler_lock.py`, see below) are both code- and test-complete
but **not yet committed** — awaiting explicit approval per this
project's commit convention.

## What we are currently building

Nothing actively mid-task. Everything below either fully landed with
passing tests or wasn't started; the cleanup and scheduler-lock work
just haven't been committed yet.

## What was completed (this session, most recent first)

1. **Duplicate-scheduler fix** (`ROADMAP.md`'s "Known lifecycle risks" —
   this closes that item) — `agent/scheduler_lock.py` (new): a
   non-blocking, kernel-managed `fcntl.flock(LOCK_EX | LOCK_NB)` on a
   dedicated lock file
   (`~/Library/Application Support/CampusPilot/scheduler.lock`),
   re-attempted on every poll tick by both `agent/scheduler_daemon.py`
   (new `_poll_once()`, called from `run_forever()`) and `ui/menu_bar.py`
   (new `_scheduler_tick()`, called from `_scheduler_loop()`). Whichever
   process wins the lock for that tick executes due tasks exactly as
   before (neither poller's internal due-task/mark_run logic was
   touched); the loser skips the tick entirely — no execution, no
   `mark_run`, no UI/voice-state interaction — and logs a
   `scheduler_lock_deferred` diagnostic. Released automatically by the
   kernel the instant the holding process exits or is killed, so unlike
   `ui/menu_bar.py`'s own PID-file single-instance lock, this needed no
   stale-lock detection code at all. Both deployment modes (menu-bar
   always-on, `scheduler_daemon.py` headless fallback) are preserved and
   may now run together safely. 22 new tests: `tests/test_scheduler_lock.py`
   (8 — including a real subprocess-based cross-process test, with a
   hard `SIGKILL` to prove kernel-managed release, not just a clean
   exit), `tests/test_scheduler_daemon.py` (6), and a new
   `TestMenuBarSchedulerLock` class in `tests/test_phase6_security.py`
   (8, including one that binds the real `_run_due_scheduled_tasks`
   method to a mock instance via `types.MethodType` to prove the actual
   production call path executes end to end, not just that a stub gets
   called). **Not committed yet.**
2. **Repository cleanup** (`ROADMAP.md`'s "Next" cleanup item) — removed
   three confirmed-dead files after inspecting each for live references
   (code, launch agents, scripts, tests, docs): root `memory.json`
   (superseded by the absolute-path store under
   `~/Library/Application Support/CampusPilot/`, git-tracked, `git rm`'d),
   `CampusPilotAgent.app.old-handbuilt/` (pre-py2app bundle; the live
   LaunchAgent plist points at the current `CampusPilotAgent.app`, not
   this one; not git-tracked, `rm -rf`'d), and
   `docs/old-launchagent-backups/com.tommy.campuspilot.v3.bak.plist` (a
   backup LaunchAgent config pointing at a defunct prior-project path,
   `~/CampusPilot_v3`; git-tracked, `git rm`'d). Deletions are staged but
   **not committed** — awaiting explicit approval per this project's
   commit convention. `ARCHITECTURE.md` and this file's "known issues"
   section updated to drop references to the now-removed `memory.json`.
3. **Menu-bar cost readout, Option B** — `agent/usage.py` gained
   `cost_since(cutoff)`/`cost_today()` (returns `None`, not `0.0`, on
   unreadable/corrupt usage data, so callers can fail safely rather than
   show a wrong number); `ui/menu_bar.py` gained an "Estimated Cost"
   dropdown item, read lazily on click via `cost_today()` — no polling
   timer, title/state-icon system untouched. 11 new tests (6 in
   `tests/test_usage.py`, 5 in `tests/test_phase6_security.py`).
   **Committed as `1a15ac0`.**
4. **Tool-count doc fix** (committed `f3fd416`) — `CLAUDE.md`,
   `ARCHITECTURE.md`, `ROADMAP.md` corrected from a stale "~45 tools" to
   the real registered count, 53. `CHANGELOG.md`'s Phase 1 entry was
   deliberately left at "~45" — historical record, not current state.
5. **Committed the two batches from the prior session**: `602bd03`
   (Phase 8 post-release hardening: voice-confirmation gating,
   sentence-chunked TTS, timed quiet/sleep/off modes, their tests, the
   `config/settings.py` comment fix) and `262bf2b` (the documentation
   system itself: `CLAUDE.md`, `HANDOFF.md`, `ARCHITECTURE.md`,
   `ROADMAP.md`, `CHANGELOG.md`, `SESSION_LOG.md`, plus the
   `docs/ARCHITECTURE.md` pointer note).

## What is partially completed

Nothing mid-implementation and nothing uncommitted. Everything above
either fully landed with passing tests and is committed, or wasn't
started.

## Current bugs / known issues

None newly discovered and unfixed this session. Pre-existing, documented,
not fixed (low priority, not currently causing harm):
- **Playwright profile contention** — Streamlit and menu-bar processes
  each hold their own browser context against the same on-disk Chrome
  profile; no locking if both drive a browser at once.

**Resolved this session**: duplicate scheduler risk (`agent/
scheduler_daemon.py` and `ui/menu_bar.py`'s built-in scheduler
independently executing the same due task if run simultaneously) — see
"What was completed" above. `agent/scheduler_lock.py`'s cross-process
lock now enforces single ownership per poll tick; procedural
"don't run both together" advice is no longer necessary but still
harmless if followed.

## Current blockers

None.

## Recent architectural decisions

- Real coworker-agent execution goes through a **new** `execute_agent()`
  (subprocess-isolated) rather than modifying the existing
  `route_and_execute()` — the latter's test suite depends on in-process
  fake agents a subprocess can't see. Both functions now exist in
  `agent/agents/manager.py`; only `execute_agent()` is on the real,
  live path.
- Voice-sourced requests get an extra, autonomy-independent confirmation
  gate for anything with real-world cost/consequence
  (`agent/autonomy.py`'s `_VOICE_ALWAYS_CONFIRMS`) — established with
  `add_reminder`, now also covers `open_browser`/`consult_coworker_agent`.
  **If you add a new tool with a real side effect that a misheard wake
  word could plausibly trigger, consider adding it to this set.**
- `agent/quiet_mode.py` is the one shared mechanism for all suppression
  (indefinite "quiet" and timed "sleep"/"off") — resist the urge to build
  a parallel system for a similar future need; extend this one.
- Root `ARCHITECTURE.md` is now authoritative over `docs/ARCHITECTURE.md`.
- The menu-bar cost readout used Option B (dropdown item, lazy-on-click)
  over Option A (always-visible title figure) — Option A would need a
  recurring update path and would contend with the title's existing job
  of reflecting live voice/task state, a shared-mutable-UI-state risk
  not worth taking for a nice-to-have. `agent/usage.py`'s new
  `cost_since()`/`cost_today()` are deliberately generic (not menu-bar-
  specific) so `pages/1_Dashboard.py` and a possible future Option A can
  reuse the same aggregation instead of reimplementing it.
- The duplicate-scheduler fix chose a fresh, dedicated
  `agent/scheduler_lock.py` module (its own lock file, `scheduler.lock`)
  over extending `agent/scheduled_tasks.py`'s existing `TASKS_FILE.lock`
  — that lock is a *blocking* shared/exclusive lock scoped to safe
  read/modify/write of the JSON data, a different concept from a
  *non-blocking* per-tick ownership arbitration lock, and mixing the two
  purposes on one file risked subtle deadlocks. Also chose it over
  extending `ui/menu_bar.py`'s existing `APP_LOCK_FILE`/PID-based
  single-instance lock, since that lock answers a different question
  ("is a second menu-bar instance already running") from "which of the
  two *different* scheduler processes owns this tick" — `fcntl.flock` is
  also strictly better here than the PID-file pattern (kernel-released
  on crash/kill, no stale-lock detection code needed at all).

## Files recently modified

**Uncommitted** (working tree, as of this writing):
```
new:      agent/scheduler_lock.py
new:      tests/test_scheduler_lock.py
new:      tests/test_scheduler_daemon.py
modified: agent/scheduler_daemon.py
modified: ui/menu_bar.py
modified: tests/test_phase6_security.py
modified: HANDOFF.md, ARCHITECTURE.md, ROADMAP.md, CHANGELOG.md (this documentation update)
deleted (staged): memory.json, docs/old-launchagent-backups/com.tommy.campuspilot.v3.bak.plist
deleted (untracked, gone): CampusPilotAgent.app.old-handbuilt/
```
The first six rows are the duplicate-scheduler fix (code + tests); the
last two rows are the repository cleanup from earlier this same session.
Both are code- and test-complete, 775/775 passing, awaiting explicit
commit approval per this project's convention.

**Committed**, most recent first: `1a15ac0` (menu-bar cost readout:
`agent/usage.py`, `ui/menu_bar.py`, `tests/test_usage.py`,
`tests/test_phase6_security.py`, plus the documentation update
describing it), `f3fd416` (tool-count doc fix), `262bf2b` (documentation
system), `602bd03` (Phase 8 post-release hardening), `d5a8886`
(Phase 8), `db71d8b` (Phase 7). See `CHANGELOG.md` / `git log` for full
history.

## Tests recently run and their results

`python -m unittest discover -s tests -v` → **775 passed**, 0 failed
(run at the end of this session, after the cleanup and the
duplicate-scheduler fix — 753 + 22 new: 8 in `tests/test_scheduler_lock.py`,
6 in `tests/test_scheduler_daemon.py`, 8 in `tests/test_phase6_security.py`'s
new `TestMenuBarSchedulerLock` class — no regressions). Verified no test
artifacts leaked into the real
`~/Library/Application Support/CampusPilot/` files (no stray
`scheduler.lock`, no `unittest:`-prefixed entries in the real
`scheduled_tasks.json`). This number will be stale the moment new tests
are added — re-run, don't trust this number blindly.

## What still needs to be done

1. **Commit the repository cleanup and the duplicate-scheduler fix**
   (see "Files recently modified" above) — both code- and test-complete,
   775/775 passing, awaiting explicit user approval per this project's
   commit convention. Consider whether the user wants these as one
   commit or split (cleanup vs. scheduler-lock feature+tests vs. doc
   update).
2. Nothing else outstanding from this session.

## Exact recommended next steps

For the next session, in order of what's most likely to matter:

1. If the user wants the cleanup and/or scheduler-lock work committed,
   do a normal `git add`/`git commit` for the files listed above
   (already tested — nothing further needed first).
2. Otherwise, check `ROADMAP.md`'s "Next" section for the next planned
   work — Playwright profile contention is now the only remaining
   documented-but-unfixed lifecycle risk. Provider admin-key cost
   reconciliation is explicitly user-deferred — don't reopen unprompted.

## Important context that would otherwise be lost

- **The live app's actual running state is volatile and not tracked by
  git** — check `ps aux | grep CampusPilotAgent` / `grep streamlit`
  before assuming anything about what's currently running. As of this
  writing, `streamlit run app.py` is running (PID visible via `ps`); the
  native menu-bar app (`CampusPilotAgent.app`) is **not** currently
  running (it was running earlier this session and was restarted
  multiple times to pick up live fixes, but is down now — restart via
  `open CampusPilotAgent.app` if voice interaction is needed).
- **The user's real environment has a background-audio wake-word problem**
  — confirmed live via audit-log transcripts that were clearly TV/video
  content, not the user speaking. Partially mitigated (voice-confirmation
  gating, quiet/sleep/off modes) but the underlying over-sensitive wake-
  word detection itself was not re-tuned this session (discussed, not
  done — see `ROADMAP.md`).
- **Real API cost is a standing user concern**, not a one-time complaint
  — be mindful of it in any live testing (prefer free/cheap real smoke
  tests, as this session repeatedly did: an unregistered-agent-name
  subprocess call, a deferred-stub agent call, a memory write/cleanup —
  none of which touch a paid API).
- **The user explicitly deferred** linking real OpenAI/Anthropic Admin
  API keys for authoritative billing reconciliation ("it's okay for
  now") — don't reopen this unprompted; it's recorded in `ROADMAP.md`'s
  "Next" section for when they're ready.
