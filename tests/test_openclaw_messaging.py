"""Tests for agent/openclaw_messaging.py -- OpenClaw M2's outbound,
text-only messaging bridge (the real Gateway `send` RPC, always through
the separate _MESSAGE_PROFILE device identity, never chat.send).

Same two-layer policy as tests/test_openclaw_gateway.py:
- TestSendRpcContract/TestDeliveryOutcomes/TestPairing run a genuine
  local fake Gateway server (websockets.sync.server, ephemeral loopback
  port) for the load-bearing real-wire-protocol paths, including a
  genuine simulated uncertain-delivery scenario (the server receives the
  send frame -- proving it was really transmitted -- then never
  responds, so the client's own real timeout fires).
- Everything else is a pure unit test of validation/allowlist/permission
  logic, independent of any network activity.

No real OpenClaw installation, no real channel, no real outbound message
anywhere in this file. Fake tokens/keys only.

Run with: python -m unittest tests.test_openclaw_messaging -v
"""
import json
import threading
import time
import unittest
import uuid
from unittest.mock import patch

from websockets.exceptions import ConnectionClosed
from websockets.sync.server import serve as ws_serve

import agent.openclaw_gateway as gw
import agent.openclaw_messaging as om
from agent.executor import _run_tool
from agent.request_context import RequestContext
from config.settings import settings

# --- Real local fake Gateway server (genuine socket) --------------------


def _make_message_handler(
    challenge_nonce="fake-message-nonce",
    challenge_ts=1_800_000_000_000,
    connect_error=None,
    grant_operator_write=True,
    send_result=None,
    send_error=None,
    drop_after_send_frame=False,
    capture=None,
    connect_log=None,
):
    def handler(ws):
        try:
            _serve_one_message(
                ws, challenge_nonce=challenge_nonce, challenge_ts=challenge_ts,
                connect_error=connect_error, grant_operator_write=grant_operator_write,
                send_result=send_result, send_error=send_error,
                drop_after_send_frame=drop_after_send_frame,
                capture=capture, connect_log=connect_log,
            )
        except ConnectionClosed:
            # A client legitimately disconnecting mid-exchange (e.g. after
            # its own timeout fires while we're deliberately not
            # responding) is normal, expected server-side behavior here,
            # not a test failure.
            pass

    return handler


def _serve_one_message(ws, *, challenge_nonce, challenge_ts, connect_error,
                        grant_operator_write, send_result, send_error,
                        drop_after_send_frame, capture, connect_log):
    ws.send(json.dumps({
        "type": "event", "event": "connect.challenge",
        "payload": {"nonce": challenge_nonce, "ts": challenge_ts},
    }))
    raw = ws.recv()
    connect_req = json.loads(raw)
    params = connect_req.get("params", {})
    if capture is not None:
        capture["params"] = params
    if connect_log is not None:
        connect_log.append(params)

    if connect_error is not None:
        ws.send(json.dumps({"type": "res", "id": connect_req["id"], "ok": False, "error": connect_error}))
        return

    granted_scopes = ["operator.write"] if grant_operator_write else []
    hello_payload = {
        "type": "hello-ok", "protocol": 4,
        "server": {"version": "test-gateway"},
        "features": {"methods": ["send"]},
        "snapshot": {}, "auth": {"role": "operator", "scopes": granted_scopes},
        "policy": {},
    }
    ws.send(json.dumps({"type": "res", "id": connect_req["id"], "ok": True, "payload": hello_payload}))

    raw2 = ws.recv()
    req = json.loads(raw2)
    if capture is not None:
        capture["method"] = req.get("method")
        capture["send_params"] = req.get("params")
    if connect_log is not None:
        connect_log[-1] = {**connect_log[-1], "_send_params": req.get("params"), "_method": req.get("method")}

    if drop_after_send_frame:
        # Simulate uncertain delivery: the request frame was genuinely
        # received (proof it was really transmitted over the wire) but
        # the server deliberately never answers -- the real client-side
        # timeout must fire, and (only for method=="send") get converted
        # to OpenClawUncertainDelivery, never a plain timeout.
        time.sleep(5)
        return

    if send_error is not None:
        ws.send(json.dumps({"type": "res", "id": req["id"], "ok": False, "error": send_error}))
        return

    payload = send_result if send_result is not None else {
        "messageId": "msg-fake-123", "channel": req.get("params", {}).get("channel"),
    }
    ws.send(json.dumps({"type": "res", "id": req["id"], "ok": True, "payload": payload}))


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


