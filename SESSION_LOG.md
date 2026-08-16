# Jarvis — Session Log

Lightweight per-session record. Concise by design — for depth, see
`CHANGELOG.md` (what/why/tests) or `git log`. Newest entry on top.

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
