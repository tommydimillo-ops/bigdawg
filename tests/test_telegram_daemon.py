"""Tests for agent/telegram_daemon.py's inbound message handling -- the
owner gate, the reset command, non-text handling, and the normal
turn (feed the owner's text to execute_task, send the reply back).
execute_task and the bridge's send_message are mocked; the daemon's own
logic is real.

Run with: python -m unittest tests.test_telegram_daemon -v
"""
import unittest
from unittest.mock import patch

import agent.telegram_daemon as daemon
from config.settings import settings


def _msg(text=None, chat_id="42", **extra):
    m = {"chat": {"id": chat_id}}
    if text is not None:
        m["text"] = text
    m.update(extra)
    return m


class TelegramDaemonTestCase(unittest.TestCase):
    def setUp(self):
        self._orig_owner = settings.telegram_owner_chat_id
        object.__setattr__(settings, "telegram_owner_chat_id", "42")
        self._send = patch("agent.telegram_daemon.send_message").start()
        self._exec = patch("agent.telegram_daemon.execute_task", return_value="the reply").start()

    def tearDown(self):
        patch.stopall()
        object.__setattr__(settings, "telegram_owner_chat_id", self._orig_owner)


class TestHandleMessage(TelegramDaemonTestCase):

    def test_owner_text_runs_a_turn_and_sends_the_reply(self):
        history = []
        daemon._handle_message(_msg("what's the weather"), history)
        self._exec.assert_called_once()
        args, kwargs = self._exec.call_args
        self.assertEqual(args[0], "what's the weather")
        self.assertEqual(kwargs.get("source") or args[2], "telegram")
        self._send.assert_called_once_with("the reply")
        self.assertEqual(
            history,
            [{"role": "user", "content": "what's the weather"},
             {"role": "assistant", "content": "the reply"}],
        )

    def test_non_owner_message_is_dropped_before_the_agent(self):
        history = []
        daemon._handle_message(_msg("do something", chat_id="9999"), history)
        self._exec.assert_not_called()
        self._send.assert_not_called()
        self.assertEqual(history, [])

    def test_reset_command_clears_history_without_a_turn(self):
        history = [{"role": "user", "content": "old"}, {"role": "assistant", "content": "older"}]
        daemon._handle_message(_msg("/reset"), history)
        self._exec.assert_not_called()
        self.assertEqual(history, [])
        self._send.assert_called_once()

    def test_non_text_message_gets_a_text_only_notice(self):
        history = []
        daemon._handle_message(_msg(text=None, voice={"file_id": "abc"}), history)
        self._exec.assert_not_called()
        self._send.assert_called_once()
        self.assertIn("text", self._send.call_args.args[0].lower())

    def test_agent_exception_is_contained_and_the_user_turn_is_rolled_back(self):
        self._exec.side_effect = RuntimeError("boom")
        history = []
        daemon._handle_message(_msg("trigger a crash"), history)
        self.assertEqual(history, [])  # no dangling user turn
        self._send.assert_called_once()
        self.assertIn("went wrong", self._send.call_args.args[0].lower())

    def test_history_is_bounded(self):
        history = [{"role": "user", "content": f"m{i}"} for i in range(daemon._MAX_HISTORY_MESSAGES)]
        daemon._handle_message(_msg("newest"), history)
        self.assertLessEqual(len(history), daemon._MAX_HISTORY_MESSAGES)
        self.assertEqual(history[-1], {"role": "assistant", "content": "the reply"})


class TestRunForeverGuards(unittest.TestCase):

    def test_refuses_to_start_when_not_configured(self):
        with patch("agent.telegram_daemon.is_configured", return_value=False):
            with self.assertRaises(SystemExit):
                daemon.run_forever()


if __name__ == "__main__":
    unittest.main()