class RealMessageGatewayTestCase(unittest.TestCase):
    """Points settings at a real local fake server, real throwaway
    MESSAGE-profile device identity, and a permissive channel/target
    allowlist (telegram:allowed-target-1) for the duration of the test."""

    def setUp(self):
        self._orig_url = settings.openclaw_gateway_url
        self._orig_enabled = settings.openclaw_enabled
        self._orig_timeout = settings.openclaw_timeout_seconds
        self._orig_msg_enabled = settings.openclaw_messaging_enabled
        self._orig_channels = settings.openclaw_allowed_channels
        self._orig_targets = settings.openclaw_allowed_targets
        object.__setattr__(settings, "openclaw_enabled", True)
        object.__setattr__(settings, "openclaw_messaging_enabled", True)
        object.__setattr__(settings, "openclaw_allowed_channels", "telegram")
        object.__setattr__(settings, "openclaw_allowed_targets", "telegram:allowed-target-1")

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
        object.__setattr__(settings, "openclaw_messaging_enabled", self._orig_msg_enabled)
        object.__setattr__(settings, "openclaw_allowed_channels", self._orig_channels)
        object.__setattr__(settings, "openclaw_allowed_targets", self._orig_targets)

    def _serve(self, handler):
        server = _FakeGatewayServer(handler)
        self.addCleanup(server.close)
        object.__setattr__(settings, "openclaw_gateway_url", server.url)
        return server


class TestSendRpcContract(RealMessageGatewayTestCase):

    def test_method_is_exactly_send(self):
        capture = {}
        self._serve(_make_message_handler(capture=capture))
        om.send_message("telegram", "allowed-target-1", "hello there")
        self.assertEqual(capture["method"], "send")

    def test_chat_send_is_never_used(self):
        capture = {}
        self._serve(_make_message_handler(capture=capture))
        om.send_message("telegram", "allowed-target-1", "hello there")
        self.assertNotEqual(capture["method"], "chat.send")
        self.assertNotEqual(capture["method"], "message.action")

    def test_send_params_shape_matches_stable_schema(self):
        # Real SendParamsSchema (openclaw@2026.7.1-2, additionalProperties:
        # false): to/message/channel/idempotencyKey are what this
        # text-only bridge sends -- accountId/threadId only when
        # supplied. Never agentId/sessionKey/parseMode/media fields.
        capture = {}
        self._serve(_make_message_handler(capture=capture))
        om.send_message("telegram", "allowed-target-1", "hello there")
        params = capture["send_params"]
        self.assertEqual(params["to"], "allowed-target-1")
        self.assertEqual(params["message"], "hello there")
        self.assertEqual(params["channel"], "telegram")
        self.assertIn("idempotencyKey", params)
        for forbidden_field in ("agentId", "sessionKey", "parseMode", "mediaUrl", "mediaUrls", "buffer", "asVoice"):
            self.assertNotIn(forbidden_field, params)

    def test_idempotency_key_is_present_and_a_valid_uuid(self):
        capture = {}
        self._serve(_make_message_handler(capture=capture))
        om.send_message("telegram", "allowed-target-1", "hello there")
        key = capture["send_params"]["idempotencyKey"]
        uuid.UUID(key)  # must not raise

    def test_account_id_and_thread_id_are_not_accepted_parameters(self):
        # Narrowed for the first M2 release (see module docstring):
        # accountId/threadId are optional in the real SendParamsSchema but
        # are not yet independently allowlisted, so send_message() must
        # not accept them at all and they must never appear on the wire.
        import inspect
        sig = inspect.signature(om.send_message)
        self.assertNotIn("account_id", sig.parameters)
        self.assertNotIn("thread_id", sig.parameters)

        capture = {}
        self._serve(_make_message_handler(capture=capture))
        om.send_message("telegram", "allowed-target-1", "hi")
        params = capture["send_params"]
        self.assertNotIn("accountId", params)
        self.assertNotIn("threadId", params)

    def test_gateway_message_id_normalized_into_result(self):
        self._serve(_make_message_handler(send_result={"messageId": "real-msg-id-999", "channel": "telegram"}))
        result = om.send_message("telegram", "allowed-target-1", "hi")
        self.assertEqual(result["message_id"], "real-msg-id-999")

    def test_message_identity_requests_only_operator_write(self):
        capture = {}
        self._serve(_make_message_handler(capture=capture))
        om.send_message("telegram", "allowed-target-1", "hi")
        self.assertEqual(capture["params"]["scopes"], ["operator.write"])

    def test_message_identity_uses_a_distinct_device_from_read_identity(self):
        capture = {}
        self._serve(_make_message_handler(capture=capture))
        om.send_message("telegram", "allowed-target-1", "hi")
        message_device_id = capture["params"]["device"]["id"]
        self.assertIn("OPENCLAW_MESSAGE_DEVICE_PRIVATE_KEY", self._secrets)
        self.assertNotIn("OPENCLAW_DEVICE_PRIVATE_KEY", self._secrets)
        _, read_device_id, _ = gw._load_or_create_device_identity(gw._READ_PROFILE)
        self.assertNotEqual(message_device_id, read_device_id)


