# HANDOFF — Jarvis current state

**Read this after `CLAUDE.md`.** This file is the single source of truth
for "what's going on right now" — it will drift out of date faster than
the other docs; if anything here contradicts the actual code or git
state, trust the code (see `CLAUDE.md`'s NEW SESSION PROTOCOL) and fix
this file.

Last updated: 2026-08-19. OpenClaw M1/M1.5/M2 (including the M2
hardening/review pass) are all now **complete, committed, pushed, and
CI-verified** — HEAD is `d270dc461b15d8bd79e013032fea9ba05a674f87`
("Add permission-gated OpenClaw outbound messaging"), CI run
`32310485314` succeeded. See the "OpenClaw M1 + M1.5" and "OpenClaw M2"
sections below for full detail; both are done, no OpenClaw work is
in-flight or awaiting review.

This same session then ran **Graphify G0** — evaluating and approving
Graphify as an optional, local, development-time structural-code-graph
tool for this repo (not a Jarvis runtime subsystem). G0 finalized as
commit-worthy documentation only (`.gitignore` entry, `docs/GRAPHIFY.md`,
and these project records) — no Jarvis source file changed — and is
committed as `7b4d0b6b2fecdd3264d8a5f48b3babcc1c5ee295` ("Document local
Graphify development integration"), pushed, CI-verified (GitHub Actions
run `32312885286`, success).

A later session implemented **Graphify G1** — four narrow, read-only
Jarvis tools over the locally generated Graphify graph
(`agent/code_graph.py`, `tools/schemas/graphify.py`) — **now complete,
committed as `c99e7928b34ee11de803a23fae2fc819d532b620`** ("Add
read-only Jarvis code graph intelligence"), pushed, CI-verified (GitHub
Actions run `32418542989`, success). The `graphify`/`graphifyy`
package/executable is still never invoked from Jarvis runtime; only the
graph *data* it already wrote is read, with the standard library only.

A later session then ran **Graphify G1.1** — a narrow reliability audit
triggered by an observation from G1's own real-graph validation (a
possible incremental-extraction completeness gap). Confirmed real and
reproducible via a controlled two-clone experiment; documented in
`docs/GRAPHIFY.md` (no `agent/code_graph.py` change was needed — its
`authoritative: false` design already covers this). The live local
graph was replaced with a clean full rebuild during the audit, and that
finding's documentation-only finalization is committed — see the
"Graphify G1.1" section below and `CHANGELOG.md`'s dated entry for the
exact commit hash.

## OpenClaw M1 + M1.5 — READ-ONLY GATEWAY BRIDGE ✅ COMPLETE, COMMITTED, PUSHED, CI-VERIFIED

- **M1 commit**: `d1eb8130609d03e0f4f68a3f2cc46c4e3d66ade2`
- **M1.5 commit**: `8502c03b396d774e8e1f41f1ace7e87383ec429b` (pushed,
  CI-verified, GitHub Actions run `32073836073`, unittest step
  completed/success)
- **Current OpenClaw M1 capabilities**: an optional, disabled-by-default,
  read-only Gateway bridge — authenticated loopback WebSocket, stable
  compatibility target `openclaw@2026.7.1-2` (verified against an
  actual running instance of that exact version, not just its source),
  protocol version 4, a persistent Jarvis Ed25519 device identity, a
  human-only pairing flow, `operator.read`-only scope, a fixed RPC
  allowlist (`health`/`status`/`node.list`), and two Jarvis tools
  (`openclaw_status`/`openclaw_list_nodes`).

**Automated vs. real-world testing**: M1 has extensive mocked and
local-fake-Gateway protocol tests (real Ed25519 signature verification
against a genuine local WebSocket server, never a stub), **and** as of
M1.5, has also been verified against a real, running
`openclaw@2026.7.1-2` process (temporary, isolated, removed afterward —
no OpenClaw installation persists on this machine).

## OpenClaw M2 — OUTBOUND TEXT MESSAGING ✅ COMPLETE, HARDENED, COMMITTED, PUSHED, CI-VERIFIED

