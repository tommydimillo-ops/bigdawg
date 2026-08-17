# HANDOFF — Jarvis current state

**Read this after `CLAUDE.md`.** This file is the single source of truth
for "what's going on right now" — it will drift out of date faster than
the other docs; if anything here contradicts the actual code or git
state, trust the code (see `CLAUDE.md`'s NEW SESSION PROTOCOL) and fix
this file.

Last updated: 2026-08-17, a session that continued from Phase 9
Milestone 3 (committed, pushed, CI-verified as `4265f55`) into a new,
separate initiative — OpenClaw interoperability. OpenClaw M1 (the
read-only Gateway bridge, across five internal passes documented in
prior CHANGELOG.md entries) is committed as `d1eb813`, pushed, and
CI-verified (GitHub Actions run `31963970515`). This session then ran
**OpenClaw M1.5 — a real loopback Gateway smoke test**: installed an
actual `openclaw@2026.7.1-2` process (isolated, temporary, never a
daemon), and called Jarvis's real `openclaw_status`/`openclaw_list_nodes`
tools against it through the normal `tools.registry.dispatch()` path.
This found and fixed two real bugs (`client.platform` and
`client.deviceFamily` both required on the wire, not just in the signed
payload — see CHANGELOG.md's M1.5 entry for full detail) that neither
extensive source-reading nor the local fake test server had caught.
With both fixed, the real smoke test succeeded end to end. **M1.5's
fixes are verified and awaiting this session's finalization commit.**

## OpenClaw M1 — READ-ONLY GATEWAY BRIDGE ✅ COMPLETE (M1.5 real-Gateway-verified)

- **M1 commit**: `d1eb8130609d03e0f4f68a3f2cc46c4e3d66ade2` (pushed,
  CI-verified, GitHub Actions run `31963970515`)
- **M1.5 status**: real-Gateway smoke test passed; two bug fixes
  verified locally (1098/1098 tests) but not yet committed as of this
  writing — confirm with `git status` whether this session's
  finalization commit has landed since this was written.
- **Current OpenClaw capabilities**: an optional, disabled-by-default,
  read-only Gateway bridge — authenticated loopback WebSocket, stable
  compatibility target `openclaw@2026.7.1-2` (now verified against an
  actual running instance of that exact version, not just its source),
  protocol version 4, a persistent Jarvis Ed25519 device identity, a
  human-only pairing flow, `operator.read`-only scope, a fixed RPC
  allowlist (`health`/`status`/`node.list`), and two Jarvis tools
  (`openclaw_status`/`openclaw_list_nodes`).

**Security boundaries**: Jarvis remains the sole orchestrator; no raw
RPC; no `node.invoke`; no `operator.write`/admin/pairing scope; no
automatic pairing approval by Jarvis (the real Gateway auto-approved
pairing itself in the M1.5 dev/loopback test configuration — a real
OpenClaw default for that config shape, not anything Jarvis did); no
third-party OpenClaw plugins; no OpenClaw model-routing authority; no
OpenClaw memory authority; no shared secrets store.

**Automated vs. real-world testing**: M1 has extensive mocked and
local-fake-Gateway protocol tests (real Ed25519 signature verification
against a genuine local WebSocket server, never a stub), **and** as of
M1.5, has now also been verified against a real, running
`openclaw@2026.7.1-2` process (temporary, isolated, removed afterward —
no OpenClaw installation persists on this machine). OpenClaw M2
(messaging) is **not implemented**. Device capabilities are **not
implemented**. Phase 9 Milestone 4 (FTS5) remains deferred until after
the OpenClaw work currently planned.

## Current project status

**Phase 9**: Milestones 0-3 complete, committed, pushed — HEAD `4265f55`
on `origin/main` at the time, CI-verified (GitHub Actions run
`31950985587`, `success`). Milestone 4 (FTS5) not started, sequenced
after OpenClaw.

**OpenClaw** (a separate, real, independently-developed open-source
project — github.com/openclaw/openclaw, docs.openclaw.ai — not a Jarvis
subsystem): **M0** complete, approved, no code. **M1** (read-only
Gateway bridge with real Ed25519 device-identity authentication) is
**complete, committed (`d1eb813`), pushed, and CI-verified**. **M1.5**
(real loopback Gateway smoke test) passed, with two real bug fixes
verified locally, pending this session's finalization commit — see the
section above and CHANGELOG.md's M1.5 entry for full detail.

**Working tree status depends on whether this session's M1.5
finalization commit has landed** — confirm with a live `git status`/
`git log` rather than trusting this file. **1098 tests pass,
0 failures** as of the M1.5 fixes (1096 at M1's own commit; 1093 before
the stable-compatibility pass + 3 new; 1090 before the beta
re-verification pass; 1075 before the device-auth correction pass; 1024
before OpenClaw M1 first landed), no live/paid API calls during testing.
A real, temporary `openclaw@2026.7.1-2` process WAS installed and run
during M1.5's smoke test, then fully removed — no OpenClaw installation
persists on this machine as of this writing.

## What we are currently building

Nothing actively mid-task — OpenClaw M1.5's real-Gateway fixes are
complete and tested; this session's remaining work is documentation +
the finalization commit/push/CI-verify sequence. The user has not yet
said whether to start OpenClaw M2 (messaging) or Phase 9 Milestone 4
(FTS5) next; both are explicitly deferred until asked for.

## What was completed (this session, most recent first)

0. **OpenClaw M1.5 — real loopback Gateway smoke test** (new session,
   2026-08-17, follows M1's own commit/push/CI-verify from the prior
   session): ran an actual `openclaw@2026.7.1-2` process for the first
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
1. **OpenClaw M1 re-verification #2 — stable compatibility: auth-field
   bug fixed for real, device-ID CONFIRMED** (same session, follow-up to
   item 2 below): re-verification #1 (also this session, folded below)
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
2. **OpenClaw M1 re-verification #1 — signedAt checked, superseded auth
   fix** (same session, folded into item 1 above for the corrected final
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
   field existence alone) was itself incorrect, as item 1 above found
   and corrected.
3. **OpenClaw M1 correction — real Ed25519 device-identity auth**
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
4. **OpenClaw M1 v1 — read-only Gateway bridge, shared-token auth**
   (same session, superseded by items 1-3 above, kept here for full
   session history): `agent/openclaw_gateway.py` (new), a fixed RPC
   allowlist (`health`/`status`/`node.list` only), normalized errors,
   two tools (`openclaw_status`/`openclaw_list_nodes`,
   `tools/schemas/openclaw.py`, both permission_level 0, read-only).
   `websockets==16.1.1` added (was already an incidental transitive
   dependency of `streamlit`, now pinned directly). 51 tests at this
   stage (36 + 15 across the two new test files).
5. **OpenClaw M0 — research/architecture audit** (no code changes) —
   see `CHANGELOG.md`/`ROADMAP.md` for the full finding list (Intel
   macOS support, loopback-WebSocket local default, protocol version 4,
   the 7-scope operator model, unsandboxed plugin execution).

## What is partially completed

Nothing mid-implementation. OpenClaw M1.5's real-Gateway bug fixes are
complete and fully tested; the only thing not done as this file was
written is this session's own finalization commit/push/CI-verify
sequence (in progress in the same session that wrote this update).

## Current bugs / known issues

None remaining. The two real bugs M1.5's smoke test found
(`client.platform`, `client.deviceFamily` both missing from the wire
`connect` params) are fixed and verified against a real Gateway — see
item 0 in "What was completed" above. The device-ID hash algorithm
(flagged in a prior session) remains CONFIRMED against real primary
source. No other open issues.

## Current blockers

None technical. This session's M1.5 finalization commit/push/CI-verify
sequence may or may not have completed depending on when this file is
read — confirm with `git status`/`git log`.

## Recent architectural decisions

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

**OpenClaw M1.5** (this session, being finalized — check `git status`
for current state; by the time this finalization commit lands, expect
it as one new commit):
```
modified: agent/openclaw_gateway.py (client.platform/deviceFamily fix)
modified: tests/test_openclaw_gateway.py (2 new regression tests, fake
          server signature verification now uses captured wire values)
modified: ROADMAP.md
modified: ARCHITECTURE.md
modified: CHANGELOG.md
modified: SESSION_LOG.md
modified: HANDOFF.md (this file)
```

**Committed history**, most recent first: `d1eb813` (OpenClaw M1),
`4265f55` (Phase 9 Milestone 3), `8d4da44` (Phase 9 Milestone 2),
`7b67bf0` (Phase 9 Milestone 1), `d0f791c` (Obsidian vault integration),
`d3481fc` (Phase 9 Milestone 0 — GitHub Actions CI). See
`CHANGELOG.md` / `git log` for full history.

## Tests recently run and their results

`python -m unittest discover -s tests` → **1098 passed, 0 failed** (run
at the end of this session, after the M1.5 real-Gateway bug fixes). No
paid API calls. Both mocked/local-fake-Gateway tests AND a real,
temporary `openclaw@2026.7.1-2` process were used this session — the
real process was fully removed afterward, no OpenClaw installation
persists on this machine. This number will be stale the moment new
tests are added — re-run, don't trust it blindly.

## What still needs to be done

1. **Nothing outstanding for OpenClaw M1 itself** — committed (`d1eb813`),
   pushed to `origin/main`, CI-verified (GitHub Actions run
   `31963970515`, event=push, conclusion=success; unittest step:
   completed/success).
2. **OpenClaw M1.5** (real-Gateway smoke test + two bug fixes): verified
   locally; confirm with `git log`/`git status` whether this session's
   own finalization commit/push/CI-verify has completed.
3. **Do not start OpenClaw M2** (messaging, `operator.write`) until the
   user says so.
4. **Do not start Phase 9 Milestone 4** (FTS5) until OpenClaw work is
   complete or explicitly deprioritized.
4. **Done** — the real-Gateway smoke test (M1.5) has now run
   successfully (see item 0 in "What was completed" above). A future
   session could re-run one against a newer OpenClaw release if useful,
   but that would be validating a different compatibility target, not
   filling a gap in this one.

## Exact recommended next steps

For the next session, in order of what's most likely to matter:

1. Re-verify this file against actual git state first (per `CLAUDE.md`'s
   NEW SESSION PROTOCOL) — confirm `git log`/`git status` show the M1.5
   finalization commit landed, in sync with `origin/main`, and that the
   test suite still passes (expect 1098+).
2. If the user wants to proceed with OpenClaw M2 (messaging), that is a
   real scope increase (`operator.write`) and should get the same
   explicit-approval treatment M1/M1.5 themselves did.
3. If a compatibility check against a newer OpenClaw release (beyond
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
