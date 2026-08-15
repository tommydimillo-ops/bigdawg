# HANDOFF — Jarvis current state

**Read this after `CLAUDE.md`.** This file is the single source of truth
for "what's going on right now" — it will drift out of date faster than
the other docs; if anything here contradicts the actual code or git
state, trust the code (see `CLAUDE.md`'s NEW SESSION PROTOCOL) and fix
this file.

Last updated: 2026-08-15, end of a session that committed prior work and
built the menu-bar cost readout.

## Current project status

Phase 8 (Observability, Cost Control & Agent Runtime Hardening) is
complete and committed. The three post-Phase-8 live-production fixes and
the documentation system (both previously uncommitted) are now committed
as `602bd03` and `262bf2b`. A doc-accuracy fix (tool count) landed as
`f3fd416`. The menu-bar cost readout (`ROADMAP.md`'s "Next" item, built
as Option B — a dropdown item, not an always-visible title) is
implemented and tested but **not yet committed** — awaiting the user's
go-ahead, per this project's "only commit when explicitly asked"
convention.

## What we are currently building

Nothing actively mid-task. The menu-bar cost readout (see above) is
finished and ready to commit whenever asked.

## What was completed (this session, most recent first)

1. **Menu-bar cost readout, Option B** — `agent/usage.py` gained
   `cost_since(cutoff)`/`cost_today()` (returns `None`, not `0.0`, on
   unreadable/corrupt usage data, so callers can fail safely rather than
   show a wrong number); `ui/menu_bar.py` gained an "Estimated Cost"
   dropdown item, read lazily on click via `cost_today()` — no polling
   timer, title/state-icon system untouched. 11 new tests (6 in
   `tests/test_usage.py`, 5 in `tests/test_phase6_security.py`). **Not
   committed yet.**
2. **Tool-count doc fix** (committed `f3fd416`) — `CLAUDE.md`,
   `ARCHITECTURE.md`, `ROADMAP.md` corrected from a stale "~45 tools" to
   the real registered count, 53. `CHANGELOG.md`'s Phase 1 entry was
   deliberately left at "~45" — historical record, not current state.
3. **Committed the two batches from the prior session**: `602bd03`
   (Phase 8 post-release hardening: voice-confirmation gating,
   sentence-chunked TTS, timed quiet/sleep/off modes, their tests, the
   `config/settings.py` comment fix) and `262bf2b` (the documentation
   system itself: `CLAUDE.md`, `HANDOFF.md`, `ARCHITECTURE.md`,
   `ROADMAP.md`, `CHANGELOG.md`, `SESSION_LOG.md`, plus the
   `docs/ARCHITECTURE.md` pointer note).

## What is partially completed

Nothing mid-implementation. Everything above either fully landed with
passing tests or wasn't started. The cost readout (item 1) is code-
and-test complete but sits uncommitted — not "partial," just
intentionally paused for commit approval.

## Current bugs / known issues

None newly discovered and unfixed this session. Pre-existing, documented,
not fixed (low priority, not currently causing harm):
- **Duplicate scheduler risk** — `agent/scheduler_daemon.py` and
  `ui/menu_bar.py`'s built-in scheduler both execute the same scheduled
  tasks if run simultaneously. Mitigation is procedural (don't run both),
  not code-enforced.
- **Playwright profile contention** — Streamlit and menu-bar processes
  each hold their own browser context against the same on-disk Chrome
  profile; no locking if both drive a browser at once.
- **`memory.json` at repo root is dead** — real memory store is
  `~/Library/Application Support/CampusPilot/memory.json`; the root file
  is a harmless pre-path-fix fossil.

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

## Files recently modified

**Uncommitted** (working tree, as of this writing):
```
M agent/usage.py            M tests/test_usage.py
M ui/menu_bar.py            M tests/test_phase6_security.py
M HANDOFF.md   M CHANGELOG.md   M ROADMAP.md   M ARCHITECTURE.md   (this documentation update)
```
All of the above is the menu-bar cost readout (code + tests) plus this
same-session documentation update describing it — nothing left over from
any earlier uncommitted state.

**Committed**, most recent first: `f3fd416` (tool-count doc fix),
`262bf2b` (documentation system), `602bd03` (Phase 8 post-release
hardening), `d5a8886` (Phase 8), `db71d8b` (Phase 7). See `CHANGELOG.md`
/ `git log` for full history.

## Tests recently run and their results

`python -m unittest discover -s tests` → **753 passed**, 0 failed (last
run this session, after the cost-readout implementation — 742 + 11 new
tests, no regressions). This number will be stale the moment new tests
are added — re-run, don't trust this number blindly.

## What still needs to be done

1. **Commit the menu-bar cost readout** (`agent/usage.py`,
   `ui/menu_bar.py`, `tests/test_usage.py`, `tests/test_phase6_security.py`,
   plus this documentation update) — code- and test-complete, 753/753
   passing, just awaiting explicit user approval per this project's
   commit convention.
2. Nothing else outstanding from this session.

## Exact recommended next steps

For the next session, in order of what's most likely to matter:

1. If the user wants the cost-readout work committed, do a normal
   `git add`/`git commit` for the files listed above (already tested —
   nothing further needed first). Consider whether they want it as one
   commit or split (feature code+tests vs. the doc update).
2. Otherwise, check `ROADMAP.md`'s "Next" section for the next planned
   work — provider admin-key cost reconciliation (explicitly user-
   deferred, don't reopen unprompted) and the documented-but-unfixed
   lifecycle risks (duplicate scheduler, Playwright profile contention)
   are the remaining candidates.

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
