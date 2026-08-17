# Jarvis — Session Log

Lightweight per-session record. Concise by design — for depth, see
`CHANGELOG.md` (what/why/tests) or `git log`. Newest entry on top.

---

### 2026-08-17 (latest) — OpenClaw M1.5: real loopback Gateway smoke test, two real bugs found and fixed

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