class TestDeliveryOutcomes(RealMessageGatewayTestCase):

    def test_confirmed_send_returns_normalized_success(self):
        self._serve(_make_message_handler(send_result={"messageId": "m1", "channel": "telegram"}))
        result = om.send_message("telegram", "allowed-target-1", "hi")
        self.assertEqual(result, {
            "sent": True, "delivery_status": "confirmed",
            "channel": "telegram", "target": "allowed-target-1", "message_id": "m1",
        })

    def test_definitive_failure_returns_normalized_failure(self):
        self._serve(_make_message_handler(send_error={"code": "INVALID_REQUEST", "message": "bad target"}))
        result = om.send_message("telegram", "allowed-target-1", "hi")
        self.assertFalse(result["sent"])
        self.assertEqual(result["delivery_status"], "failed")

    def test_definitive_failure_is_never_retried(self):
        connect_log = []
        self._serve(_make_message_handler(
            send_error={"code": "INVALID_REQUEST", "message": "bad target"}, connect_log=connect_log,
        ))
        om.send_message("telegram", "allowed-target-1", "hi")
        self.assertEqual(len(connect_log), 1)

    def test_uncertain_delivery_after_timeout_is_normalized(self):
        object.__setattr__(settings, "openclaw_timeout_seconds", 0.5)
        self._serve(_make_message_handler(drop_after_send_frame=True))
        result = om.send_message("telegram", "allowed-target-1", "hi")
        self.assertFalse(result["sent"])
        self.assertEqual(result["delivery_status"], "uncertain")

    def test_uncertain_delivery_causes_exactly_one_transmission(self):
        # Hardened behavior: no automatic retry after an uncertain
        # delivery, with the same idempotencyKey or otherwise -- the
        # Gateway's dedupe cache is in-memory/single-process and does not
        # survive a Gateway restart, so a same-key resend is not provably
        # safe (see agent/openclaw_messaging.py's module docstring). This
        # proves the real `send` frame is transmitted exactly once, not
        # merely that the final result looks "uncertain".
        object.__setattr__(settings, "openclaw_timeout_seconds", 0.5)
        connect_log = []
        self._serve(_make_message_handler(drop_after_send_frame=True, connect_log=connect_log))
        result = om.send_message("telegram", "allowed-target-1", "hi")
        self.assertEqual(len(connect_log), 1)
        self.assertFalse(result["sent"])
        self.assertEqual(result["delivery_status"], "uncertain")

    def test_uncertain_delivery_never_triggers_a_second_send_raw_call(self):
        with patch(
            "agent.openclaw_messaging._send_raw",
            side_effect=gw.OpenClawUncertainDelivery("no response"),
        ) as mock_send:
            object.__setattr__(settings, "openclaw_messaging_enabled", True)
            try:
                object.__setattr__(settings, "openclaw_allowed_channels", "telegram")
                object.__setattr__(settings, "openclaw_allowed_targets", "telegram:allowed-target-1")
                result = om.send_message("telegram", "allowed-target-1", "hi")
            finally:
                object.__setattr__(settings, "openclaw_messaging_enabled", False)
                object.__setattr__(settings, "openclaw_allowed_channels", "")
                object.__setattr__(settings, "openclaw_allowed_targets", "")
        mock_send.assert_called_once()
        self.assertEqual(result["delivery_status"], "uncertain")


