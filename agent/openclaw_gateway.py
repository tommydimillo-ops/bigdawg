"""OpenClaw M1 -- a narrow, optional, READ-ONLY bridge to a local OpenClaw
Gateway (docs.openclaw.ai/gateway/protocol, current stable release
`openclaw 2026.7.1-2`, protocol version 4). This module owns connection,
device-identity authentication, protocol negotiation, one allowlisted
RPC call, and normalized errors -- nothing else. It makes NO Jarvis
policy decisions: no tool-permission logic, no model routing, no
autonomy/confirmation checks. Every result this module returns still
flows through the ordinary tool-registry path (tools/schemas/openclaw.py's
ToolSpecs) exactly like any other tool.

DEVICE-AUTH VERIFICATION (2026-08-16): third-party Gateway client device
authentication is genuinely undocumented on docs.openclaw.ai (confirmed
directly -- docs.openclaw.ai/gateway/security does not cover it) and
OpenClaw's own GitHub issue #17571 ("[Docs]: Third-party client
authentication guide") confirms this gap officially. Rather than trust
that issue's own technical claims at face value (its payload-format
claim turned out to cite a stale "v1" scheme; the real, current scheme
is "v3" -- see below), this implementation was verified against the
actual PUBLISHED, OFFICIAL npm packages: `@openclaw/gateway-client` and
`@openclaw/gateway-protocol` (both pulled via `npm pack` and inspected
directly, not paraphrased). Confirmed from that real source, verbatim:
  - The exact V3 device-auth payload string format (see
    _build_device_auth_payload_v3 below), from `device-auth.ts`'s real
    `buildDeviceAuthPayloadV3`.
  - The exact `connect.challenge` event shape (nonce + optional ts) and
    that a client MUST wait for it before sending anything containing
    device-auth material, from `protocol-client.handshake.test.ts`'s
    real tests.
  - The complete, real `ConnectErrorDetailCodes` enum (PAIRING_REQUIRED,
    AUTH_SCOPE_MISMATCH, AUTH_TOKEN_MISMATCH, AUTH_DEVICE_TOKEN_MISMATCH,
    every DEVICE_AUTH_* code, etc.), from `connect-error-details.mjs`.
  - The real, closed `GATEWAY_CLIENT_IDS`/`GATEWAY_CLIENT_MODES` enums
    (client.id/client.mode are NOT free text -- the Gateway validates
    them against this fixed set), from `client-info.mjs`. `"backend"`
    mode and the `"gateway-client"` id are OpenClaw's own reserved
    internal identity and are never used here, per explicit instruction.
    This bridge uses `"cli"` for both -- the closest legitimate,
    non-reserved identity for a non-interactive, programmatic
    integration -- and puts Jarvis's own honest identification in the
    free-text `deviceFamily` field instead (validated fields carry
    Gateway policy; free-text fields carry human-facing identity).
  - That the actual Ed25519 signing/key-generation/device-identity-
    persistence implementation is genuinely NOT part of either published
    package -- both expose it only as an injected dependency
    (`deps.signDevicePayload`, `deps.loadOrCreateDeviceIdentity`, both
    stubbed as no-ops in the published default), confirmed by reading
    the real compiled `index.mjs` of both packages directly. That
    implementation lives inside the main `openclaw` application's own
    unpublished source, not in the packages this project can install.

DEVICE-ID DERIVATION -- CONFIRMED (2026-08-16, see STABLE COMPATIBILITY
VERIFICATION below): previously an unverified assumption; now confirmed
against the real, current STABLE `openclaw` app package's own compiled
`src/infra/device-identity.ts` (`deriveDeviceIdFromPublicKey`), and
against the real Gateway SERVER's own independent re-derivation-and-
compare check on every connect (`message-handler`'s `derivedId =
deriveDeviceIdFromPublicKey(device.publicKey); if (!derivedId ||
derivedId !== device.id) reject(...)`). The real algorithm is exactly
SHA-256 of the raw 32-byte Ed25519 public key, lowercase hex-encoded --
identical to this module's `hashlib.sha256(raw_public_key).hexdigest()`.
DEVICE_AUTH_DEVICE_ID_MISMATCH handling (`_raise_for_error` below) is
kept regardless, as defense-in-depth against a future protocol change,
not because this is still in doubt.

RE-VERIFICATION #1 (2026-08-16, against the newer `2026.8.1-beta.2` npm
release, more recent than the one originally inspected above): a claim
surfaced that `device.signedAt` must always be the client's current wall
clock and must never be the `connect.challenge` event's own `ts`. This
was checked directly against `@openclaw/gateway-client`'s real,
freshly-pulled `dist/index.mjs` (`GatewayClient.buildConnectPlan`,
`packages/gateway-client/src/client.ts`) AND the browser client's real
`GatewayBrowserDeviceAuthLifecycle.buildPlan`
(`dist/session-subscriptions-*.mjs`, `packages/gateway-client/src/
browser-device-auth.ts`). Both real implementations do the OPPOSITE of
that claim: `signedAtMs = challengeTs ?? Date.now()` -- the challenge's
own `ts` is used whenever the Gateway provides one, and current time is
only a fallback for a challenge that omits it. The CLI/backend client
additionally throws ("gateway connect challenge timestamp invalid")
if a device identity is configured and the challenge has no `ts` at all
-- i.e. real clients PREFER the challenge timestamp, not the reverse.
This module's `_connect_and_call` already implements exactly that
(`int(challenge_ts) if challenge_ts is not None else
int(time.time() * 1000)`), so no change was made here; the claim was not
applied.

RE-VERIFICATION #2 -- STABLE COMPATIBILITY (2026-08-16, against the
actual CURRENT STABLE `openclaw` npm app package, `openclaw@2026.7.1-2`,
`dist-tags.latest`): `@openclaw/gateway-client`/`@openclaw/gateway-
protocol` have NO stable (non-prerelease) npm release at all -- their
`latest` dist-tag points to an intentionally-empty `0.0.0` reserved
placeholder, and every real version published is a `-beta.N` prerelease
(confirmed via `npm view <pkg> versions`); their own CHANGELOG.md says
"Publish the reference Gateway WebSocket client for the first time" --
i.e. these packages were extracted from the main app and published only
very recently, beta-only so far. RE-VERIFICATION #1's beta-package
findings are therefore prerelease evidence, not the compatibility
baseline. The main `openclaw` app package IS published as a real stable
release and vendors its own (slightly older, pre-extraction) copy of
this same client/server logic directly in its bundle -- downloaded via
`npm pack openclaw@2026.7.1-2` (87MB unpacked) and inspected directly.
Findings, compared against the beta packages:
  - `signedAt`: the STABLE client's own `assembleConnectParams` computes
    `signedAtMs = Date.now()` UNCONDITIONALLY -- `challengeTs` does not
    exist anywhere in the stable bundle at all (grepped the entire
    extracted `dist/`, zero matches). The `challengeTs`-preferred
    behavior is a genuinely newer addition that only exists in beta.
    This does NOT make RE-VERIFICATION #1's "keep as-is" conclusion
    wrong, though: the real Gateway SERVER's own signature-freshness
    check (`message-handler`'s real, compiled source) is
    `Math.abs(Date.now() - device.signedAt) > DEVICE_SIGNATURE_SKEW_MS`
    (`DEVICE_SIGNATURE_SKEW_MS = 120_000`, i.e. a plain +/-2-minute
    wall-clock skew check against the SERVER's own clock) -- it never
    compares `signedAt` to the challenge's own `ts`. Preferring the
    challenge timestamp (this module's current, unchanged behavior) is
    therefore fully compatible with both stable and beta/current
    Gateway servers; kept as-is per explicit instruction not to re-open
    this without new primary-source evidence, which this is not.
  - `auth` field semantics -- THIS revealed a real, separate, more
    serious bug than RE-VERIFICATION #1's own fix introduced. The
    stable Gateway SERVER's actual connect-auth resolution
    (`resolveSharedConnectAuth`, `resolveDeviceTokenCandidate`,
    `resolveConnectAuthDecisionCore`, all read directly from compiled
    server source) is authoritative here, not client-side field
    *existence* in a schema: `auth.token` (+ `auth.password`) is
    checked against the Gateway's own configured SHARED secret
    (`resolveSharedConnectAuth`) -- this is what Jarvis's
    `OPENCLAW_GATEWAY_TOKEN` actually IS. `auth.bootstrapToken` is
    verified via a WHOLLY SEPARATE path (`verifyBootstrapToken(deviceId,
    publicKey, token, ...)`) meant for a genuinely distinct device-
    pairing/setup credential Jarvis does not hold -- RE-VERIFICATION #1
    incorrectly sent `OPENCLAW_GATEWAY_TOKEN` under this field, which
    was itself a bug, now fixed. `auth.deviceToken` is checked via a
    third, separate path (`verifyDeviceToken`), and MUST be used
    (rather than reusing `auth.token`) for a stored, already-paired
    device token specifically because a rejection there reports
    `AUTH_DEVICE_TOKEN_MISMATCH` (`candidateSource ===
    "explicit-device-token"`) -- reusing `auth.token` for a stale
    device token would instead surface as `AUTH_TOKEN_MISMATCH`,
    silently breaking this module's stale-token clear-and-retry logic.
    Corrected: the shared credential now always goes under `auth.token`;
    a stored device token now always goes under `auth.deviceToken`;
    `auth.bootstrapToken` is never populated (see `_connect_and_call`'s
    `auth_field`).
  - Device-ID derivation: now CONFIRMED (see the paragraph above) via
    this same stable bundle's `deriveDeviceIdFromPublicKey` and its use
    in the server's own independent re-derive-and-compare check.

REAL GATEWAY SMOKE TEST (2026-08-17): every finding above came from
reading source, never from running an actual OpenClaw Gateway. Running
one for the first time (an isolated, loopback-only, plugin-disabled
`openclaw@2026.7.1-2` process under a temp state dir, per this project's
standing "verify against primary source" discipline extended one level
further) immediately surfaced a real bug none of that source-reading
had caught: the real Gateway rejected `connect` with `INVALID_REQUEST`
("at /client: must have required property 'platform'"). The real
`ConnectParams.client` schema does mark `platform` required
(`protocol.schema.json`), and the real client always sends
`this.opts.platform ?? process.platform` -- this had simply never been
checked against directly, since the fake local test server's own
signature/field checks never validated the full schema, only the
specific fields this bridge's tests were written to check. Fixed:
`client.platform` (and the signed V3 payload's `platform` component,
which must match) now carries `sys.platform` (`_CLIENT_PLATFORM`),
matching Node's `process.platform` string values byte-for-byte on every
platform Jarvis runs on.

Fixing that surfaced a second, related bug immediately: the real
Gateway then rejected connect with "device signature invalid". The real
server reconstructs the signed payload's `deviceFamily` component from
`connectParams.client.deviceFamily`
(`resolveDeviceSignaturePayloadVersion`, real compiled server source) --
this module signed `deviceFamily="jarvis"` but never actually included
`client.deviceFamily` in the wire `connect` params at all, so the
server's independent reconstruction used an empty string and the
signature no longer matched what was actually sent. Fixed: `client`
now also carries `deviceFamily` (`_CLIENT_DEVICE_FAMILY`), matching what
was already being signed. Both bugs are the same class of mistake --
signing a field that isn't also present on the wire -- and neither was
caught by extensive source-reading or by the local fake test server,
whose own signature verification reconstructed payloads from expected
constants rather than from the actual captured wire values; the fake
server was corrected to do the latter (read `client.platform`/
`client.deviceFamily` from what was actually sent, not from a constant)
so a future version of this same mistake would be caught locally next
time. A concrete reminder that source-reading and a local fake server,
however careful, are not a substitute for testing against the real
thing at least once.

Connection model: one-shot (websockets.sync.client, not async -- fits
Jarvis's synchronous tool architecture with no event-loop adapter
needed). Never requests scopes beyond ["operator.read"] -- the minimal
scope OpenClaw's own docs confirm is sufficient for
health/status/node.list. Bounded retry: if a stored device token is
rejected (AUTH_DEVICE_TOKEN_MISMATCH), it is cleared and ONE retry is
made with the bootstrap token -- mirroring the real client's own
documented "cleared stale device-auth token" behavior (confirmed in the
same compiled `index.mjs`) -- never more than that.
"""
import base64
import hashlib
import json
import sys
import time
import uuid
from typing import Optional

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import (
    Encoding,
    NoEncryption,
    PrivateFormat,
    PublicFormat,
    load_pem_private_key,
)
from websockets.exceptions import WebSocketException
from websockets.sync.client import connect as ws_connect

