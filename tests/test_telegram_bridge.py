"""Tests for agent/telegram_bridge.py -- the direct api.telegram.org
bridge behind the two-way Telegram integration. The one external boundary
(a `curl` subprocess) is mocked; everything else is real.

Run with: python -m unittest tests.test_telegram_bridge -v
"""
import json
import unittest
from unittest.mock import MagicMock, patch

import agent.telegram_bridge as tb
from config.settings import settings
from tools import registry


def _curl_ok(result):
    return MagicMock(returncode=0, stdout=json.dumps({"ok": True, "result": result}))


def _curl_api_error(description):
    return MagicMock(returncode=0, stdout=json.dumps({"ok": False, "description": description}))


class TelegramBridgeTestCase(unittest.TestCase):
    def setUp(self):
        self._orig = {
            "e": settings.telegram_enabled,
            "o": settings.telegram_owner_chat_id,
            "t": settings.telegram_poll_timeout_seconds,
        }
        object.__setattr__(settings, "telegram_enabled", True)
        object.__setattr__(settings, "telegram_owner_chat_id", "999001")
        self._secret = patch("agent.telegram_bridge.get_secret", return_value="bot-token-xyz")
        self._secret.start()

    def tearDown(self):
        self._secret.stop()
        object.__setattr__(settings, "telegram_enabled", self._orig["e"])
        object.__setattr__(settings, "telegram_owner_chat_id", self._orig["o"])
        object.__setattr__(settings, "telegram_poll_timeout_seconds", self._orig["t"])


class TestConfiguredAndOwnerGate(TelegramBridgeTestCase):

    def test_is_configured_true_when_enabled_owner_and_token_present(self):
        self.assertTrue(tb.is_configured())

    def test_not_configured_without_a_token(self):
        self._secret.stop()
        with patch("agent.telegram_bridge.get_secret", return_value=None):
            self.assertFalse(tb.is_configured())
        self._secret.start()

    def test_not_configured_when_disabled(self):
        object.__setattr__(settings, "telegram_enabled", False)
        self.assertFalse(tb.is_configured())

    def test_not_configured_without_an_owner_chat_id(self):
        object.__setattr__(settings, "telegram_owner_chat_id", "")
        self.assertFalse(tb.is_configured())

    def test_is_owner_matches_only_the_exact_configured_id(self):
        self.assertTrue(tb.is_owner("999001"))
        self.assertTrue(tb.is_owner(999001))
        self.assertFalse(tb.is_owner("999002"))
        self.assertFalse(tb.is_owner(""))

    def test_empty_owner_matches_nothing(self):
        object.__setattr__(settings, "telegram_owner_chat_id", "")
        self.assertFalse(tb.is_owner(""))
        self.assertFalse(tb.is_owner("0"))