class TestPairing(RealMessageGatewayTestCase):

    def test_pairing_required_is_normalized_and_never_auto_approved(self):
        self._serve(_make_message_handler(connect_error={
            "code": "PAIRING_REQUIRED", "message": "device pairing required",
            "details": {"requestId": "msg-pairing-req-1", "reason": "not-paired"},
        }))
        result = om.send_message("telegram", "allowed-target-1", "hi")
        self.assertFalse(result["sent"])
        self.assertTrue(result.get("pairing_required"))
        self.assertEqual(result["pairing_request_id"], "msg-pairing-req-1")

    def test_pairing_result_shape_is_distinct_from_m1_read_pairing(self):
        self._serve(_make_message_handler(connect_error={
            "code": "PAIRING_REQUIRED", "message": "device pairing required",
            "details": {"requestId": "msg-pairing-req-2", "reason": "not-paired"},
        }))
        result = om.send_message("telegram", "allowed-target-1", "hi")
        # get_status()'s pairing shape uses "configured"/"available" keys;
        # send_message's uses "sent"/"delivery_status" -- naturally
        # distinct by call site, never confusable.
        self.assertIn("sent", result)
        self.assertIn("delivery_status", result)
        self.assertNotIn("configured", result)
        self.assertNotIn("available", result)

    def test_pairing_never_exposes_signature_or_key_material(self):
        self._serve(_make_message_handler(connect_error={
            "code": "PAIRING_REQUIRED", "message": "device pairing required",
            "details": {"requestId": "msg-pairing-req-3", "reason": "not-paired"},
        }))
        result = om.send_message("telegram", "allowed-target-1", "hi")
        dumped = json.dumps(result).lower()
        self.assertNotIn("signature", dumped)
        self.assertNotIn("publickey", dumped)
        self.assertNotIn("privatekey", dumped)


class TestAllowlist(unittest.TestCase):

    def setUp(self):
        self._orig_msg_enabled = settings.openclaw_messaging_enabled
        self._orig_channels = settings.openclaw_allowed_channels
        self._orig_targets = settings.openclaw_allowed_targets
        object.__setattr__(settings, "openclaw_messaging_enabled", True)
        object.__setattr__(settings, "openclaw_allowed_channels", "telegram")
        object.__setattr__(settings, "openclaw_allowed_targets", "telegram:allowed-target-1")

    def tearDown(self):
        object.__setattr__(settings, "openclaw_messaging_enabled", self._orig_msg_enabled)
        object.__setattr__(settings, "openclaw_allowed_channels", self._orig_channels)
        object.__setattr__(settings, "openclaw_allowed_targets", self._orig_targets)

    def test_messaging_disabled_by_default(self):
        from config.settings import Settings
        self.assertFalse(Settings.openclaw_messaging_enabled)
        self.assertEqual(Settings.openclaw_allowed_channels, "")
        self.assertEqual(Settings.openclaw_allowed_targets, "")

    def test_messaging_disabled_rejects_before_any_network_call(self):
        object.__setattr__(settings, "openclaw_messaging_enabled", False)
        with patch("agent.openclaw_messaging._send_raw") as mock_send:
            result = om.send_message("telegram", "allowed-target-1", "hi")
        mock_send.assert_not_called()
        self.assertFalse(result["sent"])
        self.assertEqual(result["delivery_status"], "failed")

    def test_unsupported_channel_rejected_before_network_call(self):
        with patch("agent.openclaw_messaging._send_raw") as mock_send:
            result = om.send_message("discord", "allowed-target-1", "hi")
        mock_send.assert_not_called()
        self.assertFalse(result["sent"])

    def test_target_not_allowlisted_rejected_before_network_call(self):
        with patch("agent.openclaw_messaging._send_raw") as mock_send:
            result = om.send_message("telegram", "some-other-target", "hi")
        mock_send.assert_not_called()
        self.assertFalse(result["sent"])

    def test_exact_allowlisted_target_is_accepted_past_validation(self):
        with patch("agent.openclaw_messaging._send_raw", return_value={"messageId": "m"}) as mock_send:
            result = om.send_message("telegram", "allowed-target-1", "hi")
        mock_send.assert_called_once()
        self.assertTrue(result["sent"])

    def test_wildcard_channel_is_not_supported(self):
        object.__setattr__(settings, "openclaw_allowed_channels", "telegram")
        with patch("agent.openclaw_messaging._send_raw") as mock_send:
            result = om.send_message("*", "allowed-target-1", "hi")
        mock_send.assert_not_called()
        self.assertFalse(result["sent"])

    def test_wildcard_target_is_not_supported_even_if_literally_configured(self):
        # Configuring the literal string "*" as a target allowlists only
        # that literal string, never "anything" -- there is no
        # wildcard-expansion code path at all.
        object.__setattr__(settings, "openclaw_allowed_targets", "telegram:*")
        with patch("agent.openclaw_messaging._send_raw") as mock_send:
            result = om.send_message("telegram", "some-real-user-id", "hi")
        mock_send.assert_not_called()
        self.assertFalse(result["sent"])

    def test_channel_matching_is_case_insensitive(self):
        with patch("agent.openclaw_messaging._send_raw", return_value={"messageId": "m"}) as mock_send:
            result = om.send_message("TeleGram", "allowed-target-1", "hi")
        mock_send.assert_called_once()
        self.assertTrue(result["sent"])

    def test_target_matching_is_case_sensitive(self):
        with patch("agent.openclaw_messaging._send_raw") as mock_send:
            result = om.send_message("telegram", "ALLOWED-TARGET-1", "hi")
        mock_send.assert_not_called()
        self.assertFalse(result["sent"])

    def test_model_supplied_channel_cannot_bypass_allowlist_via_whitespace_or_case_tricks(self):
        for crafted in ("  telegram  ", "TELEGRAM", "Telegram\n"):
            with self.subTest(crafted=crafted):
                with patch("agent.openclaw_messaging._send_raw") as mock_send:
                    om.send_message("discord", crafted, "hi")
                mock_send.assert_not_called()