from agent.cancellation import cancellation_requested
from agent.observability import log_event
from agent.request_context import get_current_request_id
from agent.secrets import get_secret, set_secret
from config.settings import settings

# Verified 2026-08-16 against docs.openclaw.ai/gateway/protocol. OpenClaw
# nodes may use protocol N-1 for backward compatibility, but this bridge
# only ever speaks the current version -- re-verify against current docs
# before bumping, and before assuming an older/newer Gateway is
# compatible.
_PROTOCOL_VERSION = 4

# Real, closed enums verified against @openclaw/gateway-protocol's
# published client-info.mjs -- client.id/client.mode are validated by
# the Gateway against this exact set, not free text. "cli" is the
# closest legitimate, non-reserved identity available for a
# non-interactive third-party integration; "backend"/"gateway-client"
# are OpenClaw's own reserved internal identity and are never used here.
_CLIENT_ID = "cli"
_CLIENT_MODE = "cli"
_CLIENT_DEVICE_FAMILY = "jarvis"
# Real ConnectParams.client schema (protocol.schema.json) marks `platform`
# required (`"required": ["id", "version", "platform", "mode"]`) -- caught
# 2026-08-17 by a real Gateway process rejecting connect with
# INVALID_REQUEST ("at /client: must have required property 'platform'"),
# not by any prior source inspection. sys.platform matches Node's
# process.platform byte-for-byte on every platform Jarvis runs on
# ("darwin"/"linux"/"win32"), which the real client itself uses as its
# default (`this.opts.platform ?? process.platform`).
_CLIENT_PLATFORM = sys.platform
_ROLE = "operator"
_SCOPES = ["operator.read"]