class TestSendMessage(TelegramBridgeTestCase):

    def test_sends_to_the_owner_and_reports_one_chunk(self):
        with patch("agent.telegram_bridge.subprocess.run", return_value=_curl_ok({"message_id": 1})) as run:
            result = tb.send_message("hello from jarvis")
        self.assertEqual(result, {"sent": True, "chunks": 1, "error": None})
        sent_body = json.loads(run.call_args.args[0][run.call_args.args[0].index("-d") + 1])
        self.assertEqual(sent_body["chat_id"], "999001")
        self.assertEqual(sent_body["text"], "hello from jarvis")

    def test_long_message_is_split_not_truncated(self):
        long_text = "x" * (tb.MAX_MESSAGE_LENGTH * 2 + 10)
        with patch("agent.telegram_bridge.subprocess.run", return_value=_curl_ok({})) as run:
            result = tb.send_message(long_text)
        self.assertTrue(result["sent"])
        self.assertEqual(result["chunks"], 3)
        self.assertEqual(run.call_count, 3)
        total = sum(
            len(json.loads(c.args[0][c.args[0].index("-d") + 1])["text"])
            for c in run.call_args_list
        )
        self.assertEqual(total, len(long_text))

    def test_empty_text_becomes_a_placeholder_not_an_empty_send(self):
        with patch("agent.telegram_bridge.subprocess.run", return_value=_curl_ok({})) as run:
            tb.send_message("   ")
        body = json.loads(run.call_args.args[0][run.call_args.args[0].index("-d") + 1])
        self.assertEqual(body["text"], "(empty response)")

    def test_refuses_a_non_owner_chat_id(self):
        with patch("agent.telegram_bridge.subprocess.run") as run:
            result = tb.send_message("hi", chat_id="555")
        run.assert_not_called()
        self.assertFalse(result["sent"])
        self.assertIn("non-owner", result["error"])

    def test_never_raises_on_api_error_returns_normalized_failure(self):
        with patch("agent.telegram_bridge.subprocess.run", return_value=_curl_api_error("chat not found")):
            result = tb.send_message("hi")
        self.assertFalse(result["sent"])
        self.assertIn("chat not found", result["error"])

    def test_returns_failure_when_bridge_not_configured(self):
        object.__setattr__(settings, "telegram_enabled", False)
        with patch("agent.telegram_bridge.subprocess.run") as run:
            result = tb.send_message("hi")
        run.assert_not_called()
        self.assertFalse(result["sent"])


class TestGetUpdates(TelegramBridgeTestCase):

    def test_passes_offset_and_message_only_filter(self):
        with patch("agent.telegram_bridge.subprocess.run", return_value=_curl_ok([{"update_id": 5}])) as run:
            updates = tb.get_updates(offset=42)
        self.assertEqual(updates, [{"update_id": 5}])
        body = json.loads(run.call_args.args[0][run.call_args.args[0].index("-d") + 1])
        self.assertEqual(body["offset"], 42)
        self.assertEqual(body["allowed_updates"], ["message"])

    def test_raises_telegram_error_on_transport_failure(self):
        with patch("agent.telegram_bridge.subprocess.run", return_value=MagicMock(returncode=7, stdout="")):
            with self.assertRaises(tb.TelegramError):
                tb.get_updates()

    def test_raises_telegram_error_on_unreadable_body(self):
        with patch("agent.telegram_bridge.subprocess.run", return_value=MagicMock(returncode=0, stdout="not json")):
            with self.assertRaises(tb.TelegramError):
                tb.get_updates()


class TestOffsetPersistence(TelegramBridgeTestCase):

    def test_round_trips_through_the_redirected_offset_file(self):
        tb.save_offset(12345)
        self.assertEqual(tb.load_offset(), 12345)

    def test_missing_or_corrupt_offset_file_reads_as_zero(self):
        import os
        if os.path.exists(tb.OFFSET_FILE):
            os.remove(tb.OFFSET_FILE)
        self.assertEqual(tb.load_offset(), 0)
        with open(tb.OFFSET_FILE, "w") as handle:
            handle.write("{{ not json")
        self.assertEqual(tb.load_offset(), 0)


class TestSendTelegramMessageTool(unittest.TestCase):

    def test_registered_with_the_expected_permission_shape(self):
        spec = registry.get("send_telegram_message")
        self.assertIsNotNone(spec)
        self.assertEqual(spec.permission_level, 3)
        self.assertTrue(spec.side_effect)
        self.assertTrue(spec.unattended_allowed)
        self.assertFalse(spec.requires_live_confirmation)
        self.assertFalse(spec.parallel_safe)

    def test_has_no_recipient_parameter(self):
        props = registry.get("send_telegram_message").input_schema["properties"]
        self.assertEqual(set(props), {"message"})

    def test_dispatch_delegates_to_the_bridge(self):
        with patch("tools.schemas.telegram.send_message", return_value={"sent": True, "chunks": 1, "error": None}) as send:
            out = registry.dispatch("send_telegram_message", {"message": "ping"})
        send.assert_called_once_with(text="ping")
        self.assertEqual(json.loads(out)["sent"], True)


if __name__ == "__main__":
    unittest.main()
