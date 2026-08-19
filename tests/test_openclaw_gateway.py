"""Tests for agent/openclaw_gateway.py -- OpenClaw M1's read-only,
device-identity-authenticated Gateway bridge.

Two layers, matching this project's established "mock at the external-
call boundary, but prove at least one real-boundary path works" policy:
- TestRealFakeGatewayServer runs a genuine local WebSocket server
  (websockets.sync.server, ephemeral loopback port) implementing the
  real challenge -> device-auth-signed-connect -> hello-ok/pairing
  lifecycle, INCLUDING real Ed25519 signature verification against the
  public key the client actually sends -- not a mocked client, and not
  a "signature is non-empty" stub check.
- Everything else is a pure unit test of identity/payload/minimization/
  normalization logic, independent of any network activity.

No real OpenClaw installation is used or required anywhere in this file.
Fake tokens and freshly-generated throwaway keys only -- nothing here is
a real credential.

Run with: python -m unittest tests.test_openclaw_gateway -v
"""
import base64
import json
import sys
import threading
import time
import unittest
from unittest.mock import patch

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey
from cryptography.hazmat.primitives.serialization import Encoding, NoEncryption, PrivateFormat, PublicFormat
from websockets.exceptions import ConnectionClosed
from websockets.sync.server import serve as ws_serve

import agent.openclaw_gateway as gw
from config.settings import settings