class TestMessageValidation(unittest.TestCase):

    def setUp(self):
        self._orig_msg_enabled = settings.openclaw_messaging_enabled
        self._orig_channels = settings.openclaw_allowed_channels
        self._orig_targets = settings.openclaw_allowed_targets
        object.__setattr__(settings, "openclaw_messaging_enabled", True)
        object.__setattr__(settings, "openclaw_allowed_channels", "telegram")
        object.__setattr__(settings, "openclaw_allowed_targets", "telegram:allowed-target-1")

    def tearDown(self):
        object.__setattr__(settings, "openclaw_messaging_enabled", self._orig_msg_enabled)
        object.__setattr__(settings, "openclaw_allowed_channels", self._orig_channels)
        object.__setattr__(settings, "openclaw_allowed_targets", self._orig_targets)

    def test_empty_message_rejected(self):
        with patch("agent.openclaw_messaging._send_raw") as mock_send:
            result = om.send_message("telegram", "allowed-target-1", "")
        mock_send.assert_not_called()
        self.assertFalse(result["sent"])

    def test_none_message_rejected(self):
        with patch("agent.openclaw_messaging._send_raw") as mock_send:
            result = om.send_message("telegram", "allowed-target-1", None)
        mock_send.assert_not_called()
        self.assertFalse(result["sent"])

    def test_whitespace_only_message_rejected(self):
        with patch("agent.openclaw_messaging._send_raw") as mock_send:
            result = om.send_message("telegram", "allowed-target-1", "   \n\t  ")
        mock_send.assert_not_called()
        self.assertFalse(result["sent"])

    def test_oversized_message_rejected(self):
        oversized = "x" * (om.MAX_MESSAGE_LENGTH + 1)
        with patch("agent.openclaw_messaging._send_raw") as mock_send:
            result = om.send_message("telegram", "allowed-target-1", oversized)
        mock_send.assert_not_called()
        self.assertFalse(result["sent"])

    def test_message_at_exactly_max_length_is_accepted(self):
        exact = "x" * om.MAX_MESSAGE_LENGTH
        with patch("agent.openclaw_messaging._send_raw", return_value={"messageId": "m"}) as mock_send:
            result = om.send_message("telegram", "allowed-target-1", exact)
        mock_send.assert_called_once()
        self.assertTrue(result["sent"])

    def test_normal_text_is_accepted(self):
        with patch("agent.openclaw_messaging._send_raw", return_value={"messageId": "m"}) as mock_send:
            result = om.send_message("telegram", "allowed-target-1", "a completely normal message")
        mock_send.assert_called_once()
        self.assertTrue(result["sent"])

    def test_oversized_message_is_rejected_not_truncated(self):
        oversized = "x" * (om.MAX_MESSAGE_LENGTH + 500)
        with patch("agent.openclaw_messaging._send_raw") as mock_send:
            om.send_message("telegram", "allowed-target-1", oversized)
        mock_send.assert_not_called()

    def test_missing_channel_rejected(self):
        with patch("agent.openclaw_messaging._send_raw") as mock_send:
            result = om.send_message(None, "allowed-target-1", "hi")
        mock_send.assert_not_called()
        self.assertFalse(result["sent"])

    def test_missing_target_rejected(self):
        with patch("agent.openclaw_messaging._send_raw") as mock_send:
            result = om.send_message("telegram", None, "hi")
        mock_send.assert_not_called()
        self.assertFalse(result["sent"])

    def test_no_media_params_ever_emitted(self):
        with patch("agent.openclaw_messaging._send_raw", return_value={"messageId": "m"}) as mock_send:
            om.send_message("telegram", "allowed-target-1", "hi")
        sent_params = mock_send.call_args[0][0]
        for forbidden in ("mediaUrl", "mediaUrls", "buffer", "filename", "contentType", "asVoice", "gifPlayback"):
            self.assertNotIn(forbidden, sent_params)