_DEVICE_PRIVATE_KEY_SECRET = "OPENCLAW_DEVICE_PRIVATE_KEY"
_DEVICE_TOKEN_SECRET = "OPENCLAW_DEVICE_TOKEN"
_BOOTSTRAP_TOKEN_SECRET = "OPENCLAW_GATEWAY_TOKEN"

# Every RPC method this module will ever send, full stop -- see module
# docstring. No caller-supplied method string reaches _call() from a
# tool's own input schema (openclaw_status/openclaw_list_nodes take no
# method-selecting parameter at all -- see tools/schemas/openclaw.py),
# so this allowlist is defense-in-depth on top of that structural
# guarantee, not the only thing standing between a tool call and an
# arbitrary RPC. Deliberately excludes node.describe -- node.list's own
# response already carries everything get_node_list() needs.
_ALLOWED_METHODS = frozenset({"health", "status", "node.list"})

# Real, verified error codes (ConnectErrorDetailCodes,
# @openclaw/gateway-protocol/connect-error-details.mjs, read verbatim
# from the published package) -- NOT guessed. Grouped by which
# normalized exception each maps to.
_SCOPE_MISMATCH_CODES = frozenset({"AUTH_SCOPE_MISMATCH"})
_AUTH_ERROR_CODES = frozenset({
    "AUTH_REQUIRED", "AUTH_UNAUTHORIZED", "AUTH_TOKEN_MISSING", "AUTH_TOKEN_MISMATCH",
    "AUTH_TOKEN_NOT_CONFIGURED", "AUTH_PASSWORD_MISSING", "AUTH_PASSWORD_MISMATCH",
    "AUTH_PASSWORD_NOT_CONFIGURED", "AUTH_BOOTSTRAP_TOKEN_INVALID", "AUTH_DEVICE_TOKEN_MISMATCH",
    "AUTH_TAILSCALE_IDENTITY_MISSING", "AUTH_TAILSCALE_PROXY_MISSING", "AUTH_TAILSCALE_WHOIS_FAILED",
    "AUTH_TAILSCALE_IDENTITY_MISMATCH", "DEVICE_IDENTITY_REQUIRED", "CONTROL_UI_DEVICE_IDENTITY_REQUIRED",
    "DEVICE_AUTH_INVALID", "DEVICE_AUTH_DEVICE_ID_MISMATCH", "DEVICE_AUTH_SIGNATURE_EXPIRED",
    "DEVICE_AUTH_NONCE_REQUIRED", "DEVICE_AUTH_NONCE_MISMATCH", "DEVICE_AUTH_SIGNATURE_INVALID",
    "DEVICE_AUTH_PUBLIC_KEY_INVALID",
})
_UNSUPPORTED_ERROR_CODES = frozenset({"PROTOCOL_MISMATCH", "CLIENT_VERSION_MISMATCH", "CONTROL_UI_BUILD_MISMATCH"})
_PAIRING_REQUIRED_CODE = "PAIRING_REQUIRED"
# AUTH_DEVICE_TOKEN_MISMATCH specifically gets the bounded-retry
# treatment (see _call) rather than an immediate raise -- it stays out
# of _AUTH_ERROR_CODES' direct-raise path only inside _connect_and_call,
# which checks for it before falling through to _raise_for_error.
_AUTH_RATE_LIMITED_CODE = "AUTH_RATE_LIMITED"