def _b64url_decode(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode(value + padding)


def _extract_auth_credential(auth: dict):
    """Real ConnectParams.auth carries the credential under the field
    naming which kind it is (deviceToken/bootstrapToken/token are
    distinct, mutually exclusive fields per the real protocol schema) --
    this fake server accepts whichever one the client actually sent,
    mirroring that real shape rather than assuming one fixed key."""
    return auth.get("deviceToken") or auth.get("bootstrapToken") or auth.get("token")


# --- Real local fake Gateway server (genuine socket, real crypto) ------

def _make_handler(
    method_responses=None,
    protocol=4,
    challenge_nonce="fake-challenge-nonce",
    challenge_ts=1_800_000_000_000,
    omit_challenge=False,
    omit_challenge_ts=False,
    connect_error=None,
    require_valid_signature=True,
    require_wrong_nonce_in_payload=False,
    grant_operator_read=True,
    issue_device_token=None,
    expect_device_token=None,
    reject_shared_token=False,
    hang_after_challenge=False,
    capture=None,
):
    def handler(ws):
        try:
            _serve_one(
                ws, method_responses=method_responses, protocol=protocol,
                challenge_nonce=challenge_nonce, challenge_ts=challenge_ts,
                omit_challenge=omit_challenge, omit_challenge_ts=omit_challenge_ts,
                connect_error=connect_error,
                require_valid_signature=require_valid_signature,
                require_wrong_nonce_in_payload=require_wrong_nonce_in_payload,
                grant_operator_read=grant_operator_read,
                issue_device_token=issue_device_token,
                expect_device_token=expect_device_token,
                reject_shared_token=reject_shared_token,
                hang_after_challenge=hang_after_challenge,
                capture=capture,
            )
        except ConnectionClosed:
            # A client legitimately disconnecting mid-exchange (e.g.
            # right after a rejected hello) is normal, expected server-
            # side behavior, not a test failure.
            pass

    return handler


def _serve_one(ws, *, method_responses, protocol, challenge_nonce, challenge_ts,
                omit_challenge, omit_challenge_ts, connect_error, require_valid_signature,
                require_wrong_nonce_in_payload, grant_operator_read,
                issue_device_token, expect_device_token, reject_shared_token,
                hang_after_challenge, capture):
    if not omit_challenge:
        challenge_payload = {"nonce": challenge_nonce}
        if not omit_challenge_ts:
            challenge_payload["ts"] = challenge_ts
        ws.send(json.dumps({"type": "event", "event": "connect.challenge", "payload": challenge_payload}))

    if hang_after_challenge:
        ws.recv(timeout=5)
        time.sleep(3)
        return

    raw = ws.recv()
    connect_req = json.loads(raw)
    params = connect_req.get("params", {})
    device = params.get("device") or {}
    auth = params.get("auth") or {}
    if capture is not None:
        capture["params"] = params

    if connect_error is not None:
        ws.send(json.dumps({"type": "res", "id": connect_req["id"], "ok": False, "error": connect_error}))
        return

    # Real Gateway server connect-auth resolution (resolveSharedConnectAuth/
    # resolveDeviceTokenCandidate/resolveConnectAuthDecisionCore, verified
    # against openclaw@2026.7.1-2's compiled server source -- see
    # agent/openclaw_gateway.py's STABLE COMPATIBILITY VERIFICATION
    # docstring section): the SHARED Gateway secret (Jarvis's
    # OPENCLAW_GATEWAY_TOKEN) belongs under "token"; a stored, already-
    # paired device credential belongs under the separate "deviceToken"
    # field (checked via a wholly separate path, so its own rejection is
    # reported as AUTH_DEVICE_TOKEN_MISMATCH, not AUTH_TOKEN_MISMATCH).
    # "bootstrapToken" is a distinct device-pairing/setup credential Jarvis
    # never sends. Checked independently so tests can simulate "the
    # device-token attempt is rejected but the shared-token retry
    # succeeds" -- impossible to express if both shared one field.
    device_token_sent = auth.get("deviceToken")
    if device_token_sent is not None and device_token_sent != expect_device_token:
        ws.send(json.dumps({
            "type": "res", "id": connect_req["id"], "ok": False,
            "error": {"code": "AUTH_DEVICE_TOKEN_MISMATCH", "message": "device token mismatch"},
        }))
        return
    if auth.get("token") is not None and reject_shared_token:
        ws.send(json.dumps({
            "type": "res", "id": connect_req["id"], "ok": False,
            "error": {"code": "AUTH_DEVICE_TOKEN_MISMATCH", "message": "shared token also rejected"},
        }))
        return

    if require_valid_signature:
        try:
            public_key_bytes = _b64url_decode(device["publicKey"])
            public_key = Ed25519PublicKey.from_public_bytes(public_key_bytes)
            # Reconstruct the EXACT payload the real client should have
            # signed, using the real module's own payload builder, then
            # cryptographically verify -- not a "non-empty" stub check.
            expected_nonce = "wrong-nonce" if require_wrong_nonce_in_payload else device.get("nonce")
            expected_payload = gw._build_device_auth_payload_v3(
                device_id=device.get("id"), client_id=params.get("client", {}).get("id"),
                client_mode=params.get("client", {}).get("mode"), role=params.get("role"),
                scopes=params.get("scopes") or [], signed_at_ms=device.get("signedAt"),
                token=_extract_auth_credential(auth), nonce=expected_nonce,
                platform=params.get("client", {}).get("platform"),
                device_family=params.get("client", {}).get("deviceFamily"),
            )
            signature_bytes = _b64url_decode(device["signature"])
            public_key.verify(signature_bytes, expected_payload.encode("utf-8"))
        except (InvalidSignature, KeyError, ValueError, TypeError):
            ws.send(json.dumps({
                "type": "res", "id": connect_req["id"], "ok": False,
                "error": {"code": "DEVICE_AUTH_SIGNATURE_INVALID", "message": "device signature verification failed"},
            }))
            return

    if device.get("nonce") != challenge_nonce:
        ws.send(json.dumps({
            "type": "res", "id": connect_req["id"], "ok": False,
            "error": {"code": "DEVICE_AUTH_NONCE_MISMATCH", "message": "nonce does not match the issued challenge"},
        }))
        return

    granted_scopes = ["operator.read"] if grant_operator_read else []
    hello_payload = {
        "type": "hello-ok", "protocol": protocol,
        "server": {"version": "test-gateway"},
        "features": {"methods": ["health", "status", "node.list"]},
        "snapshot": {}, "auth": {"role": "operator", "scopes": granted_scopes},
        "policy": {},
    }
    if issue_device_token:
        hello_payload["auth"]["deviceToken"] = issue_device_token

    ws.send(json.dumps({"type": "res", "id": connect_req["id"], "ok": True, "payload": hello_payload}))

    raw2 = ws.recv()
    req = json.loads(raw2)
    response = (method_responses or {}).get(req["method"])
    if response is None:
        ws.send(json.dumps({
            "type": "res", "id": req["id"], "ok": False,
            "error": {"code": "TEST_NO_RESPONSE_CONFIGURED", "message": f"no response configured for {req['method']}"},
        }))
    else:
        ws.send(json.dumps({"type": "res", "id": req["id"], "ok": True, "payload": response}))


class _FakeGatewayServer:
    def __init__(self, handler):
        self._server = ws_serve(lambda ws: handler(ws), "127.0.0.1", 0)
        self.port = self._server.socket.getsockname()[1]
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()

    @property
    def url(self):
        return f"ws://127.0.0.1:{self.port}"

    def close(self):
        self._server.shutdown()
        self._thread.join(timeout=2)


class RealGatewayTestCase(unittest.TestCase):
    """Points settings at a real local fake server and a real, freshly-
    generated throwaway device identity (never a real credential) for
    the duration of the test."""

    def setUp(self):
        self._orig_url = settings.openclaw_gateway_url
        self._orig_enabled = settings.openclaw_enabled
        self._orig_timeout = settings.openclaw_timeout_seconds
        object.__setattr__(settings, "openclaw_enabled", True)

        self._secrets = {"OPENCLAW_GATEWAY_TOKEN": "fake-bootstrap-token"}

        def _fake_get_secret(name):
            return self._secrets.get(name) or None

        def _fake_set_secret(name, value):
            self._secrets[name] = value

        self._get_secret_patch = patch("agent.openclaw_gateway.get_secret", side_effect=_fake_get_secret)
        self._set_secret_patch = patch("agent.openclaw_gateway.set_secret", side_effect=_fake_set_secret)
        self._get_secret_patch.start()
        self._set_secret_patch.start()

    def tearDown(self):
        self._get_secret_patch.stop()
        self._set_secret_patch.stop()
        object.__setattr__(settings, "openclaw_gateway_url", self._orig_url)
        object.__setattr__(settings, "openclaw_enabled", self._orig_enabled)
        object.__setattr__(settings, "openclaw_timeout_seconds", self._orig_timeout)

    def _serve(self, handler):
        server = _FakeGatewayServer(handler)
        self.addCleanup(server.close)
        object.__setattr__(settings, "openclaw_gateway_url", server.url)
        return server


class TestRealFakeGatewayServer(RealGatewayTestCase):

    def test_health_round_trip_with_real_signature_verification(self):
        self._serve(_make_handler({"health": {"runtime": "running"}}))
        result = gw._call("health", profile=gw._READ_PROFILE)
        self.assertEqual(result, {"runtime": "running"})

    def test_signed_at_uses_the_connect_challenge_timestamp_not_wall_clock(self):
        # Regression guard: the real, current @openclaw/gateway-client
        # (GatewayClient.buildConnectPlan AND
        # GatewayBrowserDeviceAuthLifecycle.buildPlan, both re-verified
        # 2026-08-16 against the freshly-published 2026.8.1-beta.2 release)
        # use `signedAtMs = challengeTs ?? Date.now()` -- the challenge's
        # own timestamp whenever the Gateway supplies one. A distinctive,
        # clearly-not-"now" value proves the client used THAT value and not
        # its own wall clock (which in 2026 would be nowhere near this).
        distinctive_challenge_ts = 1_234_567_890_000
        captured = {}
        self._serve(_make_handler(
            {"health": {}}, challenge_ts=distinctive_challenge_ts, capture=captured,
        ))
        gw._call("health", profile=gw._READ_PROFILE)
        self.assertEqual(captured["params"]["device"]["signedAt"], distinctive_challenge_ts)

    def test_signed_at_falls_back_to_wall_clock_when_challenge_omits_timestamp(self):
        # The real client only falls back to Date.now() when the challenge
        # itself carries no ts at all (and the CLI/backend client actually
        # refuses to proceed in that case when a device identity is
        # configured -- this bridge's own fallback is the browser client's
        # more lenient behavior, still verified against real source).
        captured = {}
        before_ms = int(time.time() * 1000)
        self._serve(_make_handler({"health": {}}, omit_challenge_ts=True, capture=captured))
        gw._call("health", profile=gw._READ_PROFILE)
        after_ms = int(time.time() * 1000)
        signed_at = captured["params"]["device"]["signedAt"]
        self.assertGreaterEqual(signed_at, before_ms)
        self.assertLessEqual(signed_at, after_ms)

    def test_shared_token_sent_under_auth_token_not_bootstrap_token(self):
        # Real Gateway server: auth.token (+ auth.password) is checked
        # against the Gateway's own configured SHARED secret
        # (resolveSharedConnectAuth, verified against openclaw@2026.7.1-2's
        # compiled server source). Jarvis's OPENCLAW_GATEWAY_TOKEN IS that
        # shared secret -- it must never be sent under auth.bootstrapToken
        # (a wholly separate, verifyBootstrapToken-checked device-pairing/
        # setup credential Jarvis does not hold).
        captured = {}
        self._serve(_make_handler({"health": {}}, capture=captured))
        gw._call("health", profile=gw._READ_PROFILE)
        auth = captured["params"]["auth"]
        self.assertEqual(auth.get("token"), "fake-bootstrap-token")
        self.assertNotIn("bootstrapToken", auth)
        self.assertNotIn("deviceToken", auth)

    def test_client_platform_is_present_and_signed(self):
        # Real ConnectParams.client schema (protocol.schema.json) marks
        # platform required -- caught 2026-08-17 by a REAL Gateway process
        # rejecting connect with INVALID_REQUEST ("at /client: must have
        # required property 'platform'"), not by any prior source
        # inspection. Regression guard: the wire client.platform value and
        # the signed payload's platform component must both be present and
        # consistent (sys.platform, matching Node's process.platform
        # byte-for-byte on darwin/linux/win32).
        captured = {}
        self._serve(_make_handler({"health": {}}, capture=captured))
        gw._call("health", profile=gw._READ_PROFILE)
        client = captured["params"]["client"]
        self.assertEqual(client.get("platform"), sys.platform)
        self.assertTrue(client.get("platform"))

    def test_client_device_family_is_present_and_signed(self):
        # Real Gateway server reconstructs the signed payload's
        # deviceFamily component from connectParams.client.deviceFamily
        # (resolveDeviceSignaturePayloadVersion, real compiled server
        # source) -- caught 2026-08-17 by a REAL Gateway process rejecting
        # connect with "device signature invalid" once client.platform
        # alone was fixed. Jarvis signed deviceFamily="jarvis" but never
        # actually sent client.deviceFamily on the wire, so the server's
        # independent reconstruction used an empty string and the
        # signature no longer matched. Regression guard: both must be
        # present and consistent.
        captured = {}
        self._serve(_make_handler({"health": {}}, capture=captured))
        gw._call("health", profile=gw._READ_PROFILE)
        client = captured["params"]["client"]
        self.assertEqual(client.get("deviceFamily"), gw._CLIENT_DEVICE_FAMILY)

    def test_stored_device_token_sent_under_auth_device_token(self):
        # Real Gateway server: auth.deviceToken is checked via a wholly
        # separate path (verifyDeviceToken) from auth.token's shared-secret
        # check -- required so a rejection reports AUTH_DEVICE_TOKEN_MISMATCH
        # (candidateSource "explicit-device-token") rather than being
        # silently reinterpreted as a failed shared-token check.
        self._secrets["OPENCLAW_DEVICE_TOKEN"] = "issued-device-token"
        captured = {}
        self._serve(_make_handler(
            {"health": {}}, expect_device_token="issued-device-token", capture=captured,
        ))
        gw._call("health", profile=gw._READ_PROFILE)
        auth = captured["params"]["auth"]
        self.assertEqual(auth.get("deviceToken"), "issued-device-token")
        self.assertNotIn("token", auth)
        self.assertNotIn("bootstrapToken", auth)

    def test_v3_payload_token_matches_the_credential_actually_sent(self):
        # STEP 4 (load-bearing): Jarvis must never sign payload using a
        # different credential than the one placed in the wire auth object
        # -- OpenClaw has had real bugs in this area before. Proven here by
        # reconstructing the expected V3 payload from the captured wire
        # auth value and confirming the real signature verifies against it
        # (the fake server's own require_valid_signature path already does
        # this cryptographically; this test additionally asserts the plain
        # values match for a clear, direct signal on failure).
        self._secrets["OPENCLAW_DEVICE_TOKEN"] = "issued-device-token"
        captured = {}
        self._serve(_make_handler(
            {"health": {}}, expect_device_token="issued-device-token", capture=captured,
        ))
        gw._call("health", profile=gw._READ_PROFILE)
        auth = captured["params"]["auth"]
        device = captured["params"]["device"]
        sent_credential = auth.get("deviceToken") or auth.get("token")
        self.assertEqual(sent_credential, "issued-device-token")
        expected_payload = gw._build_device_auth_payload_v3(
            device_id=device["id"], client_id=gw._CLIENT_ID, client_mode=gw._CLIENT_MODE,
            role=gw._ROLE, scopes=gw._READ_PROFILE.scopes, signed_at_ms=device["signedAt"],
            token=sent_credential, nonce=device["nonce"], platform=gw._CLIENT_PLATFORM,
            device_family=gw._CLIENT_DEVICE_FAMILY,
        )
        signature_bytes = _b64url_decode(device["signature"])
        public_key = Ed25519PublicKey.from_public_bytes(_b64url_decode(device["publicKey"]))
        public_key.verify(signature_bytes, expected_payload.encode("utf-8"))

    def test_status_round_trip(self):
        self._serve(_make_handler({"status": {"runtime": "running", "version": "test-1"}}))
        result = gw._call("status", profile=gw._READ_PROFILE)
        self.assertEqual(result["version"], "test-1")

    def test_node_list_round_trip(self):
        self._serve(_make_handler({"node.list": {"nodes": [{"id": "n1", "platform": "macos"}]}}))
        result = gw._call("node.list", profile=gw._READ_PROFILE)
        self.assertEqual(result["nodes"][0]["id"], "n1")

    def test_get_status_end_to_end_through_real_server(self):
        self._serve(_make_handler({"status": {"runtime": "running", "version": "test-1"}}))
        summary = gw.get_status()
        self.assertTrue(summary["available"])
        self.assertEqual(summary["detail"]["runtime"], "running")

    def test_get_node_list_end_to_end_through_real_server(self):
        self._serve(_make_handler({"node.list": {"nodes": [
            {"id": "n1", "displayName": "Tommy's iPhone", "platform": "ios", "connected": True,
             "capabilities": ["device.info", "system.notify"]},
        ]}}))
        summary = gw.get_node_list()
        self.assertTrue(summary["available"])
        node = summary["nodes"][0]
        self.assertEqual(node["display_name"], "Tommy's iPhone")
        self.assertEqual(node["capabilities"], ["device.info", "system.notify"])

    def test_invalid_signature_is_rejected_by_the_server_and_normalized_by_the_client(self):
        # The server independently, cryptographically re-verifies against
        # the real payload -- a tampered nonce means the signature no
        # longer matches what was actually signed.
        self._serve(_make_handler({"health": {}}, require_wrong_nonce_in_payload=True))
        with self.assertRaises(gw.OpenClawAuthError):
            gw._call("health", profile=gw._READ_PROFILE)

    def test_missing_operator_read_scope_fails_closed(self):
        self._serve(_make_handler({"health": {"runtime": "running"}}, grant_operator_read=False))
        with self.assertRaises(gw.OpenClawScopeError):
            gw._call("health", profile=gw._READ_PROFILE)

    def test_pairing_required_is_normalized_and_never_auto_approved(self):
        self._serve(_make_handler(connect_error={
            "code": "PAIRING_REQUIRED", "message": "device pairing required",
            "details": {"requestId": "req-abc123", "reason": "not-paired"},
        }))
        try:
            gw._call("health", profile=gw._READ_PROFILE)
            self.fail("expected OpenClawPairingRequired")
        except gw.OpenClawPairingRequired as error:
            self.assertEqual(error.request_id, "req-abc123")
            self.assertEqual(error.reason, "not-paired")

    def test_get_status_normalizes_pairing_required_cleanly(self):
        self._serve(_make_handler(connect_error={
            "code": "PAIRING_REQUIRED", "message": "device pairing required",
            "details": {"requestId": "req-xyz", "reason": "not-paired"},
        }))
        summary = gw.get_status()
        self.assertTrue(summary["configured"])
        self.assertFalse(summary["available"])
        self.assertTrue(summary["pairing_required"])
        self.assertEqual(summary["pairing_request_id"], "req-xyz")
        # No raw error structure, signatures, or key material leaked.
        dumped = json.dumps(summary)
        self.assertNotIn("signature", dumped.lower())
        self.assertNotIn("publickey", dumped.lower())

    def test_unsupported_protocol_is_normalized(self):
        self._serve(_make_handler({"health": {}}, protocol=99))
        with self.assertRaises(gw.OpenClawUnsupportedCapability):
            gw._call("health", profile=gw._READ_PROFILE)

    def test_unrecognized_error_code_falls_back_to_protocol_error(self):
        self._serve(_make_handler({}))  # no response configured for "health"
        with self.assertRaises(gw.OpenClawProtocolError):
            gw._call("health", profile=gw._READ_PROFILE)

    def test_timeout_waiting_for_challenge_is_normalized(self):
        object.__setattr__(settings, "openclaw_timeout_seconds", 0.5)
        self._serve(_make_handler(hang_after_challenge=False, omit_challenge=True))
        with self.assertRaises(gw.OpenClawTimeout):
            gw._call("health", profile=gw._READ_PROFILE)

    def test_timeout_waiting_for_hello_is_normalized(self):
        object.__setattr__(settings, "openclaw_timeout_seconds", 0.5)
        self._serve(_make_handler(hang_after_challenge=True))
        with self.assertRaises(gw.OpenClawTimeout):
            gw._call("health", profile=gw._READ_PROFILE)

    def test_gateway_not_running_is_unavailable_not_a_crash(self):
        object.__setattr__(settings, "openclaw_gateway_url", "ws://127.0.0.1:1")
        object.__setattr__(settings, "openclaw_timeout_seconds", 1.0)
        with self.assertRaises(gw.OpenClawUnavailable):
            gw._call("health", profile=gw._READ_PROFILE)

    def test_error_message_never_contains_the_bootstrap_token(self):
        self._serve(_make_handler(connect_error={"code": "AUTH_TOKEN_MISMATCH", "message": "token mismatch"}))
        try:
            gw._call("health", profile=gw._READ_PROFILE)
            self.fail("expected OpenClawAuthError")
        except gw.OpenClawAuthError as error:
            self.assertNotIn("fake-bootstrap-token", str(error))

    def test_device_token_is_used_when_present_and_expected_by_server(self):
        self._secrets["OPENCLAW_DEVICE_TOKEN"] = "issued-device-token"
        self._serve(_make_handler(
            {"health": {"runtime": "running"}}, expect_device_token="issued-device-token",
        ))
        result = gw._call("health", profile=gw._READ_PROFILE)
        self.assertEqual(result["runtime"], "running")

    def test_new_device_token_from_hello_ok_is_persisted(self):
        self._serve(_make_handler({"health": {}}, issue_device_token="brand-new-device-token"))
        gw._call("health", profile=gw._READ_PROFILE)
        self.assertEqual(self._secrets.get("OPENCLAW_DEVICE_TOKEN"), "brand-new-device-token")

    def test_stale_device_token_is_cleared_and_retried_once_with_shared_token(self):
        self._secrets["OPENCLAW_DEVICE_TOKEN"] = "stale-device-token"
        # The stale device-token attempt (auth.deviceToken) is rejected as a
        # mismatch, forcing the bounded fallback to a second attempt sent
        # under auth.token (the shared OPENCLAW_GATEWAY_TOKEN credential),
        # which this fake server accepts by default (reject_shared_token
        # =False).
        self._serve(_make_handler(
            {"health": {"runtime": "running"}}, expect_device_token="a-token-that-is-not-stale",
        ))
        result = gw._call("health", profile=gw._READ_PROFILE)
        self.assertEqual(result["runtime"], "running")
        # Stale token cleared (set to empty string via _clear_device_token).
        self.assertEqual(self._secrets.get("OPENCLAW_DEVICE_TOKEN"), "")

    def test_device_token_mismatch_retry_is_bounded_to_one_attempt(self):
        self._secrets["OPENCLAW_DEVICE_TOKEN"] = "stale-device-token"
        # Server accepts NEITHER credential -- the initial auth.deviceToken
        # attempt AND the auth.token (shared) retry both get rejected. Must
        # raise cleanly, never loop beyond the one bounded retry.
        self._serve(_make_handler(
            {"health": {}}, expect_device_token="a-token-nothing-will-match", reject_shared_token=True,
        ))
        with self.assertRaises(gw.OpenClawAuthError):
            gw._call("health", profile=gw._READ_PROFILE)


class TestConfiguration(unittest.TestCase):

    def setUp(self):
        self._orig_enabled = settings.openclaw_enabled

    def tearDown(self):
        object.__setattr__(settings, "openclaw_enabled", self._orig_enabled)

    def test_disabled_by_default(self):
        from config.settings import Settings
        self.assertFalse(Settings.openclaw_enabled)

    def test_url_defaults_to_loopback(self):
        from config.settings import Settings
        self.assertEqual(Settings.openclaw_gateway_url, "ws://127.0.0.1:18789")

    @patch("agent.openclaw_gateway.get_secret", return_value=None)
    def test_disabled_and_no_token_is_not_configured(self, mock_secret):
        object.__setattr__(settings, "openclaw_enabled", False)
        self.assertFalse(gw.openclaw_configured())

    @patch("agent.openclaw_gateway.get_secret", return_value="a-token")
    def test_enabled_with_bootstrap_token_is_configured(self, mock_secret):
        object.__setattr__(settings, "openclaw_enabled", True)
        self.assertTrue(gw.openclaw_configured())

    @patch("agent.openclaw_gateway.get_secret", return_value=None)
    def test_enabled_without_token_is_not_configured(self, mock_secret):
        object.__setattr__(settings, "openclaw_enabled", True)
        self.assertFalse(gw.openclaw_configured())

    def test_importing_the_module_does_not_require_a_token_or_connection(self):
        self.assertTrue(hasattr(gw, "get_status"))


class TestDeviceIdentity(unittest.TestCase):
    """Direct, mocked-secrets tests of _load_or_create_device_identity --
    no network involved."""

    def setUp(self):
        self._secrets = {}
        self._get_patch = patch("agent.openclaw_gateway.get_secret", side_effect=lambda n: self._secrets.get(n))
        self._set_patch = patch("agent.openclaw_gateway.set_secret", side_effect=lambda n, v: self._secrets.__setitem__(n, v))
        self._get_patch.start()
        self._set_patch.start()

    def tearDown(self):
        self._get_patch.stop()
        self._set_patch.stop()

    def test_generates_and_persists_a_new_identity(self):
        self.assertNotIn("OPENCLAW_DEVICE_PRIVATE_KEY", self._secrets)
        _, device_id, public_key_b64url = gw._load_or_create_device_identity(gw._READ_PROFILE)
        self.assertIn("OPENCLAW_DEVICE_PRIVATE_KEY", self._secrets)
        self.assertTrue(self._secrets["OPENCLAW_DEVICE_PRIVATE_KEY"].startswith("-----BEGIN PRIVATE KEY-----"))
        self.assertEqual(len(device_id), 64)  # sha256 hex digest length
        self.assertTrue(public_key_b64url)

    def test_identity_persists_and_is_deterministic_across_calls(self):
        _, device_id_1, public_key_1 = gw._load_or_create_device_identity(gw._READ_PROFILE)
        _, device_id_2, public_key_2 = gw._load_or_create_device_identity(gw._READ_PROFILE)
        self.assertEqual(device_id_1, device_id_2)
        self.assertEqual(public_key_1, public_key_2)

    def test_different_key_produces_a_different_device_id(self):
        _, device_id_1, _ = gw._load_or_create_device_identity(gw._READ_PROFILE)
        del self._secrets["OPENCLAW_DEVICE_PRIVATE_KEY"]
        _, device_id_2, _ = gw._load_or_create_device_identity(gw._READ_PROFILE)
        self.assertNotEqual(device_id_1, device_id_2)

    def test_reloads_an_existing_stored_key_rather_than_regenerating(self):
        private_key = Ed25519PrivateKey.generate()
        pem = private_key.private_bytes(Encoding.PEM, PrivateFormat.PKCS8, NoEncryption()).decode("ascii")
        self._secrets["OPENCLAW_DEVICE_PRIVATE_KEY"] = pem
        raw_public = private_key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
        expected_device_id = __import__("hashlib").sha256(raw_public).hexdigest()

        _, device_id, _ = gw._load_or_create_device_identity(gw._READ_PROFILE)
        self.assertEqual(device_id, expected_device_id)

    def test_known_public_key_produces_the_expected_device_id(self):
        # STEP 6 known-answer test: a fixed, independently-precomputed
        # SHA-256-hex value for a fixed test keypair, proving the exact
        # algorithm (not just self-consistency against whatever key this
        # run happens to generate). Confirmed CONFIRMED (not assumed)
        # against the real stable openclaw app's own
        # deriveDeviceIdFromPublicKey (src/infra/device-identity.ts) and
        # the Gateway server's own independent re-derivation check.
        fixed_pem = (
            "-----BEGIN PRIVATE KEY-----\n"
            "MC4CAQAwBQYDK2VwBCIEIAABAgMEBQYHCAkKCwwNDg8QERITFBUWFxgZGhscHR4f\n"
            "-----END PRIVATE KEY-----\n"
        )
        self._secrets["OPENCLAW_DEVICE_PRIVATE_KEY"] = fixed_pem
        _, device_id, _ = gw._load_or_create_device_identity(gw._READ_PROFILE)
        self.assertEqual(
            device_id,
            "56475aa75463474c0285df5dbf2bcab73da651358839e9b77481b2eab107708c",
        )
        self.assertEqual(len(device_id), 64)
        self.assertEqual(device_id, device_id.lower())

    def test_private_key_material_never_appears_in_the_device_id_or_public_key(self):
        private_key, device_id, public_key_b64url = gw._load_or_create_device_identity(gw._READ_PROFILE)
        pem = self._secrets["OPENCLAW_DEVICE_PRIVATE_KEY"]
        self.assertNotIn(pem, device_id)
        self.assertNotIn(pem, public_key_b64url)


class TestDeviceAuthPayload(unittest.TestCase):

    def test_v3_payload_format_matches_the_verified_reference_shape(self):
        payload = gw._build_device_auth_payload_v3(
            device_id="deviceabc", client_id="cli", client_mode="cli", role="operator",
            scopes=["operator.read"], signed_at_ms=1700000000123, token="tok", nonce="nonce1",
            platform="MacOS", device_family="Jarvis",
        )
        self.assertEqual(
            payload,
            "v3|deviceabc|cli|cli|operator|operator.read|1700000000123|tok|nonce1|macos|jarvis",
        )

    def test_platform_and_device_family_are_lowercased(self):
        payload = gw._build_device_auth_payload_v3(
            device_id="d", client_id="cli", client_mode="cli", role="operator",
            scopes=[], signed_at_ms=1, token=None, nonce="n", platform="MacOS", device_family="Jarvis",
        )
        self.assertIn("|macos|jarvis", payload)

    def test_missing_token_becomes_empty_string_not_none(self):
        payload = gw._build_device_auth_payload_v3(
            device_id="d", client_id="cli", client_mode="cli", role="operator",
            scopes=[], signed_at_ms=1, token=None, nonce="n",
        )
        self.assertIn("||n", payload)  # empty token segment between the pipes

    def test_multiple_scopes_are_comma_joined(self):
        payload = gw._build_device_auth_payload_v3(
            device_id="d", client_id="cli", client_mode="cli", role="operator",
            scopes=["operator.read", "operator.write"], signed_at_ms=1, token=None, nonce="n",
        )
        self.assertIn("operator.read,operator.write", payload)


class TestSignature(unittest.TestCase):

    def test_valid_signature_verifies_against_the_public_key(self):
        private_key = Ed25519PrivateKey.generate()
        payload = "v3|a|cli|cli|operator|operator.read|1|tok|nonce|platform|family"
        signature_b64url = gw._sign_payload(private_key, payload)

        raw_public = private_key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
        public_key = Ed25519PublicKey.from_public_bytes(raw_public)
        public_key.verify(_b64url_decode(signature_b64url), payload.encode("utf-8"))  # must not raise

    def test_signature_over_a_different_payload_fails_verification(self):
        private_key = Ed25519PrivateKey.generate()
        signature_b64url = gw._sign_payload(private_key, "payload-a")

        raw_public = private_key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
        public_key = Ed25519PublicKey.from_public_bytes(raw_public)
        with self.assertRaises(InvalidSignature):
            public_key.verify(_b64url_decode(signature_b64url), b"payload-b")

    def test_signature_from_a_different_key_fails_verification(self):
        private_key_a = Ed25519PrivateKey.generate()
        private_key_b = Ed25519PrivateKey.generate()
        payload = "same-payload"
        signature_b64url = gw._sign_payload(private_key_a, payload)

        raw_public_b = private_key_b.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
        public_key_b = Ed25519PublicKey.from_public_bytes(raw_public_b)
        with self.assertRaises(InvalidSignature):
            public_key_b.verify(_b64url_decode(signature_b64url), payload.encode("utf-8"))


_FORBIDDEN_METHODS = (
    "node.invoke", "chat.send", "chat.inject", "chat.abort", "tools.invoke",
    "config.get", "config.set", "exec.run", "approval.resolve", "plugin.install",
    "sessions.list", "agents.list", "cron.add", "system.info", "literally.anything.else",
)


class TestSecurityAllowlist(unittest.TestCase):
    """Both profiles are tested against the SAME forbidden-method list --
    neither _READ_PROFILE nor _MESSAGE_PROFILE may ever reach any of
    these, and each profile's own allowed method is exactly one specific
    thing, never the other profile's."""

    @patch("agent.openclaw_gateway.openclaw_configured", return_value=True)
    def test_forbidden_methods_rejected_for_read_profile(self, mock_configured):
        for method in _FORBIDDEN_METHODS:
            with self.subTest(method=method):
                with self.assertRaises(gw.OpenClawProtocolError):
                    gw._call(method, profile=gw._READ_PROFILE)

    @patch("agent.openclaw_gateway.openclaw_configured", return_value=True)
    def test_forbidden_methods_rejected_for_message_profile(self, mock_configured):
        for method in _FORBIDDEN_METHODS:
            with self.subTest(method=method):
                with self.assertRaises(gw.OpenClawProtocolError):
                    gw._call(method, profile=gw._MESSAGE_PROFILE)

    @patch("agent.openclaw_gateway.openclaw_configured", return_value=True)
    def test_read_profile_cannot_call_send(self, mock_configured):
        # Load-bearing: a read-only connection must not be able to reach
        # the messaging RPC even though "send" is a real, known method.
        with self.assertRaises(gw.OpenClawProtocolError):
            gw._call("send", profile=gw._READ_PROFILE)

    @patch("agent.openclaw_gateway.openclaw_configured", return_value=True)
    def test_message_profile_cannot_call_read_methods(self, mock_configured):
        # Load-bearing, the other direction: a write-scoped messaging
        # connection must not be able to reach health/status/node.list
        # either -- each profile's allowlist is independently exact, not
        # a superset/subset relationship.
        for method in ("health", "status", "node.list"):
            with self.subTest(method=method):
                with self.assertRaises(gw.OpenClawProtocolError):
                    gw._call(method, profile=gw._MESSAGE_PROFILE)

    def test_read_allowlist_is_exactly_health_status_node_list(self):
        self.assertEqual(gw._READ_PROFILE.allowed_methods, frozenset({"health", "status", "node.list"}))

    def test_message_allowlist_is_exactly_send(self):
        self.assertEqual(gw._MESSAGE_PROFILE.allowed_methods, frozenset({"send"}))

    def test_read_profile_scopes_are_exactly_operator_read(self):
        self.assertEqual(gw._READ_PROFILE.scopes, ("operator.read",))

    def test_message_profile_scopes_are_exactly_operator_write(self):
        # Confirmed against real primary source (openclaw@2026.7.1-2's
        # operator-scope-compat module) that operator.write already
        # satisfies an operator.read check server-side -- this profile
        # must never separately request operator.read, and must never
        # request operator.admin/operator.approvals/operator.pairing/
        # operator.talk/operator.talk.secrets or any other scope.
        self.assertEqual(gw._MESSAGE_PROFILE.scopes, ("operator.write",))

    def test_read_and_message_profiles_use_distinct_secrets(self):
        # STEP 3's load-bearing requirement: the read-only identity must
        # remain independently scoped and revocable from the messaging
        # identity, and vice versa.
        self.assertNotEqual(gw._READ_PROFILE.private_key_secret, gw._MESSAGE_PROFILE.private_key_secret)
        self.assertNotEqual(gw._READ_PROFILE.device_token_secret, gw._MESSAGE_PROFILE.device_token_secret)

    def test_client_identity_is_not_the_reserved_gateway_client(self):
        self.assertNotEqual(gw._CLIENT_ID, "gateway-client")
        self.assertNotEqual(gw._CLIENT_MODE, "backend")

    def test_no_raw_rpc_function_exists_on_the_module(self):
        public_names = [n for n in dir(gw) if not n.startswith("_")]
        self.assertNotIn("openclaw_raw_rpc", public_names)
        self.assertNotIn("call_rpc", public_names)
        self.assertNotIn("invoke", public_names)
        self.assertNotIn("send_raw", public_names)

    def test_send_raw_only_exists_as_a_private_name(self):
        # There must be no public-looking side-effecting transport
        # function for general Jarvis callers -- only the private
        # _send_raw, sanctioned for use exclusively by
        # agent/openclaw_messaging.py (see that function's own docstring).
        self.assertFalse(hasattr(gw, "send_raw"))
        self.assertTrue(hasattr(gw, "_send_raw"))
        self.assertTrue(callable(gw._send_raw))


class TestProfileIdentityEnforcement(RealGatewayTestCase):
    """_call() must fail closed on anything that isn't one of the two
    real module-level _Profile constants, checked by identity (`is`),
    not by equality -- a forged _Profile with identical field values to
    a real one would otherwise pass a naive `==` check."""

    @patch("agent.openclaw_gateway.openclaw_configured", return_value=True)
    def test_forged_read_like_profile_is_rejected(self, mock_configured):
        forged = gw._Profile(
            name="read",
            private_key_secret=gw._READ_PROFILE.private_key_secret,
            device_token_secret=gw._READ_PROFILE.device_token_secret,
            scopes=gw._READ_PROFILE.scopes,
            allowed_methods=gw._READ_PROFILE.allowed_methods,
        )
        self.assertEqual(forged, gw._READ_PROFILE)  # equal by value...
        with self.assertRaises(gw.OpenClawProtocolError):
            gw._call("health", profile=forged)  # ...but still rejected by identity

    @patch("agent.openclaw_gateway.openclaw_configured", return_value=True)
    def test_forged_message_like_profile_is_rejected(self, mock_configured):
        forged = gw._Profile(
            name="message",
            private_key_secret=gw._MESSAGE_PROFILE.private_key_secret,
            device_token_secret=gw._MESSAGE_PROFILE.device_token_secret,
            scopes=gw._MESSAGE_PROFILE.scopes,
            allowed_methods=gw._MESSAGE_PROFILE.allowed_methods,
        )
        self.assertEqual(forged, gw._MESSAGE_PROFILE)
        with self.assertRaises(gw.OpenClawProtocolError):
            gw._call("send", profile=forged)

    @patch("agent.openclaw_gateway.openclaw_configured", return_value=True)
    def test_forged_broader_scope_profile_is_rejected(self, mock_configured):
        forged = gw._Profile(
            name="admin",
            private_key_secret="OPENCLAW_DEVICE_PRIVATE_KEY",
            device_token_secret="OPENCLAW_DEVICE_TOKEN",
            scopes=("operator.admin",),
            allowed_methods=frozenset({"health", "status", "node.list", "send", "config.set"}),
        )
        with self.assertRaises(gw.OpenClawProtocolError):
            gw._call("health", profile=forged)
        with self.assertRaises(gw.OpenClawProtocolError):
            gw._call("send", profile=forged)

    def test_legitimate_profiles_are_unaffected_by_the_identity_check(self):
        # Confirms the fail-closed check itself doesn't reject the two
        # real profiles: pointed at a closed local port (nothing
        # listening, so the connection fails fast, same pattern as
        # test_gateway_not_running_is_unavailable_not_a_crash above),
        # both still fail -- but only with OpenClawUnavailable, never the
        # profile-identity error, proving the identity check passed them
        # through to the (here, deliberately failing) connection step.
        object.__setattr__(settings, "openclaw_gateway_url", "ws://127.0.0.1:1")
        object.__setattr__(settings, "openclaw_timeout_seconds", 1.0)
        with self.assertRaises(gw.OpenClawUnavailable) as read_ctx:
            gw._call("health", profile=gw._READ_PROFILE)
        self.assertNotIn("sanctioned identities", str(read_ctx.exception))
        with self.assertRaises(gw.OpenClawUnavailable) as message_ctx:
            gw._call("send", profile=gw._MESSAGE_PROFILE)
        self.assertNotIn("sanctioned identities", str(message_ctx.exception))


class TestNodeDataMinimization(unittest.TestCase):

    def test_minimize_node_drops_unlisted_fields(self):
        raw = {
            "id": "n1", "displayName": "Phone", "platform": "ios", "connected": True,
            "authToken": "should-not-appear", "signature": "should-not-appear",
            "publicKey": "should-not-appear", "ipAddress": "10.0.0.5",
            "capabilities": ["device.info"],
        }
        minimized = gw._minimize_node(raw)
        self.assertEqual(set(minimized.keys()), {"id", "display_name", "platform", "connected", "capabilities"})
        self.assertNotIn("10.0.0.5", json.dumps(minimized))

    def test_minimize_capabilities_keeps_only_names_from_descriptor_objects(self):
        raw = [{"name": "device.info", "pluginCode": "should-not-appear"}, "system.notify"]
        minimized = gw._minimize_capabilities(raw)
        self.assertEqual(minimized, ["device.info", "system.notify"])
        self.assertNotIn("pluginCode", json.dumps(minimized))

    def test_get_node_list_never_raises_on_malformed_payload(self):
        with patch("agent.openclaw_gateway.openclaw_configured", return_value=True), \
             patch("agent.openclaw_gateway._call", return_value={"nodes": "not-a-list"}):
            result = gw.get_node_list()
            self.assertEqual(result["nodes"], [])


class TestNormalizedFunctionsNeverRaise(unittest.TestCase):

    @patch("agent.openclaw_gateway.openclaw_configured", return_value=False)
    def test_get_status_when_not_configured(self, mock_configured):
        result = gw.get_status()
        self.assertEqual(result, {"configured": False, "available": False, "detail": "OpenClaw is disabled or not configured"})

    @patch("agent.openclaw_gateway.openclaw_configured", return_value=True)
    @patch("agent.openclaw_gateway._call", side_effect=gw.OpenClawTimeout("timed out"))
    def test_get_status_catches_every_openclaw_error(self, mock_call, mock_configured):
        result = gw.get_status()
        self.assertFalse(result["available"])
        self.assertIn("timed out", result["detail"])

    @patch("agent.openclaw_gateway.openclaw_configured", return_value=True)
    @patch("agent.openclaw_gateway._call", return_value={"nodes": []})
    def test_zero_nodes_is_a_clean_empty_list_not_a_failure(self, mock_call, mock_configured):
        result = gw.get_node_list()
        self.assertTrue(result["available"])
        self.assertEqual(result["nodes"], [])


if __name__ == "__main__":
    unittest.main()