class TestSecurity(unittest.TestCase):

    def setUp(self):
        self._orig_msg_enabled = settings.openclaw_messaging_enabled
        self._orig_channels = settings.openclaw_allowed_channels
        self._orig_targets = settings.openclaw_allowed_targets
        object.__setattr__(settings, "openclaw_messaging_enabled", True)
        object.__setattr__(settings, "openclaw_allowed_channels", "telegram")
        object.__setattr__(settings, "openclaw_allowed_targets", "telegram:allowed-target-1")

    def tearDown(self):
        object.__setattr__(settings, "openclaw_messaging_enabled", self._orig_msg_enabled)
        object.__setattr__(settings, "openclaw_allowed_channels", self._orig_channels)
        object.__setattr__(settings, "openclaw_allowed_targets", self._orig_targets)

    def test_no_raw_rpc_function_exists_on_the_messaging_module(self):
        public_names = [n for n in dir(om) if not n.startswith("_")]
        self.assertNotIn("openclaw_raw_rpc", public_names)
        self.assertNotIn("call_rpc", public_names)
        self.assertNotIn("send_raw_rpc", public_names)

    def test_message_body_never_appears_in_full_in_log_events(self):
        long_secret_looking_message = "the launch code is ABC123XYZ" * 20  # > preview length
        with patch("agent.openclaw_messaging._send_raw", return_value={"messageId": "m"}), \
             patch("agent.openclaw_messaging.log_event") as mock_log:
            om.send_message("telegram", "allowed-target-1", long_secret_looking_message)
        for call in mock_log.call_args_list:
            for value in list(call.args) + list(call.kwargs.values()):
                if isinstance(value, str):
                    self.assertNotIn(long_secret_looking_message, value)

    def test_no_token_or_key_material_in_log_events(self):
        with patch("agent.openclaw_messaging._send_raw", return_value={"messageId": "m"}), \
             patch("agent.openclaw_messaging.log_event") as mock_log:
            om.send_message("telegram", "allowed-target-1", "hi")
        for call in mock_log.call_args_list:
            dumped = json.dumps({"args": [str(a) for a in call.args], "kwargs": {k: str(v) for k, v in call.kwargs.items()}})
            self.assertNotIn("fake-bootstrap-token", dumped)
            self.assertNotIn("BEGIN PRIVATE KEY", dumped)