class OpenClawError(Exception):
    """Base class for every normalized error this module raises --
    tools/schemas/openclaw.py catches this one class, never a long list
    of specific exception types or a raw websockets/OSError."""


class OpenClawUnavailable(OpenClawError):
    """OpenClaw is disabled, not configured, not running, refused the
    connection outright, or is rate-limiting auth attempts -- treat
    exactly like "OpenClaw isn't installed," never surface a stack
    trace for this."""


class OpenClawAuthError(OpenClawError):
    """The Gateway rejected the configured credential or device
    signature (a real, verified ConnectErrorDetailCodes AUTH_*/
    DEVICE_AUTH_* code -- see module docstring)."""


class OpenClawScopeError(OpenClawError):
    """Authentication succeeded but the granted scopes don't include
    operator.read -- this bridge fails closed rather than proceeding
    with a call it wasn't actually authorized to make (STEP 7's own
    explicit requirement)."""


class OpenClawPairingRequired(OpenClawError):
    """A new (or scope/role-upgraded) device identity needs a human to
    approve it via OpenClaw's own pairing workflow
    (`openclaw devices approve <requestId>`) before this bridge can
    proceed. Never auto-approved by this module."""

    def __init__(self, message: str, request_id: Optional[str] = None, reason: Optional[str] = None):
        super().__init__(message)
        self.request_id = request_id
        self.reason = reason


