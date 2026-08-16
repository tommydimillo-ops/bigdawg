# HANDOFF — Jarvis current state

**Read this after `CLAUDE.md`.** This file is the single source of truth
for "what's going on right now" — it will drift out of date faster than
the other docs; if anything here contradicts the actual code or git
state, trust the code (see `CLAUDE.md`'s NEW SESSION PROTOCOL) and fix
this file.

Last updated: 2026-08-15, a session that implemented Phase 9 Milestone 2
(task-aware, multi-provider model routing) end to end, then — at the
user's explicit request, before allowing any commit — ran a narrow
pre-commit currency/cost review of every provider default against
current official documentation, and fixed what it found.

## Current project status

**Phase 9 milestone structure**: Milestone 1 (cross-process Playwright
browser-profile ownership locking) is **complete and committed** as
`7b67bf0`. Milestone 2 (task-aware, multi-provider model routing) is
implemented, currency-reviewed, and fully tested, but **uncommitted**.
Milestone 3 has not been started.

HEAD is at `7b67bf0` ("Harden Playwright browser profile ownership",
Milestone 1). **The working tree is NOT clean**: `git status` shows 11
modified files, 6 new untracked files, and this file (`HANDOFF.md`)
itself modified — see "Files recently modified" below for the exact
list. Nothing is staged. Nothing from Milestone 2 (or this file's own
edits) has been committed or pushed. `ROADMAP.md`/`ARCHITECTURE.md`/
`CHANGELOG.md`/`SESSION_LOG.md` still describe the pre-Phase-9 state;
they were deliberately NOT updated this session because the user's
instructions were explicit: implement, review, **do not commit, do not
push, do not start Milestone 3**. Update those four docs as part of
whatever commit eventually lands this work, not before.

The working tree contains a complete, tested, currency-reviewed Phase 9
Milestone 2: deterministic task classification, capability/health/budget
filtering, cost-aware provider ranking, a generalized N-provider fallback
loop, and working (not just scaffolded) optional xAI and Perplexity
providers. **928 tests pass, 0 failures** (re-confirmed this pass), no
live/paid API calls made anywhere in the test suite (everything is
mocked at the client/HTTP boundary).

## What we are currently building

Nothing actively mid-task — Milestone 2 is feature-complete, reviewed,
and tested. The only open item is the user's own commit decision (see
"What still needs to be done").

## What was completed (this session, most recent first)

