# HANDOFF — Jarvis current state

**Read this after `CLAUDE.md`.** This file is the single source of truth
for "what's going on right now" — it will drift out of date faster than
the other docs; if anything here contradicts the actual code or git
state, trust the code (see `CLAUDE.md`'s NEW SESSION PROTOCOL) and fix
this file.

Last updated: 2026-08-15, end of the session that built this
documentation system.

## Current project status

Phase 8 (Observability, Cost Control & Agent Runtime Hardening) is
complete and committed. Three additional live-production fixes landed
after it in the same working session, responding to real observed
problems, and are **not yet committed**. This documentation system
(this file and its siblings) is being built as its own, separate task
right now.

## What we are currently building

The persistent session/handoff documentation system itself
(`CLAUDE.md`, `HANDOFF.md`, `ARCHITECTURE.md`, `ROADMAP.md`,
`CHANGELOG.md`, `SESSION_LOG.md`) — user-requested directly, explicitly
to survive a new Claude Code session with no prior conversational
context. This is the last step of that task (writing this file).

## What was completed (this session, most recent first)

1. **Documentation system** — all 6 files written from a full repository
   inspection (not templates). `docs/ARCHITECTURE.md` (pre-existing,
   accurate through ~Phase 6) is superseded by the new root
   `ARCHITECTURE.md`; needs a short pointer note added (see "still to
   do" below).
2. **Voice-confirmation gating** for `open_browser`/
   `consult_coworker_agent` — extends the existing `add_reminder`
   voice-always-confirms rule in `agent/autonomy.py`.
3. **Sentence-chunked TTS** — `voice/speak.py` now speaks sentence-by-
   sentence instead of waiting for the whole reply.
4. **Timed quiet modes** — "sleep" (10 min) / "off" (30 min) in
   `agent/quiet_mode.py`, wired into both `ui/menu_bar.py` and `app.py`.
5. **Phase 8** (committed as `d5a8886`) — see `CHANGELOG.md` for full
   detail.

## What is partially completed

Nothing mid-implementation. Everything above either fully landed with
passing tests or wasn't started.

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

## Files recently modified

**Uncommitted** (working tree, as of this writing):
```
M agent/autonomy.py         M app.py                    M tests/test_quiet_mode.py
M agent/quiet_mode.py       M config/settings.py         M tests/test_voice_speak.py
M ui/menu_bar.py            M tests/test_autonomy.py     M tests/test_phase6_security.py
M voice/speak.py
?? ARCHITECTURE.md  ?? CLAUDE.md  ?? ROADMAP.md  ?? CHANGELOG.md  ?? SESSION_LOG.md  (?? HANDOFF.md, this file)
```

**Committed**, most recent: `d5a8886` (Phase 8), `db71d8b` (Phase 7). See
`CHANGELOG.md` / `git log` for full history.

## Tests recently run and their results

`python -m unittest discover -s tests` → **742 passed**, 0 failed (last
run this session, after the `config/settings.py` comment fix). This
number will be stale the moment new tests are added — re-run, don't
trust this number blindly.

## What still needs to be done

1. ~~Add a short pointer note to `docs/ARCHITECTURE.md` marking it
   superseded by the root `ARCHITECTURE.md`.~~ **Done** — verified
   present at the top of `docs/ARCHITECTURE.md` (confirmed in the
   following session by direct file inspection; this file had been
   stale on that point).
2. Decide whether to commit the 3 uncommitted live fixes (autonomy
   gating, TTS chunking, quiet modes) — they're tested and were verified
   live on the running app, just never committed. **Not committed
   automatically without being asked** — this project's convention is to
   only commit when the user explicitly requests it.
3. Nothing else outstanding from this session.

## Exact recommended next steps

For the next session, in order of what's most likely to matter:

1. If the user wants the 3 uncommitted live fixes committed, do a normal
   `git add`/`git commit` for them (they're already tested and verified
   live — nothing further needed first).
2. Otherwise, check `ROADMAP.md`'s "Next" section for the next planned
   work — the menu-bar cost readout and provider admin-key reconciliation
   are the two most recently discussed candidates, both currently
   un-started.

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