class OpenClawProtocolError(OpenClawError):
    """A malformed frame or a response that doesn't match the documented
    protocol shape."""


class OpenClawTimeout(OpenClawError):
    """The connection, challenge, handshake, or RPC call didn't complete
    within settings.openclaw_timeout_seconds."""


class OpenClawUnsupportedCapability(OpenClawError):
    """The connected Gateway's negotiated protocol/feature set doesn't
    support something this bridge needs."""


class _DeviceTokenMismatch(Exception):
    """Internal sentinel only -- never leaves this module. Signals that
    the CURRENT attempt used a stored device token the Gateway just
    rejected, so the bounded fallback-to-bootstrap-token retry in
    _call() should run. Deliberately not an OpenClawError subclass so
    it can't be accidentally caught by a caller expecting the public
    error hierarchy."""


def openclaw_configured() -> bool:
    """Cheap: no network call -- mirrors agent/provider_health.py's own
    "configured" vocabulary. Requires BOTH the explicit opt-in setting
    and a real bootstrap token; either alone is treated as "not
    configured." A stored device identity/device token is NOT required
    here -- both are created/obtained lazily on first real connection
    attempt."""
    return bool(settings.openclaw_enabled) and bool(get_secret(_BOOTSTRAP_TOKEN_SECRET))


def _load_or_create_device_identity():
    """Returns (private_key, device_id, public_key_b64url). Persists a
    newly-generated Ed25519 private key (PEM, PKCS8, unencrypted --
    protected by Keychain access control, the same trust boundary every
    other secret in this project already relies on) via agent/secrets.py
    on first use; reloads it on every subsequent call rather than
    caching in memory, matching this module's one-shot-per-call
    philosophy (see module docstring) -- correctness over the marginal
    cost of re-parsing a small PEM string.

    device_id derivation: SHA-256 of the raw 32-byte Ed25519 public key,
    hex-encoded. CONFIRMED against real primary source (the stable
    `openclaw` app's own `deriveDeviceIdFromPublicKey` and the Gateway
    server's own independent re-derivation check on every connect) -- see
    the module docstring's "DEVICE-ID DERIVATION -- CONFIRMED" section."""
    pem = get_secret(_DEVICE_PRIVATE_KEY_SECRET)
    if pem:
        private_key = load_pem_private_key(pem.encode("ascii"), password=None)
    else:
        private_key = Ed25519PrivateKey.generate()
        new_pem = private_key.private_bytes(Encoding.PEM, PrivateFormat.PKCS8, NoEncryption())
        set_secret(_DEVICE_PRIVATE_KEY_SECRET, new_pem.decode("ascii"))

    public_key = private_key.public_key()
    raw_public_key = public_key.public_bytes(Encoding.Raw, PublicFormat.Raw)
    device_id = hashlib.sha256(raw_public_key).hexdigest()
    public_key_b64url = base64.urlsafe_b64encode(raw_public_key).rstrip(b"=").decode("ascii")
    return private_key, device_id, public_key_b64url


def _build_device_auth_payload_v3(
    device_id: str, client_id: str, client_mode: str, role: str, scopes,
    signed_at_ms: int, token: Optional[str], nonce: str,
    platform: Optional[str] = None, device_family: Optional[str] = None,
) -> str:
    """Verbatim port of the real, current `buildDeviceAuthPayloadV3` from
    @openclaw/gateway-client's published device-auth.ts -- pipe-delimited,
    "v3" prefix, lowercase-normalized optional platform/deviceFamily
    (the real comment: "Device signatures are byte-for-byte compared by
    the gateway. Normalize optional metadata before joining so case
    differences do not break auth")."""
    scopes_str = ",".join(scopes)
    token_str = token or ""
    platform_norm = (platform or "").strip().lower()
    device_family_norm = (device_family or "").strip().lower()
    return "|".join([
        "v3", device_id, client_id, client_mode, role, scopes_str,
        str(signed_at_ms), token_str, nonce, platform_norm, device_family_norm,
    ])


def _sign_payload(private_key, payload: str) -> str:
    signature = private_key.sign(payload.encode("utf-8"))
    return base64.urlsafe_b64encode(signature).rstrip(b"=").decode("ascii")


def _clear_device_token() -> None:
    set_secret(_DEVICE_TOKEN_SECRET, "")