1. **Pre-commit currency/cost review** (explicitly requested by the user
   after approving M2's architecture "in principle" but before allowing
   a commit) — re-checked every one of `agent/model_router.py`'s
   task-aware-routing provider/model defaults (the fallback/economy/
   quality tiers the router actually chooses between) against current
   official documentation instead of trusting names chosen earlier in
   the session, and found real, dated problems. `vision_model` sits
   outside the router's candidate set — a separate, hardcoded per-call-
   site OpenAI assignment (`tools/vision.py`), per `ARCHITECTURE.md`'s
   description of vision/transcription/TTS/planning as hardcoded, non-
   routed assignments — so it was out of scope for this router-focused
   pass and was missed here; it was caught and fixed separately in a
   second, narrowly-scoped cleanup pass (see the `vision_model` bullet
   below). `transcription_model`/`tts_model` were not reviewed and
   remain outside M2's scope entirely — no finding either way on those:
   - **Perplexity**: Sonar Chat Completions (what M2 originally used) is
     officially deprecated (2026-07), support ending **2026-09-27** —
     about 6 weeks out. Rather than build new M2 infrastructure on an
     API with a 6-week shelf life, migrated to the replacement **Agent
     API** now. Its request/response shape genuinely differs from every
     other provider here (`input`/`output`, not `messages`/`choices`),
     so it could not share `agent/executor.py`'s existing
     `_run_openai_compatible_loop` — added a narrow, dedicated,
     single-shot call path instead (`_call_perplexity_agent`/
     `_run_perplexity_agent_loop`, POSTs directly to
     `https://api.perplexity.ai/v1/agent`). Deliberately **not** a
     multi-step tool-calling loop: Perplexity is never handed Jarvis's
     tool registry, so it can never become a second orchestrator — a
     request routed to Perplexity gets one grounded research answer
     back and can't also perform a Jarvis tool action in the same turn.
     `config.settings.Settings.perplexity_model` now holds an Agent API
     **preset** string (`"low"`, Perplexity's own documented sonar-pro
     replacement), not a flat-rate model ID — this is the one settings
     field that differs in kind from every sibling `*_model` field, and
     is documented as such inline.
   - **OpenAI**: `gpt-5` (the M2-era default) is stale — superseded by
     the **GPT-5.6 family**. Added three tiers: `openai_economy_model`
     (`gpt-5.6-luna`), `fallback_model` (`gpt-5.6-terra`, the "balanced"
     default and unchanged legacy `fallback_choice()` return value),
     `openai_quality_model` (`gpt-5.6-sol`). All confirmed still
     callable via `chat.completions.create()` — no Responses API
     migration was needed or done.
   - **xAI**: `grok-4` (the M2-era default) isn't a real model ID.
     Replaced with two real tiers: `xai_economy_model` (`grok-4.3`,
     general-purpose and materially cheaper — the new default) and
     `xai_quality_model` (`grok-4.6`, the flagship, only selected for
     `quality_priority` tasks).
   - **Anthropic**: `claude-sonnet-5`/`claude-haiku-4-5-20251001` were
     confirmed still current — left unchanged, per the user's own
     instruction to only change if the docs actually supported it.
   - **`vision_model`** (not a router candidate — `tools/vision.py`'s
     hardcoded OpenAI vision call): left on the stale `gpt-5` default
     when the router tiers above were reviewed, then caught and fixed in
     a second, narrowly-scoped follow-up pass — updated to
     `gpt-5.6-terra`, confirmed image-input-capable via OpenAI's official
     docs and already confirmed callable via `chat.completions.create()`
     as `fallback_model` above (same GPT-5.6 family, same API shape).
     `tools/vision.py`'s call path needed no changes — same Chat
     Completions request with an `image_url` content block it already
     used; only the settings default moved. No test pinned the old
     `gpt-5` value, so none needed updating. `transcription_model`/
     `tts_model` remain untouched and out of scope.
   - **Pricing table** (`agent/usage.py`'s `_PRICING`): found and fixed
     genuinely stale, billing-relevant entries — Sonnet 5 was priced at
     Sonnet 4.6's old rate ($3/$15 vs. the real $2/$10) and Haiku 4.5 at
     Haiku 3.5's rate ($0.80/$4 vs. the real $1/$5). Rebuilt the whole
     table against each provider's official rate card, dated with a
     "pricing last verified: 2026-08-15" header comment. Perplexity's
     preset-based cost is deliberately left unpriced (a preset's
     multi-step research cost isn't a flat per-token rate) so it falls
     through to the existing conservative `_DEFAULT_TOKEN_PRICE`
     safety net rather than guessing — never silently zero.
   - Routing decision order, capability/health/budget filtering, cost
     ranking, and the deterministic-classifier architecture were all
     preserved exactly as approved — this pass changed model IDs,
     pricing, and Perplexity's call mechanics only, never the
     architecture.
   - 13 new tests added (settings overrides, OpenAI/xAI tier-selection
     in the router, and Perplexity Agent API request-shape/error/
     cooldown/single-candidate-failure coverage), plus fixes to
     existing tests whose hardcoded dollar-amount assertions depended
     on the now-corrected pricing.
   - Full suite re-run: **928 passed, 0 failed**. No paid API calls —
     everything mocked at the client/`httpx.post` boundary, confirmed
     both by code inspection and by the ~8-second total suite runtime.
2. **Phase 9 Milestone 2 original implementation** (same session, before
   the currency review above) — the task-aware routing architecture
   itself:
   - `agent/task_classifier.py` (new) — deterministic (no model call),
     keyword/pattern-based `classify(text, source)` → `TaskRequirements`
     (task type + vision/current-web/large-context/tool needs +
     latency/quality/cost priority flags), matching this project's
     standing "never let a model decide a routing outcome" rule.
   - `agent/provider_budget.py` (new) — same-day per-provider and global
     spend ceilings, reusing `agent/usage.py`'s existing
     `cost_since()`/`cost_today()` aggregation rather than a new store.
   - `agent/provider_health.py` (extended) — `xai_configured()`/
     `perplexity_configured()`, plus an in-memory, per-provider
     failure-cooldown tracker (`record_failure`/`clear_failure`/
     `is_in_cooldown`) the router consults instead of a live
     health-check ping before every request.
   - `agent/model_router.py` (extended) — `build_fallback_chain()`: the
     real task-aware entry point. Filter order is fixed and
     load-bearing: capability → configured/healthy → budget, never
     reordered, so routing can't discover mid-call that the cheapest
     candidate simply can't do the task. Falls back to the original
     static `[anthropic, openai]` chain if `task_aware_routing_enabled`
     is off or every candidate gets filtered out — never an empty list.
     `primary_choice()`/`fallback_choice()`/`select()` (the original
     Phase 1 interface) are untouched.
   - `agent/executor.py` (majorly extended) — generalized the old
     hardcoded two-tier Claude-then-OpenAI cascade in
     `execute_task_stream` into a real loop over however many
     candidates the router returns, trying each in order and falling
     through on failure (never calling multiple providers
     simultaneously to compare answers). Verified via a real
     (unmocked-router) end-to-end test that behavior with only
     Anthropic/OpenAI configured is byte-for-byte unchanged from before.
   - `agent/chat.py` (extended) — optional `xai_client`/
     `perplexity_client`, constructed only when the matching API key is
     present (`None`, never a raised exception, when absent).
   - 83 new tests at this stage (before the currency review's further
     13 on top).

## What is partially completed

Nothing mid-implementation. Milestone 2 (architecture + currency review)
is complete and fully tested; the only thing not done is committing it,
which was explicitly deferred to the user's decision.

## Current bugs / known issues

None currently open. The one previously-documented issue here —
**Playwright profile contention** (Streamlit and menu-bar processes each
opening their own browser context against the same on-disk Chrome
profile with no cross-process coordination) — is **fixed**, not merely
mitigated: Phase 9 Milestone 1 (`7b67bf0`) added `agent/browser_lock.py`,
a non-blocking `fcntl.flock(LOCK_EX | LOCK_NB)` on a dedicated lock file,
acquired by `tools/browser.py` before launching a persistent context and
released on shutdown/dead-context discard (same primitive
`agent/scheduler_lock.py` already used for the analogous scheduler race,
adapted for a held-open-for-the-context's-lifetime duration instead of
a per-tick one). A losing process gets a clean `BrowserBusyError` message
("Another Jarvis process is already using the browser. Try again in a
moment.") instead of racing Chrome's own profile lock. Verified this
pass: 25/25 tests in `tests/test_browser_lock.py` +
`tests/test_browser.py` pass, including a real cross-process contention
test with a hard `SIGKILL` to prove kernel-managed release. This item
should not reappear in this section unless a genuine regression is
found — confirm against `agent/browser_lock.py` before re-adding it.

## Current blockers

None technical. The only blocker is a decision, not a bug: whether/how
the user wants this session's uncommitted work committed (see below).

## Recent architectural decisions

- **Perplexity stays a narrow research provider, never a second
  orchestrator** — explicit user constraint, honored by giving it a
  single-shot call path with no access to Jarvis's tool registry, rather
  than trying to replicate the full tool-calling loop against the Agent
  API's own (differently-shaped) tool mechanism. This is a deliberate,
  documented scope limit, not an oversight: a request routed to
  Perplexity can't also perform a Jarvis tool action in the same turn.
- **Migrated off Sonar Chat Completions before it was strictly forced
  to** — it remains functional until 2026-09-27, but the user was
  explicit that a brand-new integration shouldn't knowingly be built on
  an API already scheduled for retirement, so the migration happened
  during this same pre-commit pass rather than being deferred.
- **`perplexity_model` holds an Agent API preset, not a model ID** — the
  one settings field that differs in kind from its siblings
  (`openai_economy_model` etc. are all real model ID strings). Documented
  inline in `config/settings.py` precisely because it's a surprising
  exception to the pattern every other provider field follows.
- **`agent/chat.py`'s `perplexity_client` (an OpenAI-SDK-shaped client
  object) is intentionally kept despite the Agent API migration, not
  leftover dead code** — inspected and confirmed this pass: the actual
  live Agent API call (`agent/executor.py`'s `_call_perplexity_agent`)
  correctly never touches it, using a raw `httpx.post` instead (the
  Agent API's `input`/`output` shape isn't OpenAI-compatible). The
  client's one real consumer is `agent/provider_health.py`'s
  `check_providers()`, where it supplies the `initialized` field for
  Perplexity's diagnostics entry, kept structurally uniform with
  `xai_client`/`openai_client`/`anthropic_client`'s identical role there
  — `tests/test_provider_health.py`'s
  `test_optional_providers_degrade_gracefully_when_unconfigured` already
  asserts `initialized == configured` for both `xai` and `perplexity`,
  confirming this redundant-but-intentional symmetry is an existing,
  tested pattern for optional providers generally, not a Perplexity-
  specific oversight. Removing it would mean special-casing Perplexity
  out of that otherwise-uniform 4-provider dict for no functional gain —
  not done, since that would be changing working architecture for style,
  which this project's conventions and the user's explicit instruction
  both rule out.
- **No live pricing lookups** — `agent/usage.py`'s `_PRICING` is a small,
  centrally maintained, dated catalogue, deliberately not fetched from a
  live pricing API before every request (would add a network dependency
  to routing). Unknown/unpriced models fall through to a conservative
  `_DEFAULT_TOKEN_PRICE`, never a silent zero.
- **Routing decision order is fixed**: task → requirements → capability
  filter → configured/healthy filter → budget filter → cost/quality
  ranking → cheapest reliable sufficient model → call one provider →
  success or fall through to the next candidate. Never call several
  providers simultaneously to compare answers. This order was already
  approved before the currency review and was not touched by it.
- (Carried over, still true) Real coworker-agent execution goes through
  `execute_agent()` (subprocess-isolated), not `route_and_execute()`
  directly from a tool handler. `agent/quiet_mode.py` remains the one
  shared suppression mechanism. Root `ARCHITECTURE.md` is authoritative
  over `docs/ARCHITECTURE.md`.

## Files recently modified

**Uncommitted** (working tree, as of this writing — confirmed against a
live `git status`; nothing staged, working tree is NOT clean):
```
modified: HANDOFF.md
modified: agent/chat.py
modified: agent/executor.py
modified: agent/model_router.py
modified: agent/provider_health.py
modified: agent/usage.py
modified: config/settings.py
modified: tests/test_model_router.py
modified: tests/test_phase6_security.py
modified: tests/test_provider_health.py
modified: tests/test_settings.py
modified: tests/test_usage.py
new:      agent/provider_budget.py
new:      agent/task_classifier.py
new:      tests/test_chat_providers.py
new:      tests/test_executor_multi_provider_fallback.py
new:      tests/test_provider_budget.py
new:      tests/test_task_classifier.py
```
Everything except `HANDOFF.md` itself is the ENTIRETY of Phase 9
Milestone 2 (original implementation + the currency review + the
`vision_model` follow-up cleanup layered on top) — none of it has been
committed. `HANDOFF.md`'s modifications are this session's documentation
accuracy pass (see below) — also uncommitted. `ROADMAP.md`,
`ARCHITECTURE.md`, `CHANGELOG.md`, and `SESSION_LOG.md` were deliberately
left untouched this session (see "Current project status" above) and
still describe the pre-Phase-9 state; update them as part of whatever
commit eventually lands this work.

**Committed**, most recent first: `7b67bf0` (**Phase 9 Milestone 1** —
Playwright browser-profile ownership hardening, cross-process locking,
complete), `d0f791c` (Obsidian vault integration), `d3481fc` (GitHub
Actions CI), `3529737` (duplicate-scheduler fix), `96d20f5` (repository
cleanup), `1a15ac0` (menu-bar cost readout). See `CHANGELOG.md` / `git
log` for full history — note `CHANGELOG.md` itself stops before these
most recent commits and well before this session's Phase 9 work; it
needs a catch-up pass whenever this work is committed.

## Tests recently run and their results

`python -m unittest discover -s tests` → **928 passed, 0 failed** (run
at the end of this session, working tree as described above). No paid
API calls: every provider call in every test is mocked at the client/
`httpx.post` boundary; total suite runtime is ~8 seconds, consistent
with zero live network calls. This number will be stale the moment new
tests are added — re-run, don't trust it blindly.

## What still needs to be done

1. **Get the user's explicit go-ahead to commit** Phase 9 Milestone 2
   (see "Files recently modified" above) — code- and test-complete,
   928/928 passing, currency-reviewed, but per this project's commit
   convention and the user's own explicit instruction this session
   ("do NOT commit, do NOT push"), nothing should be committed without
   asking first.
2. **When committing**, also update `ROADMAP.md` (move Milestone 2 from
   wherever it's tracked into "Completed"), `ARCHITECTURE.md` (§ on
   model routing — the static Claude/OpenAI description is now stale),
   `CHANGELOG.md` (a real entry — this is exactly the kind of
   "meaningful change" that file's own convention calls for), and
   `SESSION_LOG.md`, per `CLAUDE.md`'s SESSION END PROTOCOL. None of
   these were touched this session because the user's instructions were
   specifically to implement + review, not to finalize.
3. **Do not start Milestone 3** until the user says so — explicit
   instruction this session.
4. Once xAI/Perplexity API keys are actually added by the user, the
   user said they want **tiny, cheap live smoke tests** run separately
   (not as part of this review) to confirm the real endpoints/model IDs
   actually work — not yet done, intentionally deferred.

## Exact recommended next steps

For the next session, in order of what's most likely to matter:

1. If the user confirms they want Phase 9 Milestone 2 committed, do a
   normal `git add`/`git commit` for the files listed above (already
   tested — nothing further needed first), then do the doc catch-up
   pass described in "What still needs to be done" item 2.
2. If the user has since added `XAI_API_KEY`/`PERPLEXITY_API_KEY`, offer
   the small live smoke test described in item 4 above — cheapest real
   path, not a broad live test, and only after explicit confirmation
   given this project's standing real-API-cost sensitivity.
3. Otherwise, treat this as an ordinary session start: re-verify this
   file against actual git state (per `CLAUDE.md`'s NEW SESSION
   PROTOCOL) before doing anything else, since a lot changed here.

## Important context that would otherwise be lost

- **Real API cost is a standing user concern** — this whole currency
  review happened specifically to avoid committing stale model
  defaults, and the user was explicit that no real API credits should
  be spent doing it ("mock all provider requests... we will perform
  tiny live smoke tests separately"). Every research step in this
  session used web search/fetch against public docs, never a live model
  call to Anthropic/OpenAI/xAI/Perplexity.
- **Today's date matters for two of this session's findings**: as of
  2026-08-15, Perplexity's Sonar Chat Completions has about six weeks of
  official support left (ends 2026-09-27) — if a future session finds
  this file well after that date, re-verify the Agent API is still the
  right integration and that `perplexity_model="low"` is still a valid
  preset, since this area of the vendor's API was clearly still
  evolving quickly at review time.
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