class TestFailureIsolation(unittest.TestCase):

    def test_send_message_never_raises_on_openclaw_error(self):
        with patch("agent.openclaw_messaging._send_raw", side_effect=gw.OpenClawUnavailable("gateway down")):
            object.__setattr__(settings, "openclaw_messaging_enabled", True)
            try:
                object.__setattr__(settings, "openclaw_allowed_channels", "telegram")
                object.__setattr__(settings, "openclaw_allowed_targets", "telegram:t1")
                result = om.send_message("telegram", "t1", "hi")
            finally:
                object.__setattr__(settings, "openclaw_messaging_enabled", False)
                object.__setattr__(settings, "openclaw_allowed_channels", "")
                object.__setattr__(settings, "openclaw_allowed_targets", "")
        self.assertFalse(result["sent"])
        self.assertEqual(result["delivery_status"], "failed")

    def test_missing_message_credentials_do_not_affect_read_status(self):
        # M1's own device identity/token (or lack of a message-profile
        # one) must have zero bearing on get_status()/get_node_list() --
        # completely separate secrets, completely separate profile.
        with patch("agent.openclaw_gateway.openclaw_configured", return_value=False):
            result = gw.get_status()
        self.assertEqual(result["configured"], False)

    def test_openclaw_disabled_entirely_does_not_break_send_message_normalization(self):
        object.__setattr__(settings, "openclaw_enabled", False)
        object.__setattr__(settings, "openclaw_messaging_enabled", True)
        object.__setattr__(settings, "openclaw_allowed_channels", "telegram")
        object.__setattr__(settings, "openclaw_allowed_targets", "telegram:t1")
        try:
            result = om.send_message("telegram", "t1", "hi")
        finally:
            object.__setattr__(settings, "openclaw_enabled", False)
            object.__setattr__(settings, "openclaw_messaging_enabled", False)
            object.__setattr__(settings, "openclaw_allowed_channels", "")
            object.__setattr__(settings, "openclaw_allowed_targets", "")
        self.assertFalse(result["sent"])
        self.assertEqual(result["delivery_status"], "failed")


class TestExecutorVerificationIntegration(unittest.TestCase):
    """Executor-level regression test (agent/executor.py's _run_tool,
    real dispatch through tools/registry.py's send_message_via_openclaw
    ToolSpec, not a bare call to agent.openclaw_messaging.send_message):
    proves an uncertain OpenClaw delivery is surfaced to Jarvis as a
    failed/unverified side effect, with the dedicated verifier's note
    attached, rather than silently read as verified success."""

    def setUp(self):
        self._orig_msg_enabled = settings.openclaw_messaging_enabled
        self._orig_channels = settings.openclaw_allowed_channels
        self._orig_targets = settings.openclaw_allowed_targets
        object.__setattr__(settings, "openclaw_messaging_enabled", True)
        object.__setattr__(settings, "openclaw_allowed_channels", "telegram")
        object.__setattr__(settings, "openclaw_allowed_targets", "telegram:allowed-target-1")

    def tearDown(self):
        object.__setattr__(settings, "openclaw_messaging_enabled", self._orig_msg_enabled)
        object.__setattr__(settings, "openclaw_allowed_channels", self._orig_channels)
        object.__setattr__(settings, "openclaw_allowed_targets", self._orig_targets)

    def test_uncertain_delivery_is_surfaced_as_unverified_not_silent_success(self):
        context = RequestContext.create("send a telegram message", source="chat")
        tool_input = {"channel": "telegram", "target": "allowed-target-1", "message": "hi"}
        with patch(
            "agent.openclaw_messaging._send_raw",
            side_effect=gw.OpenClawUncertainDelivery("no response"),
        ):
            result = _run_tool("send_message_via_openclaw", tool_input, source="chat", context=context)

        payload = json.loads(result.split("\n\n(Verification note:")[0])
        self.assertEqual(payload["delivery_status"], "uncertain")
        self.assertIn("Verification note:", result)
        self.assertIn("must NOT", result)
        self.assertIn("successful", result.lower())

    def test_confirmed_delivery_carries_no_verification_note(self):
        context = RequestContext.create("send a telegram message", source="chat")
        tool_input = {"channel": "telegram", "target": "allowed-target-1", "message": "hi"}
        with patch(
            "agent.openclaw_messaging._send_raw",
            return_value={"messageId": "m1"},
        ):
            result = _run_tool("send_message_via_openclaw", tool_input, source="chat", context=context)

        self.assertNotIn("Verification note:", result)
        payload = json.loads(result)
        self.assertEqual(payload["delivery_status"], "confirmed")


if __name__ == "__main__":
    unittest.main()