def _raise_for_error(error: dict) -> None:
    code = str(error.get("code") or "")
    message = str(error.get("message") or "OpenClaw request failed")[:500]
    details = error.get("details") if isinstance(error.get("details"), dict) else {}

    if code == _PAIRING_REQUIRED_CODE:
        request_id = details.get("requestId") if isinstance(details.get("requestId"), str) else None
        reason = details.get("reason") if isinstance(details.get("reason"), str) else None
        raise OpenClawPairingRequired(message or "device pairing required", request_id=request_id, reason=reason)
    if code == _AUTH_RATE_LIMITED_CODE:
        raise OpenClawUnavailable(f"OpenClaw is temporarily rate-limiting auth attempts: {message}")
    if code in _SCOPE_MISMATCH_CODES:
        raise OpenClawScopeError(message)
    if code in _AUTH_ERROR_CODES:
        raise OpenClawAuthError(message)
    if code in _UNSUPPORTED_ERROR_CODES:
        raise OpenClawUnsupportedCapability(message)
    raise OpenClawProtocolError(f"{code}: {message}" if code else message)


def _receive_matching(ws, expected_id: str, budget_start: float) -> dict:
    """Reads frames until one whose id matches expected_id arrives,
    silently skipping stray `event` frames -- the documented protocol
    allows the Gateway to broadcast events at any time. Bounded by the
    SAME overall settings.openclaw_timeout_seconds budget as the whole
    call (not a fresh timeout per frame)."""
    while True:
        remaining = settings.openclaw_timeout_seconds - (time.time() - budget_start)
        if remaining <= 0:
            raise TimeoutError("OpenClaw response budget exhausted")
        raw = ws.recv(timeout=remaining)
        frame = json.loads(raw)
        if frame.get("type") == "event":
            continue
        if frame.get("id") == expected_id:
            return frame
        # A response to some other id -- one outstanding request at a
        # time in this one-shot connection, so this should never
        # actually happen; ignore rather than treat as fatal.


def _wait_for_challenge(ws, budget_start: float):
    """Waits specifically for the `connect.challenge` event every real
    connect handshake begins with (verified verbatim against
    protocol-client.handshake.test.ts's real tests -- see module
    docstring). Returns (nonce, challenge_ts_ms_or_none); a missing/
    malformed timestamp is treated as None (matching the real client's
    own tested "malformed challenge timestamps are invalid" -> null
    behavior), with the caller falling back to wall-clock time."""
    while True:
        remaining = settings.openclaw_timeout_seconds - (time.time() - budget_start)
        if remaining <= 0:
            raise TimeoutError("OpenClaw challenge budget exhausted")
        raw = ws.recv(timeout=remaining)
        frame = json.loads(raw)
        if frame.get("type") == "event" and frame.get("event") == "connect.challenge":
            challenge_payload = frame.get("payload") or {}
            nonce = challenge_payload.get("nonce")
            if not isinstance(nonce, str) or not nonce:
                raise OpenClawProtocolError("Gateway sent a connect.challenge with no usable nonce")
            ts = challenge_payload.get("ts")
            challenge_ts = ts if isinstance(ts, (int, float)) and not isinstance(ts, bool) else None
            return nonce, challenge_ts
        # Anything else before the challenge is unexpected -- keep
        # waiting rather than proceeding without one; this bridge never
        # sends device-auth material without a real challenge to sign.