- **Status**: code- and test-complete, hardened per review, **committed
  as `d270dc461b15d8bd79e013032fea9ba05a674f87`** ("Add
  permission-gated OpenClaw outbound messaging"), pushed to
  `origin/main`, CI-verified (GitHub Actions run `32310485314`,
  success). 1176 tests passing at that commit.
- **What it adds**: `send_message_via_openclaw` (permission_level=3,
  side_effect=True, requires_live_confirmation=True — matching
  `send_email`'s convention). Input is exactly `channel`/`target`/
  `message`, all required — no `account_id`/`thread_id` in this first
  release (both are optional in the real `SendParamsSchema` but not yet
  independently allowlisted; narrowed out of the public surface in the
  hardening pass). Backed by `agent/openclaw_messaging.py` (new) and a
  profile-based extension to `agent/openclaw_gateway.py`.
- **Security boundaries** (in addition to M1's, all still true): the
  messaging identity is a SEPARATE Ed25519 device identity/token from
  M1's read identity (`OPENCLAW_MESSAGE_DEVICE_PRIVATE_KEY`/
  `OPENCLAW_MESSAGE_DEVICE_TOKEN`, never `OPENCLAW_DEVICE_PRIVATE_KEY`/
  `OPENCLAW_DEVICE_TOKEN`); requests only `operator.write`; its own RPC
  allowlist is exactly `{send}` (the read identity cannot reach `send`;
  the messaging identity cannot reach `health`/`status`/`node.list`
  either — independently exact, not a superset), and `_call()` now
  enforces this by identity (`is`, not `==`) against a fail-closed
  check for exactly `_READ_PROFILE`/`_MESSAGE_PROFILE`, rejecting any
  forged `_Profile`, even one copying valid scopes/methods.
  **Precise wording on read authority** (corrected in the hardening
  pass — see CHANGELOG.md's hardening entry for the full three-way
  distinction): the read identity is genuinely incapable of any write
  through Jarvis. The reverse claim — that a compromised messaging
  credential carries no read authority at all — is NOT accurate at the
  Gateway's own server-side scope-semantics level (`operator.write`
  already satisfies an `operator.read` check there); what actually
  keeps the messaging identity from reading anything is Jarvis's own
  RPC confinement (the `{send}`-only allowlist above), not the
  credential's cryptographic scope. `chat.send`/`message.action`/
  `node.invoke` remain structurally unreachable; no raw-RPC tool
  exists, and the transport function once named `send_raw()` is now
  private (`_send_raw()`), used only by `agent/openclaw_messaging.py`;
  messaging is globally disabled by default
  (`openclaw_messaging_enabled = False`) with empty channel/target
  allowlists, so a fresh install cannot send anywhere.
- **Delivery semantics (corrected in the hardening pass)**: at most ONE
  transmission per logical send. An `OpenClawUncertainDelivery` (frame
  transmitted, no trustworthy response) is reported as
  `delivery_status: "uncertain"` and never automatically retried — the
  original implementation retried once with the same idempotencyKey,
  reasoning the Gateway's in-memory dedupe cache made that safe; review
  found that reasoning doesn't hold across a Gateway process restart,
  so the retry was removed. A dedicated verifier
  (`agent/verification.py`'s `_verify_send_message_via_openclaw`,
  registered in `_VERIFIERS`) parses this tool's JSON result directly
  so `uncertain`/`failed` are never mistaken for `confirmed` by the
  generic failure-marker string check.
- **Still not done**: no real channel (Telegram/Discord/WhatsApp/Slack/
  Signal/iMessage/...) configured or logged into, no real outbound
  message ever sent — every automated test uses the same
  local-fake-Gateway-server pattern as M1/M1.5, never a real channel.
  Choosing/configuring the first real channel is a separate, not-yet-
  started future step (see ROADMAP.md's "Next" section).

## Graphify G0 — DEVELOPMENT CODEBASE GRAPH BASELINE ✅ COMPLETE, COMMITTED, PUSHED, CI-VERIFIED

- **Commit**: `7b4d0b6b2fecdd3264d8a5f48b3babcc1c5ee295` ("Document local
  Graphify development integration"), pushed, CI-verified (GitHub
  Actions run `32312885286`, success).

Not a Jarvis subsystem — an optional, local, development-time
structural code graph (`graphify` CLI, `graphifyy` on PyPI,
`Graphify-Labs/graphify` on GitHub, v0.9.47), installed isolated via
`uv tool install graphifyy`, entirely separate from CampusPilot's
`.venv`/`requirements.txt`. Full detail, rebuild commands, and verified
limitations: `docs/GRAPHIFY.md`.

- **What it is**: a queryable graph of this repo's own code structure
  (`graphify extract . --code-only` then `graphify cluster-only .
  --no-label` — both fully local, zero LLM/API calls, zero cost),
  giving a Claude Code session faster answers to "what calls/imports/
  depends on X" than repeated grep/Read round-trips.
- **G0 result**: 3024 nodes, 6407 edges, 163 communities, 96% EXTRACTED
  / 4% INFERRED edge confidence, zero import cycles. Validated clean —
  zero secrets, zero ignored/private paths, zero meaningful absolute-path
  leakage in the generated output.
- **Cross-checked against real source across 8 architectural areas**
  (orchestration, tool registry, autonomy, provider routing, coworker
  system, OpenClaw, voice pipeline, memory/Obsidian) — mostly accurate
  down to the exact source line, and independently corroborated three
  separate architectural claims already documented in `CLAUDE.md`.
- **Two verified limitations, must be respected going forward**: (1) a
  same-basename module-collision false positive
  (`tools/registry.py` vs. `agent/skills/registry.py`); (2) a
  system-wide miss of the `register(ToolSpec(..., handler=...))`
  registration-wiring pattern — this codebase's core tool-wiring
  mechanism, invisible for all 14 `tools/schemas/*.py` modules. Because
  of these, Graphify must never be trusted alone for ToolSpec-to-handler
  wiring or any permission/autonomy/credential-boundary question.
- **Decision**: generated `graphify-out/` is gitignored, kept **local**,
  rebuilt on demand (~12s, free) — not committed, since this repo is
  public and the graph would be a large, low-diff-signal blob mapping
  exactly where the security-relevant logic lives.
- **Not enabled (at G0)**: `graphify-mcp`, MCP registration, `graphify
  install`/`claude install`, `PreToolUse` hooks (soft or strict), `watch`
  mode, semantic/document extraction, any Jarvis runtime integration.
  `CLAUDE.md` was deliberately not modified.

## Graphify G1 — FOUR NARROW READ-ONLY JARVIS CODE-GRAPH TOOLS ✅ COMPLETE, COMMITTED, PUSHED, CI-VERIFIED

- **Status**: code- and test-complete, **committed as
  `c99e7928b34ee11de803a23fae2fc819d532b620`** ("Add read-only Jarvis
  code graph intelligence"), pushed, CI-verified (GitHub Actions run
  `32418542989`, success). 1253 tests passing at that commit.
- **What it adds**: `agent/code_graph.py` (new) — a read-only reader
  over `graphify-out/graph.json`, standard library only (`json`, `os`);
  never imports `graphifyy`, never invokes the `graphify`/`graphify-mcp`
  executables. The only subprocess call anywhere in the module is a
  fixed-argv, `shell=False` `git rev-parse HEAD` / `git status
  --porcelain --untracked-files=no` (5-second timeout, cwd pinned to the
  repo root), used only to decide graph freshness — never to run
  Graphify itself. Four ToolSpecs (`tools/schemas/graphify.py`,
  registered through the normal `tools/registry.py` path, no separate
  dispatch mechanism): `code_graph_status`, `search_code_graph`,
  `analyze_code_impact`, `find_code_path` — all `permission_level=0`,
  `side_effect=False`, `unattended_allowed=True`, `parallel_safe=True`,
  no live confirmation (same risk class as `get_system_status`). Total
  registered tools: 64 (up from 60).
- **Staleness enforcement**: `fresh` only when the graph parses AND
  `built_at_commit` equals current git HEAD AND the tracked working tree
  is clean (untracked files, including gitignored `graphify-out/`
  itself, never count as dirty — only tracked modifications do).
  `search_code_graph`/`analyze_code_impact`/`find_code_path` all refuse
  to run any traversal unless state is exactly `fresh`, returning a
  structured `{"ok": false, "state": ..., "reason": ...,
  "rebuild_required": ...}` shape instead. Nothing auto-rebuilds a stale
  graph — that stays a manual, deliberate step.
- **Never authoritative, by construction**: every result carries
  `authoritative: false` and G0's exact known-limitations list. A result
  touching `tools/registry.py`, `ToolSpec`, `tools/schemas/`,
  `agent/autonomy.py`, or permission/credential-keyword paths
  additionally carries `source_verification_required: true` —
  deterministic path-prefix/keyword matching, never a model judgment
  call. Impact/path results are worded as structural relationships only,
  never a guaranteed-runtime-effect claim.
- **Bounds enforced**: search ≤20 results (hard cap); impact ≤depth 3
  (hard cap), ≤100 results (hard cap); path ≤depth 10 (hard cap), one
  shortest path only. No caller-supplied filesystem path, CLI
  subcommand, or raw query surface in any of the four `input_schema`s;
  no fifth generic "run a graph command" tool.
- **Real-graph validation**: before commit, the actual local
  `graphify-out/graph.json` (built at commit `d270dc4`) correctly
  reported `stale` once checked against the then-current HEAD (`7b4d0b6`,
  then this implementation's own uncommitted changes) — proving the
  fail-closed staleness path against a real scenario, not only a mock.
  After commit, the graph was rebuilt against the new `c99e792` HEAD and
  every tool (`code_graph_status`/`search_code_graph`/
  `analyze_code_impact`/`find_code_path`) was re-exercised against real
  data. A same-day follow-up audit (**Graphify G1.1**, see below) then
  found and fixed a real incremental-extraction completeness gap in that
  rebuilt graph — see the "Graphify G1.1" section.
- **Not enabled (still, at G1)**: `graphify-mcp`, MCP registration,
  `graphify install`/`claude install`, `PreToolUse` hooks, `watch` mode,
  automatic graph regeneration, any permission/autonomy/routing decision
  based on graph content. `CLAUDE.md` deliberately not modified.
  `graphifyy` still not in `requirements.txt`/CampusPilot's `.venv`.

## Graphify G1.1 — INCREMENTAL-VS-FULL EXTRACTION AUDIT ✅ COMPLETE, DOCUMENTED

Documentation-only follow-up to G1 — no `agent/code_graph.py`,
ToolSpec, or test change. Full detail: `docs/GRAPHIFY.md`'s "Known
incremental-extraction limitation" section.

- **What was found**: Graphify 0.9.47's default incremental extraction
  (`graphify extract . --code-only` reusing its on-disk manifest/AST
  cache) can silently omit a direct per-symbol `imports` edge when a
  newly-added/changed Python file imports a named symbol from an old/
  unchanged (cached) module — even though `built_at_commit` matches
  HEAD, the tracked tree is clean, and `code_graph_status` reports
  `fresh`. Confirmed for real: `tools_schemas_graphify --imports-->
  tools_registry_toolspec`/`tools_registry_register` were missing from
  the live incremental graph, present in a clean full extraction at the
  identical commit. A second instance (`test_graphify_tools.py`
  importing `_run_tool` from unchanged `agent/executor.py`) confirmed
  the pattern generalizes.
- **How it was confirmed**: two isolated local clones
  (`git clone --local --no-hardlinks`, outside the repo) — one
  reproducing the exact old-commit-to-G1 incremental transition, one a
  clean extraction with no prior cache at the same commit. Four
  unrelated pre-existing `tools/schemas/*.py` modules checked as a
  control showed zero difference, ruling out a one-file fluke. Every
  node and every `contains`/`calls`/`inherits`/`method` relation for
  the new G1 files was 100% identical between modes — narrow, not
  broad. Likely mechanism identified by reading (not modifying) the
  installed Graphify source: incremental mode shares "unchanged corpus"
  context with call-resolution passes but not the plain
  `from X import Y` symbol-binding pass.
- **Action taken**: the live local graph was replaced with a clean full
  rebuild (old graph backed up outside the repo, validated, backup then
  deleted) — final: 3157 nodes, 6650 edges, 152 communities, built at
  `c99e792`.
- **Why no runtime change**: `agent/code_graph.py` already marks every
  result `authoritative: false` with its `limitations` list regardless
  of which extraction mode produced the graph — this finding is a
  rebuild-workflow note, not a gap in the tool's own trust model.
- **Documented**: a new `docs/GRAPHIFY.md` section, a terminology
  clarification that `fresh` never implies structural completeness or a
  clean-extraction provenance, and a 5-step precision rebuild workflow
  (clean tree → move old graph to an external temp backup → re-extract
  from empty → validate → only then delete the backup). Also notes
  `--force`'s documented scope doesn't confirm AST-cache bypass, so an
  empty `graphify-out/` remains the verified full-rebuild method.

## Current project status

**Phase 9**: Milestones 0-3 complete, committed, pushed — HEAD `4265f55`
on `origin/main` at the time, CI-verified (GitHub Actions run
`31950985587`, `success`). Milestone 4 (FTS5) not started, sequenced
after OpenClaw.

**OpenClaw** (a separate, real, independently-developed open-source
project — github.com/openclaw/openclaw, docs.openclaw.ai — not a Jarvis
subsystem): **M0** complete, approved, no code. **M1**, **M1.5**, and
**M2** (including its hardening pass) all complete, committed, pushed,
CI-verified — see the section above.

**Graphify**: **G0** (evaluation), **G1** (four narrow read-only Jarvis
tools), and **G1.1** (incremental-vs-full extraction audit) all
complete — see the sections above and `docs/GRAPHIFY.md`. Still not a
Jarvis runtime dependency — `graphifyy` itself is never imported or
invoked; only its already-generated graph.json is read.

**Working tree**: clean as of this writing, aside from this
documentation pass itself (`docs/GRAPHIFY.md` and these project
records). Confirm with a live `git status`/`git log` rather than
trusting this file. **1253 tests pass, 0 failures** (unchanged from the
G1 commit — this pass is documentation-only, no runtime code touched),
no live/paid API calls during testing. No real OpenClaw installation
persists on this machine.

**Local graph note**: this docs commit advances git HEAD past the
graph's `built_at_commit` (still `c99e792`, from G1.1's rebuild). Once
this commit lands, `code_graph_status` will correctly and
conservatively report the graph `stale` again — purely because HEAD
moved, not because anything is wrong. This is expected and was
deliberately **not** re-rebuilt as part of this pass; the next refresh
should happen when actually useful before code-graph analysis on a
future implementation milestone, not reflexively after every commit.

## What we are currently building

Nothing actively mid-task — OpenClaw M1/M1.5/M2 and Graphify G0/G1/G1.1
are all complete and committed. **Do not start Graphify G2 (MCP/hooks/
auto-rebuild — none implemented or assumed), a real OpenClaw messaging
channel, OpenClaw device capabilities, OpenClaw agent/model-routing
integration, or Phase 9 Milestone 4 (Conversation & History
Intelligence)** until the user explicitly says so.

## What was completed (this session, most recent first)

-4. **Graphify G1.1 — incremental-vs-full extraction audit**
    (documentation-only follow-up): see the "Graphify G1.1" section
    above and `docs/GRAPHIFY.md`'s "Known incremental-extraction
    limitation" section for full detail. Summary: confirmed, via a
    controlled two-clone experiment, that Graphify 0.9.47's incremental
    extraction can miss direct per-symbol `imports` edges from a new
    file to an old/unchanged module; replaced the live local graph with
    a clean full rebuild; documented the finding and a precision
    rebuild workflow. No runtime code change — `agent/code_graph.py`'s
    existing `authoritative: false` design already covers it.
-3. **Graphify G1 — four narrow, read-only Jarvis code-graph tools**
    (prior session): see the "Graphify G1" section above and
    `docs/GRAPHIFY.md` for full detail. Summary: `agent/code_graph.py`
    (new, standard-library-only reader, one fixed-argv `git` subprocess
    call for freshness only, never invokes `graphify`/`graphify-mcp`);
    four ToolSpecs (`tools/schemas/graphify.py`) registered through the
    normal `tools/registry.py` path; strict fail-closed staleness
    (fresh only at matching HEAD + clean tracked tree); every result
    marked `authoritative: false` with `source_verification_required:
    true` on security-relevant results; bounded search/impact/path with
    hard caps; validated against both synthetic fixtures and the real
    local graph. Committed as `c99e792`, pushed, CI-verified.
-2. **Graphify G0 — development codebase graph baseline** (new session):
    evaluated and approved Graphify as optional, local, development-time
    tooling — see the "Graphify G0" section above and `docs/GRAPHIFY.md`
    for full detail. Committed: `.gitignore` entry, `docs/GRAPHIFY.md`,
    and these project records. `graphify-out/` itself stays local,
    gitignored, never committed.
-1. **OpenClaw M2 hardening/review pass** (prior session): a review of
    the M2 diff found several issues, all fixed — see CHANGELOG.md's
    2026-08-19 entry for full detail. Summary: removed the automatic
    same-key retry on uncertain delivery (unsafe across a Gateway
    restart); added a dedicated JSON-parsing verifier for
    `send_message_via_openclaw` in `agent/verification.py`; enforced the
    closed `_Profile` set by Python identity (`is`) in `_call()`, not
    `==`; renamed `send_raw()` to private `_send_raw()`; corrected
    documentation that overstated a compromised messaging credential as
    having no read authority at all (the Gateway's own server-side scope
    semantics are asymmetric); narrowed `account_id`/`thread_id` out of
    the public tool surface. Committed as `d270dc4`, pushed, CI-verified.
0. **OpenClaw M2 — outbound text messaging bridge** (new session,
   implementation + tests only, uncommitted): the real Gateway `send`
   RPC, never `chat.send` (`ChatSendParamsSchema` requires a
   `sessionKey` and is part of OpenClaw's own agent/session execution
   surface — confirmed via real `openclaw@2026.7.1-2` server source,
   not just followed on instruction) or `message.action` (a broader CLI
   action-dispatch RPC). Added a small, closed `_Profile` type to
   `agent/openclaw_gateway.py` — exactly two instances
   (`_READ_PROFILE`, unchanged from M1; new `_MESSAGE_PROFILE`, its own
   separate Ed25519 device identity/token
   `OPENCLAW_MESSAGE_DEVICE_PRIVATE_KEY`/`OPENCLAW_MESSAGE_DEVICE_TOKEN`,
   `operator.write` only — confirmed against real source that this
   already satisfies `operator.read`, so never both — `{send}`-only RPC
   allowlist). New `agent/openclaw_messaging.py`: Jarvis-side channel/
   target allowlists (disabled and empty by default — no wildcards, no
   OpenClaw-side name resolution), message validation (4000-char cap,
   rejects rather than truncates), a fresh internally-generated
   `idempotencyKey` per send. **Corrected in a same-day hardening pass**
   (see the item above and CHANGELOG.md): this originally included one
   bounded same-key retry on a genuinely uncertain delivery, reasoned
   safe against the real Gateway's in-memory, 5-minute-TTL idempotency
   cache — review found that reasoning doesn't survive a Gateway process
   restart, so the retry was removed; a genuinely uncertain delivery is
   now reported as such and left there, never auto-retried. New tool
   `send_message_via_openclaw` (permission_level=3, side_effect=True,
   requires_live_confirmation=True, matching `send_email`'s
   convention; `account_id`/`thread_id` also later narrowed out of its
   input in the hardening pass). New tests in
   `tests/test_openclaw_messaging.py` (see the hardening item above and
   this session's final report for exact current counts). **Explicitly
   not done**: no real channel configured/logged into, no real message
   sent, nothing committed or pushed.
1. **OpenClaw M1.5 — real loopback Gateway smoke test** (prior session
   within the same overall OpenClaw initiative, committed as `8502c03`,
   pushed, CI-verified — GitHub Actions run `32073836073`): ran an
   actual `openclaw@2026.7.1-2` process for the first
   time — isolated npm install under `/tmp`, isolated
   `OPENCLAW_STATE_DIR`, loopback-only bind, test token stored via
   `agent/secrets.py`. First attempt (`--dev`) exposed a real isolation
   gap (dev workspace escaped the state-dir override, wrote under the
   real `~/.openclaw`; the `bonjour` plugin broadcast the Gateway on the
   LAN) — caught within ~8 seconds, killed before any Jarvis call,
   cleaned up with explicit user approval. Corrected approach (no
   `--dev`, explicit workspace patch, `plugins.enabled = false`)
   produced a clean isolated Gateway. The real, load-bearing test —
   Jarvis's actual `openclaw_status`/`openclaw_list_nodes` tools via
   `tools.registry.dispatch()` — found and fixed two real bugs
   (`client.platform` required by the real schema but never sent;
   `client.deviceFamily` signed into the payload but never sent on the
   wire, breaking real signature verification). With both fixed: full
   success, `operator.read` only (independently confirmed via
   `openclaw devices list`), empty node list as expected. Cleaned up
   fully afterward: Gateway killed, port freed, ~363MB temp install
   removed, smoke-test-only Keychain secrets (`OPENCLAW_GATEWAY_TOKEN`,
   `OPENCLAW_DEVICE_TOKEN`) deleted, `OPENCLAW_DEVICE_PRIVATE_KEY`
   preserved. 2 new tests (1098 total, up from 1096); the fake test
   server's signature verification was also corrected to reconstruct
   from actual captured wire values instead of duplicate constants.
2. **OpenClaw M1 re-verification #2 — stable compatibility: auth-field
   bug fixed for real, device-ID CONFIRMED** (same session, follow-up to
   item 3 below): re-verification #1 (also this session, folded below)
   had checked a claimed `signedAt` bug against the beta client packages
   and correctly left `signedAt` unchanged, but its OWN fix — sending
   Jarvis's shared `OPENCLAW_GATEWAY_TOKEN` under `auth.bootstrapToken`
   — was itself wrong, based on client-side field *existence* rather
   than the Gateway server's actual field *semantics*. This pass
   re-verified against the actual CURRENT STABLE `openclaw` npm app
   package (`openclaw@2026.7.1-2`, `dist-tags.latest`) rather than the
   separately-published client/protocol packages, which turn out to have
   **no stable npm release at all** — only an intentionally-empty `0.0.0`
   placeholder and prerelease `-beta.N` versions (their own CHANGELOG.md
   says "Publish the reference Gateway WebSocket client for the first
   time" — they were extracted from the main app and published only
   very recently). Downloaded and inspected the stable app's own
   87MB-unpacked bundle directly (`npm pack openclaw@2026.7.1-2`),
   including its **server-side** connect-auth resolution
   (`resolveSharedConnectAuth`, `resolveDeviceTokenCandidate`,
   `resolveConnectAuthDecisionCore`) — the actually-authoritative source
   for wire-field meaning, since a schema only proves a field can exist,
   not what it does. Confirmed: `auth.token` (+ `auth.password`) is
   checked against the Gateway's own configured SHARED secret — this IS
   what `OPENCLAW_GATEWAY_TOKEN` conceptually is. `auth.bootstrapToken`
   is checked via a wholly separate path (`verifyBootstrapToken(deviceId,
   publicKey, token, ...)`) meant for a genuinely distinct device-
   pairing/setup credential Jarvis does not hold — re-verification #1's
   fix incorrectly used this field, now corrected. `auth.deviceToken` is
   checked via a third, separate path (`verifyDeviceToken`), and MUST be
   used (not `auth.token`) for a stored device credential specifically
   because a rejection there reports `AUTH_DEVICE_TOKEN_MISMATCH`
   (`candidateSource === "explicit-device-token"`) — reusing `auth.token`
   would instead surface as `AUTH_TOKEN_MISMATCH`, silently breaking the
   stale-token clear-and-retry logic. Fixed: shared credential → always
   `auth.token`; stored device credential → always `auth.deviceToken`;
   `auth.bootstrapToken` → never populated. Also confirmed, while in the
   stable bundle: the `signedAt` conclusion from re-verification #1 is
   safe regardless of the stable/beta client difference (stable's own
   client uses plain `Date.now()` unconditionally, no `challengeTs`
   concept at all — a real version difference — but the Gateway
   SERVER's actual freshness check is `Math.abs(Date.now() - signedAt) >
   DEVICE_SIGNATURE_SKEW_MS` (120s), a wall-clock skew check against the
   SERVER's own clock, never an exact-match against the challenge's own
   `ts` — so either client behavior is compatible with either server
   version). And: the device-ID derivation, previously flagged as a
   documented low-risk assumption, is now **CONFIRMED** — the stable
   bundle contains a literal `deriveDeviceIdFromPublicKey` function
   (`src/infra/device-identity.ts`) doing exactly `SHA-256(raw 32-byte
   Ed25519 public key).hexdigest()`, and the Gateway server independently
   re-derives and compares this value against the client-claimed
   `device.id` on every connect — an exact match to this bridge's own
   implementation. The "unverified assumption" language has been removed
   from the module docstring, `_load_or_create_device_identity`, and
   this file; `DEVICE_AUTH_DEVICE_ID_MISMATCH` handling is kept as
   defense-in-depth, not because of remaining doubt. 3 net new tests (57
   total in `tests/test_openclaw_gateway.py`, up from 54): correct wire
   field for the shared token, correct wire field for a stored device
   token, a payload/wire-value consistency check (STEP 4: never sign one
   credential while sending another), and a known-answer test for the
   device-ID algorithm (fixed test keypair → fixed expected 64-char hex).
3. **OpenClaw M1 re-verification #1 — signedAt checked, superseded auth
   fix** (same session, folded into item 2 above for the corrected final
   state): a claim surfaced that `device.signedAt` must always be the
   client's current wall-clock time and must never be copied from the
   `connect.challenge` event's own `ts`. Checked directly against a
   freshly re-pulled, newer beta npm release (`2026.8.1-beta.2`) — both
   the CLI/backend `GatewayClient.buildConnectPlan` and the browser
   `GatewayBrowserDeviceAuthLifecycle.buildPlan` real implementations do
   `signedAtMs = challengeTs ?? Date.now()`, the opposite of the claim,
   and exactly what `agent/openclaw_gateway.py` already implemented — so
   no change was made to `signedAt` handling; two regression tests were
   added (`test_signed_at_uses_the_connect_challenge_timestamp_not_wall_clock`,
   `test_signed_at_falls_back_to_wall_clock_when_challenge_omits_timestamp`).
   This same pass's OWN auth-field fix (sending credentials under
   `auth.bootstrapToken`/`auth.deviceToken` based on client-side schema
   field existence alone) was itself incorrect, as item 2 above found
   and corrected.
4. **OpenClaw M1 correction — real Ed25519 device-identity auth**
   (replaces the shared-token-only design from the pass below): the
   original M1 explicitly flagged its auth as an unverified assumption.
   Verified against the actual published `@openclaw/gateway-client` and
   `@openclaw/gateway-protocol` npm packages (downloaded via `npm pack`,
   inspected as real compiled source — docs.openclaw.ai doesn't cover
   this flow, and a GitHub issue claiming to describe it (#17571) had
   its own payload-format claim proven stale — "v1" vs. the real,
   current "v3" — caught only by checking the real package, a genuine
   reason not to trust a single secondary source uncritically). Rebuilt
   `agent/openclaw_gateway.py`: a persistent Ed25519 device identity
   (`OPENCLAW_DEVICE_PRIVATE_KEY`, PEM/PKCS8, via `agent/secrets.py`,
   generated once and reused); the real, verified V3 device-auth payload
   format and Ed25519 signing; a `connect.challenge`-first handshake
   (the original version incorrectly sent `connect` immediately); post-
   `hello-ok` verification that `operator.read` was actually granted,
   **failing closed** otherwise (new `OpenClawScopeError`); a new
   `OpenClawPairingRequired` error for a new/unrecognized device
   identity — **never auto-approved**, a human must run
   `openclaw devices approve <requestId>` themselves; device-token
   persistence/reuse (`OPENCLAW_DEVICE_TOKEN`) with exactly one bounded
   fallback-to-bootstrap-token retry on `AUTH_DEVICE_TOKEN_MISMATCH`,
   mirroring the real client's own verified behavior — never looping
   further. `client.id`/`client.mode` use `"cli"` (the real, closed
   enum's closest legitimate, non-reserved identity — confirmed via the
   actual `client-info.mjs` source; `"backend"`/`"gateway-client"` are
   OpenClaw's own reserved internal identity and are never used, per
   explicit instruction). New dependency: `cryptography==50.0.0` —
   verified to build and work on Intel macOS + Python 3.14 (no
   pre-built wheel existed for this exact combination yet; compiles
   from source cleanly). **At this point in the session, one residual,
   explicitly-flagged assumption remained** (since CONFIRMED — see item 1
   at the top of this list): the exact device-ID hash algorithm (SHA-256
   of the raw Ed25519 public key) could not yet be confirmed against any
   primary source — genuinely not part of either published beta package,
   which both expose key generation/signing only as an injected
   dependency (stubbed as a no-op in the default export); the real
   implementation turned out to live inside the main `openclaw`
   application's own bundle, not the separately-published packages
   inspected at this stage. Deliberately low-risk if wrong: the real
   Gateway has a dedicated error code for exactly that case
   (`DEVICE_AUTH_DEVICE_ID_MISMATCH`), handled as a clean
   `OpenClawAuthError`, never a crash. The fake Gateway test server was
   rewritten to perform genuine Ed25519 signature verification against
   the real client's actual output (reconstructing the exact payload
   with the module's own real builder function, then
   `Ed25519PublicKey.verify()`), not a "signature is non-empty" stub
   check. 15 new tests (51 total in `tests/test_openclaw_gateway.py`,
   up from 36). The RPC allowlist, `operator.read`-only scope ceiling,
   and the two tools (`openclaw_status`/`openclaw_list_nodes`) are
   unchanged from the original M1 pass.
5. **OpenClaw M1 v1 — read-only Gateway bridge, shared-token auth**
   (same session, superseded by items 2-4 above, kept here for full
   session history): `agent/openclaw_gateway.py` (new), a fixed RPC
   allowlist (`health`/`status`/`node.list` only), normalized errors,
   two tools (`openclaw_status`/`openclaw_list_nodes`,
   `tools/schemas/openclaw.py`, both permission_level 0, read-only).
   `websockets==16.1.1` added (was already an incidental transitive
   dependency of `streamlit`, now pinned directly). 51 tests at this
   stage (36 + 15 across the two new test files).
6. **OpenClaw M0 — research/architecture audit** (no code changes) —
   see `CHANGELOG.md`/`ROADMAP.md` for the full finding list (Intel
   macOS support, loopback-WebSocket local default, protocol version 4,
   the 7-scope operator model, unsandboxed plugin execution).

## What is partially completed

Nothing mid-implementation. OpenClaw M2 is complete and fully tested;
the only thing not done is the user's review/commit decision, plus
choosing and configuring a first real messaging channel afterward.

## Current bugs / known issues

None remaining. The two real bugs M1.5's smoke test found
(`client.platform`, `client.deviceFamily` both missing from the wire
`connect` params) are fixed and verified against a real Gateway — see
item 1 in "What was completed" above. The device-ID hash algorithm
(flagged in a prior session) remains CONFIRMED against real primary
source. No other open issues.

## Current blockers

None technical. OpenClaw M1/M1.5/M2 and Graphify G0/G1/G1.1 are all
committed. Open decisions (none urgent): which real messaging channel
(if any) to configure for OpenClaw next, and whether/when to pursue a
further Graphify milestone (MCP/hooks/auto-rebuild — none implemented
or assumed so far). Next major planned milestone is Phase 9 / M4
(Conversation & History Intelligence) — not started.

## Recent architectural decisions

- **A separate device identity per scope tier, not a scope upgrade of
  an existing one.** OpenClaw M2 needs `operator.write` for `send`; M1's
  device identity holds only `operator.read`. Rather than request a
  broader scope on the same credential, M2 holds its own Ed25519
  keypair/device token entirely — a compromised read credential can
  never send a message through Jarvis. The reverse claim needs more
  care: a compromised messaging credential cannot escalate *through
  Jarvis* (its RPC allowlist is exactly `{send}`), but the real
  Gateway's own server-side scope semantics are asymmetric
  (`operator.write` already satisfies an `operator.read` check there),
  so it is not accurate to claim the credential itself is
  cryptographically incapable of read authority — see the hardening-
  pass entry in CHANGELOG.md for the full three-way distinction.
  Implemented as a small, closed `_Profile` type
  (`agent/openclaw_gateway.py`) with exactly two fixed instances, now
  enforced by identity (`is`, not `==`) in `_call()` so a forged
  `_Profile` with copied field values is still rejected; there is no
  public API to construct a third or request an arbitrary scope list.
- **`send`, never `chat.send` — verified against real source, not just
  followed on instruction.** The Gateway's real `chat.send` RPC requires
  a `sessionKey` and is part of OpenClaw's own agent/session execution
  surface; using it for Jarvis's outbound messages would mean an
  OpenClaw agent loop processes them, exactly the architectural blurring
  this project avoids. Confirmed directly against
  `openclaw@2026.7.1-2`'s compiled server source that `send` is a
  genuinely separate, simpler RPC method with no session/agent concept
  at all.
- **A retry that looked verified-safe, then wasn't, on closer review.**
  Before implementing any automatic retry for a side-effecting `send`
  call, the real Gateway's idempotency-cache behavior was read directly
  from its compiled server source (a real, in-memory, 5-minute-TTL,
  `idempotencyKey`-keyed cache that replays cached results on a repeat
  key) — a one-time same-key retry was then implemented on that basis.
  A subsequent hardening/review pass identified the gap: that cache is
  in-memory and single-process, so it does not survive a Gateway
  process restart. If the Gateway delivers the message and then dies/
  restarts before Jarvis receives the response, a same-key resend is no
  longer provably deduplicated and could send a real duplicate. The
  automatic retry was removed entirely as a result — for an external,
  user-visible side effect, correctness beats speculative automatic
  recovery of an ambiguous outcome. Lesson: verifying a mechanism is
  real is not the same as verifying it's durable enough for the
  specific safety property being relied on — both need checking.
- **Source-reading and a careful local fake server are not a substitute
  for testing against the real thing at least once.** M1.5's real
  loopback Gateway smoke test found two real bugs (`client.platform`,
  `client.deviceFamily` missing from the wire) that survived every prior
  source-reading pass and the entire local fake-server test suite,
  because the fake server's own signature verification reconstructed
  payloads from expected constants rather than the actual captured wire
  values. Fixed both the bugs and the fake server's fidelity gap that
  let them through.
- **A temporary test harness's own isolation claims still need
  verification, not just trust.** OpenClaw's documented
  `OPENCLAW_STATE_DIR` override, combined with `--dev`, did not fully
  isolate a real Gateway process from the user's real `~/.openclaw` —
  the dev workspace path escaped it, and the default plugin set
  (including `bonjour`) broadcast the test Gateway on the LAN. Caught
  within seconds by checking the Gateway's own log output rather than
  assuming the isolation worked; not a Jarvis security issue (the
  WebSocket bind itself was loopback-only throughout), but a reminder to
  verify a test environment's actual behavior, not just its documented
  behavior. Future temporary OpenClaw test harnesses must: never use
  `--dev`; explicitly patch the workspace path in addition to setting
  `OPENCLAW_STATE_DIR`; set `plugins.enabled = false`; verify the
  listener's real bind address before connecting; verify no `~/.openclaw`
  writes occurred; skip normal onboarding entirely; and delete temporary
  Gateway/device-token secrets afterward (a stored device token tied to
  deleted Gateway state is worse than no token — it looks configured but
  isn't valid).
- **A user-supplied "bug report" was checked against primary source
  before being applied, and the code was NOT changed when the source
  contradicted it** — a claim that `device.signedAt` must never come
  from `connect.challenge`'s own `ts` was checked against a freshly
  re-pulled, newer beta npm release and found to be the opposite of
  real, current client behavior (real clients prefer the challenge
  timestamp). No code change was made; two regression tests were added
  instead to guard the current, verified-correct behavior. This
  project's standing rule to verify security-critical protocol details
  against primary source, not memory or a single claim, applies
  symmetrically — to a user's own claim, not only to secondary sources
  like GitHub issues.
- **A schema proving a field CAN exist is not proof of what it MEANS —
  the server's own field-interpretation logic is authoritative for wire
  compatibility, not client-side schema/field existence.** The first
  auth-field fix this session (based on the beta client packages'
  `ConnectParams.auth` schema having distinct `token`/`bootstrapToken`/
  `deviceToken` fields) sent Jarvis's shared `OPENCLAW_GATEWAY_TOKEN`
  under `auth.bootstrapToken` — which compiled, matched the schema, and
  was still wrong: the real Gateway SERVER's own connect-auth resolution
  (read directly from `openclaw@2026.7.1-2`'s compiled server source)
  shows `auth.bootstrapToken` is verified via a wholly separate
  `verifyBootstrapToken(deviceId, publicKey, token, ...)` path meant for
  a genuinely distinct device-pairing/setup credential — not what a
  plain shared Gateway secret is. Corrected: shared token → `auth.token`
  (checked via `resolveSharedConnectAuth`); stored device token →
  `auth.deviceToken` (checked via `verifyDeviceToken`, and required —
  not merely preferred — because only that field's rejection reports
  `AUTH_DEVICE_TOKEN_MISMATCH`, which this bridge's stale-token
  clear-and-retry logic depends on); `auth.bootstrapToken` never used.
- **The separately-published `@openclaw/gateway-client`/`@openclaw/
  gateway-protocol` npm packages have no stable release at all** — only
  an empty `0.0.0` placeholder and beta prereleases (confirmed via
  `npm view <pkg> versions`; their own CHANGELOG.md says they were
  "published for the first time" very recently, extracted out of the
  main app). For a genuine stable-compatibility check, the real source
  of truth is the main `openclaw` app's own stable npm release
  (`openclaw@2026.7.1-2`, `dist-tags.latest`), which vendors its own
  (slightly older) copy of this same logic directly in its ~87MB
  bundle — downloaded via plain `npm pack` and inspected directly, same
  as the beta packages were.
- **Device-ID derivation is now CONFIRMED, not assumed** — the stable
  app bundle contains a literal `deriveDeviceIdFromPublicKey` function
  doing exactly `SHA-256(raw 32-byte Ed25519 public key).hexdigest()`,
  and the Gateway server independently re-derives and compares this
  against the client-claimed `device.id` on every connect. This matches
  this bridge's implementation exactly. The "unverified assumption"
  framing has been removed from the module docstring and this file;
  `DEVICE_AUTH_DEVICE_ID_MISMATCH` handling is kept as defense-in-depth
  regardless, not because of remaining doubt.
- **Device-identity auth was verified against real published packages,
  not guessed from docs or a community issue** — the single most
  important process decision this session: when docs.openclaw.ai didn't
  cover third-party device auth and a GitHub issue claiming to describe
  it had an already-provably-stale detail (payload format version), the
  response was to download and directly inspect the actual npm packages
  rather than proceed on an unverified secondary source for a security-
  critical protocol detail. This same discipline — inspect the actual
  package/bundle rather than trust a schema, a claim, or a secondary
  source — is what caught both auth-field bugs and confirmed the
  device-ID algorithm this session.
- **`"cli"` is Jarvis's OpenClaw client identity, not `"jarvis"`** — the
  original M1 pass used a free-text `"jarvis"` client.id, which the real
  Gateway would have rejected outright (client.id/client.mode are
  validated against a closed enum, confirmed via real source). Jarvis's
  actual identification goes in the free-text `deviceFamily` field
  instead.
- **Fail closed on scope, never on authentication success alone** — a
  connection that authenticates but isn't granted `operator.read` is
  treated as a failure (`OpenClawScopeError`), not a degraded success.
- **Pairing is a human operation, never automated** — `PAIRING_REQUIRED`
  normalizes to a clean, safe result; Jarvis has no pairing-approval
  tool and never will in M1's scope.
- (Carried over from the M1 v1 pass, still true) OpenClaw is optional
  subordinate infrastructure, never a second orchestrator; a fixed RPC
  allowlist, not a blocklist; synchronous WebSocket client to fit
  Jarvis's existing tool architecture; one-shot connections, no
  persistent connection manager; zero third-party OpenClaw plugin
  dependency; OpenClaw M2 (messaging, `operator.write`) is next, not
  started.
- (Carried over, still true) `MAX_AGENT_DEPTH = 1`, real coworker-agent
  execution goes through `execute_agent()` (subprocess-isolated).
  `agent/quiet_mode.py` remains the one shared suppression mechanism.
  Root `ARCHITECTURE.md` is authoritative over `docs/ARCHITECTURE.md`.

## Files recently modified

**OpenClaw M1** committed as `d1eb813` ("Add read-only OpenClaw gateway
bridge"), pushed to `origin/main`, CI-verified.

**OpenClaw M1.5** committed as `8502c03` ("Fix OpenClaw bridge against
real Gateway"), pushed, CI-verified (GitHub Actions run `32073836073`).

**OpenClaw M2 + hardening pass** committed as `d270dc4` ("Add
permission-gated OpenClaw outbound messaging"), pushed, CI-verified
(GitHub Actions run `32310485314`):
```
new:      agent/openclaw_messaging.py
new:      tests/test_openclaw_messaging.py
modified: agent/openclaw_gateway.py (profile abstraction enforced by
          identity; _send_raw (renamed private);
          OpenClawUncertainDelivery, no longer auto-retried)
modified: agent/verification.py (new _verify_send_message_via_openclaw,
          registered in _VERIFIERS)
modified: config/settings.py (openclaw_messaging_enabled,
          openclaw_allowed_channels, openclaw_allowed_targets)
modified: tools/schemas/openclaw.py (send_message_via_openclaw, no
          account_id/thread_id)
modified: tests/test_openclaw_gateway.py (profile abstraction;
          TestSecurityAllowlist restructured; new
          TestProfileIdentityEnforcement for forged-profile rejection)
modified: tests/test_openclaw_tool.py (new tool registration/dispatch
          tests)
modified: tests/test_verification.py (new verifier tests)
modified: ROADMAP.md, ARCHITECTURE.md, CHANGELOG.md, SESSION_LOG.md,
          HANDOFF.md
```

**Graphify G0** committed as `7b4d0b6` ("Document local Graphify
development integration"), pushed, CI-verified (GitHub Actions run
`32312885286`):
```
new:      docs/GRAPHIFY.md
modified: .gitignore (graphify-out/ entry)
modified: ROADMAP.md, CHANGELOG.md, SESSION_LOG.md, HANDOFF.md
```

**Graphify G1** committed as `c99e792` ("Add read-only Jarvis code
graph intelligence"), pushed, CI-verified (GitHub Actions run
`32418542989`):
```
new:      agent/code_graph.py
new:      tools/schemas/graphify.py
new:      tests/test_code_graph.py
new:      tests/test_graphify_tools.py
modified: tools/schemas/__init__.py (registers the new graphify module)
modified: docs/GRAPHIFY.md (G1 section)
modified: ARCHITECTURE.md (tool count/list, agent/code_graph.py paragraph)
modified: ROADMAP.md, CHANGELOG.md, SESSION_LOG.md, HANDOFF.md
```

**Graphify G1.1** (this session — documentation only; confirm current
state with a live `git status`):
```
modified: docs/GRAPHIFY.md (incremental-extraction limitation section,
          precision rebuild workflow)
modified: ROADMAP.md, CHANGELOG.md, SESSION_LOG.md, HANDOFF.md
```
No `.py` file, `requirements.txt`, `.gitignore`, `CLAUDE.md`, or
generated `graphify-out/` content touched. The live local graph itself
was replaced with a clean full rebuild as part of this investigation,
but that's ignored local data, not a tracked-file change.

**Committed history**, most recent first: `c99e792` (Graphify G1),
`7b4d0b6` (Graphify G0), `d270dc4` (OpenClaw M2 + hardening pass),
`8502c03` (OpenClaw M1.5), `f370c00` (M1 handoff doc update), `d1eb813`
(OpenClaw M1), `4265f55` (Phase 9 Milestone 3), `8d4da44` (Phase 9
Milestone 2), `7b67bf0` (Phase 9 Milestone 1), `d0f791c` (Obsidian
vault integration), `d3481fc` (Phase 9 Milestone 0 — GitHub Actions
CI). See `CHANGELOG.md` / `git log` for full history.

## Tests recently run and their results

`python -m unittest discover -s tests` → **1253 passed, 0 failed**
(unchanged since the Graphify G1 commit — G1.1 is documentation-only,
so this pass didn't need or trigger a regression). No paid API calls;
no real OpenClaw installation used; no LLM/API calls during Graphify's
code-only graph rebuilds; no real `graphifyy`/`graphify` CLI needed by
any test (synthetic fixtures only — the real CLI was used manually
outside the test suite for the G1.1 audit and rebuild). This number
will be stale the moment new tests are added — re-run, don't trust it
blindly.

## What still needs to be done

1. **Nothing outstanding for OpenClaw M1, M1.5, or M2** — all committed
   (`d1eb813`, `8502c03`, `d270dc4`), pushed to `origin/main`,
   CI-verified.
2. **Nothing outstanding for Graphify G0, G1, or G1.1** — all committed
   (`7b4d0b6`, `c99e792`, and this pass's commit), documented in
   `docs/GRAPHIFY.md`.
3. **Do not rebuild the real local graph reflexively** — after this
   docs commit lands, `code_graph_status` will correctly report the
   graph `stale` again purely because HEAD moved past the graph's
   `built_at_commit` (`c99e792`). This is expected; refresh it only when
   actually useful before code-graph analysis on a future milestone.
4. **Do not configure a real OpenClaw messaging channel** (Telegram/
   Discord/WhatsApp/Slack/Signal/iMessage/...) until a specific channel
   is explicitly chosen.
5. **Do not start Graphify G2** (MCP, hooks, auto-rebuild — none
   implemented or assumed), OpenClaw device capabilities, OpenClaw
   agent/model-routing integration, or Phase 9 / M4 (Conversation &
   History Intelligence) until the user explicitly says so.

## Exact recommended next steps

For the next session, in order of what's most likely to matter:

1. Re-verify this file against actual git state first (per `CLAUDE.md`'s
   NEW SESSION PROTOCOL) — confirm `git log`/`git status`/test count
   match what this file claims before trusting it.
2. Before relying on `analyze_code_impact`/`find_code_path` results for
   anything precision-sensitive, check `code_graph_status` first — it
   will likely report `stale` (HEAD has moved past the graph's
   `built_at_commit` since this docs commit). If a rebuild is actually
   useful at that point, use the precision workflow in
   `docs/GRAPHIFY.md`'s "Known incremental-extraction limitation"
   section (clean tracked tree → back up the old graph outside the repo
   → re-extract from empty → validate → delete the backup) rather than
   a bare incremental `graphify extract . --code-only`.
3. The next major planned milestone is **Phase 9 / M4 — Conversation &
   History Intelligence** — not started, not scoped in detail yet in
   this file; treat it as a fresh planning conversation with the user
   rather than assuming any prior scope.
4. If the user wants to proceed with configuring a real OpenClaw
   messaging channel, that is a real, separate, higher-risk step (real
   external service credentials, a real live send) and should get the
   same explicit-approval treatment every OpenClaw milestone so far has
   had.
5. If a compatibility check against a newer OpenClaw release (beyond
   `2026.7.1-2`) becomes useful, the M1.5 real-Gateway smoke-test
   approach documented in `ARCHITECTURE.md`'s "Real-Gateway smoke-test
   isolation" note is the one to reuse (no `--dev`, explicit workspace
   patch, `plugins.enabled = false`) — offer this only with explicit
   confirmation, matching this project's standing real-API-cost/
   real-external-service sensitivity.

## Important context that would otherwise be lost

- **A security-critical assumption was caught and corrected within the
  same overall initiative, before being committed** — the original M1
  pass was explicit about its own uncertainty ("a documented assumption,
  not verified"), which is exactly what made the follow-up correction
  possible: the gap was documented, not hidden, so it could be found and
  fixed before ever reaching a real Gateway.
- **A community-sourced technical claim (GitHub issue #17571) was
  independently caught being partly wrong** during this session's own
  verification effort (stale payload-format version) — a concrete
  reminder that secondary sources, even ones that look authoritative
  (filed against the official repo, technically detailed), need
  independent verification for security-critical protocol details, not
  just for OpenClaw specifically.
- **OpenClaw is a real, external, independently-evolving project** —
  re-verify current docs/package versions before extending the bridge
  in a future session, don't assume this session's research stays
  accurate indefinitely.
- **Real API cost is a standing user concern** — OpenClaw M1 made zero
  live network calls of any kind; `npm pack` downloads used for
  verification are free, public package metadata/tarballs, not paid API
  calls.
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