def _connect_and_call(method: str, params: Optional[dict], call_id: str, start: float, use_device_token: bool) -> dict:
    private_key, device_id, public_key_b64url = _load_or_create_device_identity()
    bootstrap_token = get_secret(_BOOTSTRAP_TOKEN_SECRET)
    device_token = get_secret(_DEVICE_TOKEN_SECRET) if use_device_token else None
    auth_token = device_token or bootstrap_token
    # Verified against the real, current STABLE Gateway server's own
    # connect-auth resolution (resolveSharedConnectAuth/
    # resolveDeviceTokenCandidate/resolveConnectAuthDecisionCore,
    # openclaw@2026.7.1-2's compiled server source) -- NOT just the
    # separately-published beta client packages' field existence, which
    # only proves a field CAN be sent, not what it MEANS. Confirmed
    # server-side: `auth.token` (+ `auth.password`) is the SHARED Gateway
    # secret check (OPENCLAW_GATEWAY_TOKEN's real identity); a stored,
    # already-paired device credential belongs under the DEDICATED
    # `auth.deviceToken` field (checked via a fully separate
    # verifyDeviceToken() path) specifically so a rejection reports
    # AUTH_DEVICE_TOKEN_MISMATCH (candidateSource "explicit-device-token")
    # rather than being silently reinterpreted as a failed shared-token
    # check -- required for the stale-token clear-and-retry logic below to
    # ever actually trigger. `auth.bootstrapToken` is a GENUINELY DISTINCT
    # credential (verified via verifyBootstrapToken(deviceId, publicKey,
    # token, ...), meant for a device-pairing/setup code) that Jarvis does
    # not hold and must never populate with OPENCLAW_GATEWAY_TOKEN -- an
    # earlier version of this bridge incorrectly did so.
    auth_field = "deviceToken" if device_token else "token"

    with ws_connect(
        settings.openclaw_gateway_url,
        open_timeout=settings.openclaw_timeout_seconds,
        close_timeout=settings.openclaw_timeout_seconds,
    ) as ws:
        nonce, challenge_ts = _wait_for_challenge(ws, start)
        signed_at_ms = int(challenge_ts) if challenge_ts is not None else int(time.time() * 1000)

        payload = _build_device_auth_payload_v3(
            device_id=device_id, client_id=_CLIENT_ID, client_mode=_CLIENT_MODE,
            role=_ROLE, scopes=_SCOPES, signed_at_ms=signed_at_ms,
            token=auth_token, nonce=nonce, platform=_CLIENT_PLATFORM,
            device_family=_CLIENT_DEVICE_FAMILY,
        )
        signature = _sign_payload(private_key, payload)

        connect_frame = {
            "type": "req",
            "id": str(uuid.uuid4()),
            "method": "connect",
            "params": {
                "minProtocol": _PROTOCOL_VERSION,
                "maxProtocol": _PROTOCOL_VERSION,
                "client": {
                    "id": _CLIENT_ID, "mode": _CLIENT_MODE, "version": "1",
                    "platform": _CLIENT_PLATFORM, "deviceFamily": _CLIENT_DEVICE_FAMILY,
                },
                "role": _ROLE,
                "scopes": list(_SCOPES),
                "auth": {auth_field: auth_token},
                "device": {
                    "id": device_id, "publicKey": public_key_b64url,
                    "signature": signature, "signedAt": signed_at_ms, "nonce": nonce,
                },
            },
        }
        ws.send(json.dumps(connect_frame))
        hello = _receive_matching(ws, connect_frame["id"], start)

        if not hello.get("ok", False):
            error = hello.get("error") or {}
            if str(error.get("code") or "") == "AUTH_DEVICE_TOKEN_MISMATCH" and use_device_token:
                raise _DeviceTokenMismatch()
            _raise_for_error(error)

        hello_payload = hello.get("payload") or {}
        negotiated_protocol = hello_payload.get("protocol")
        if negotiated_protocol != _PROTOCOL_VERSION:
            raise OpenClawUnsupportedCapability(
                f"Gateway negotiated protocol {negotiated_protocol!r}, "
                f"Jarvis only supports protocol {_PROTOCOL_VERSION}"
            )

        auth_info = hello_payload.get("auth") or {}
        granted_scopes = auth_info.get("scopes") or []
        if "operator.read" not in granted_scopes:
            # STEP 7: authentication succeeding is NOT sufficient -- the
            # exact scope this bridge needs must actually be granted, or
            # this fails closed rather than proceeding.
            raise OpenClawScopeError("Gateway authenticated the connection but did not grant operator.read")

        new_device_token = auth_info.get("deviceToken")
        if isinstance(new_device_token, str) and new_device_token and new_device_token != device_token:
            set_secret(_DEVICE_TOKEN_SECRET, new_device_token)

        advertised_methods = (hello_payload.get("features") or {}).get("methods") or []
        if advertised_methods and method not in advertised_methods:
            raise OpenClawUnsupportedCapability(f"Gateway does not advertise support for '{method}'")

        request_frame = {"type": "req", "id": call_id, "method": method, "params": params or {}}
        ws.send(json.dumps(request_frame))
        response = _receive_matching(ws, call_id, start)

    if not response.get("ok", False):
        _raise_for_error(response.get("error") or {})
    return response.get("payload") or {}


def _call(method: str, params: Optional[dict] = None) -> dict:
    """The one place this module ever opens a WebSocket. Bounded to at
    most 2 real connection attempts total: try a stored device token
    first if one exists, falling back to the bootstrap token exactly
    once if (and only if) the Gateway specifically reports
    AUTH_DEVICE_TOKEN_MISMATCH for it -- never more than that, and never
    for any other error (STEP 6's own explicit "must not create infinite
    retry loops" requirement)."""

    if method not in _ALLOWED_METHODS:
        raise OpenClawProtocolError(f"'{method}' is not an allowlisted OpenClaw method")
    if not openclaw_configured():
        raise OpenClawUnavailable("OpenClaw is disabled or not configured")

    request_id = get_current_request_id()
    if request_id and cancellation_requested(request_id):
        raise OpenClawUnavailable("cancelled before the OpenClaw request started")

    call_id = str(uuid.uuid4())
    start = time.time()
    log_event("openclaw_request_started", request_id=request_id, component="openclaw", method=method)

    stored_device_token = get_secret(_DEVICE_TOKEN_SECRET)
    attempts = [True, False] if stored_device_token else [False]
    last_auth_error: Optional[OpenClawError] = None

    for use_device_token in attempts:
        try:
            response = _connect_and_call(method, params, call_id, start, use_device_token)
        except _DeviceTokenMismatch:
            _clear_device_token()
            last_auth_error = OpenClawAuthError(
                "stored OpenClaw device token was rejected (AUTH_DEVICE_TOKEN_MISMATCH); "
                "cleared it and retried once with the bootstrap token"
            )
            continue
        except OpenClawError:
            log_event(
                "openclaw_request_failed", request_id=request_id, component="openclaw",
                level="warning", method=method, error_category="openclaw_error",
            )
            raise
        except TimeoutError as error:
            log_event(
                "openclaw_request_failed", request_id=request_id, component="openclaw",
                level="warning", method=method, error_category="timeout",
            )
            raise OpenClawTimeout(f"OpenClaw request '{method}' timed out") from error
        except (OSError, WebSocketException) as error:
            log_event(
                "openclaw_request_failed", request_id=request_id, component="openclaw",
                level="warning", method=method, error_category="unavailable", error_type=type(error).__name__,
            )
            raise OpenClawUnavailable(f"OpenClaw Gateway is unavailable: {type(error).__name__}") from error
        except (json.JSONDecodeError, TypeError, AttributeError) as error:
            log_event(
                "openclaw_request_failed", request_id=request_id, component="openclaw",
                level="warning", method=method, error_category="protocol", error_type=type(error).__name__,
            )
            raise OpenClawProtocolError(f"OpenClaw returned an unreadable response: {type(error).__name__}") from error
        else:
            log_event(
                "openclaw_request_completed", request_id=request_id, component="openclaw",
                method=method, duration_seconds=round(time.time() - start, 3),
            )
            return response

    log_event(
        "openclaw_request_failed", request_id=request_id, component="openclaw",
        level="warning", method=method, error_category="device_token_retry_exhausted",
    )
    raise last_auth_error or OpenClawAuthError("OpenClaw device token retry exhausted")


def _summarize_status_payload(payload: dict) -> dict:
    # Whitelist only -- never pass the raw Gateway payload through to a
    # tool result.
    server = payload.get("server") if isinstance(payload.get("server"), dict) else {}
    return {
        "runtime": payload.get("runtime") or payload.get("state"),
        "version": payload.get("version") or server.get("version"),
    }


def get_status() -> dict:
    """Normalized status summary for the openclaw_status tool. Never
    raises -- every OpenClawError is caught here and turned into
    available=False with a short, safe detail string. PAIRING_REQUIRED
    gets its own distinct, clean shape (never auto-approved)."""
    if not openclaw_configured():
        return {"configured": False, "available": False, "detail": "OpenClaw is disabled or not configured"}
    try:
        payload = _call("status")
    except OpenClawPairingRequired as error:
        return {
            "configured": True, "available": False, "pairing_required": True,
            "pairing_request_id": error.request_id,
            "message": "OpenClaw device pairing approval is required.",
        }
    except OpenClawError as error:
        return {"configured": True, "available": False, "detail": str(error)}
    return {
        "configured": True, "available": True, "protocol": _PROTOCOL_VERSION,
        "detail": _summarize_status_payload(payload),
    }


def _minimize_capabilities(raw_capabilities) -> list:
    """High-level capability NAMES only -- never full descriptor objects,
    which could carry node-published plugin/skill definitions."""
    if not isinstance(raw_capabilities, list):
        return []
    names = []
    for item in raw_capabilities:
        if isinstance(item, str):
            names.append(item)
        elif isinstance(item, dict) and isinstance(item.get("name"), str):
            names.append(item["name"])
    return names


def _minimize_node(raw: dict) -> dict:
    # Explicit field-by-field construction, never a dict merge/passthrough.
    return {
        "id": raw.get("id"),
        "display_name": raw.get("displayName") or raw.get("name") or raw.get("id"),
        "platform": raw.get("platform"),
        "connected": bool(raw.get("connected", raw.get("state") == "connected")),
        "capabilities": _minimize_capabilities(raw.get("capabilities") or raw.get("commands") or []),
    }


def get_node_list() -> dict:
    """Normalized node summary for the openclaw_list_nodes tool. Never
    raises."""
    if not openclaw_configured():
        return {"configured": False, "available": False, "nodes": []}
    try:
        payload = _call("node.list")
    except OpenClawPairingRequired as error:
        return {
            "configured": True, "available": False, "nodes": [], "pairing_required": True,
            "pairing_request_id": error.request_id,
            "message": "OpenClaw device pairing approval is required.",
        }
    except OpenClawError as error:
        return {"configured": True, "available": False, "detail": str(error), "nodes": []}

    raw_nodes = payload.get("nodes")
    if not isinstance(raw_nodes, list):
        raw_nodes = []
    nodes = [_minimize_node(raw) for raw in raw_nodes if isinstance(raw, dict)]
    return {"configured": True, "available": True, "nodes": nodes}
