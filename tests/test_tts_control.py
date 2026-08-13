"""Tests for agent/tts_control.py -- the shared PID-tracking mechanism
that lets a wake-word interruption (Phase 6) or a new device's reply
(pre-existing, Streamlit multi-device chat) cut off whatever speech
process is currently playing.

TTS_PID_FILE is redirected to a temp file for every test (matching every
other file-backed store's tests in this project). The `ps`/`os.kill`
calls that check/kill a REAL process are mocked -- this project's
established policy is not to require paid API calls or, here, actually
spawn audio-producing processes just to run the automated test suite.

Run with: python -m unittest tests.test_tts_control -v
"""
import os
import signal
import tempfile
import unittest
from unittest.mock import MagicMock, patch

import agent.tts_control as tts_control


class IsolatedTtsControlTestCase(unittest.TestCase):

    def setUp(self):
        self._real_pid_file = tts_control.TTS_PID_FILE
        tts_control.TTS_PID_FILE = tempfile.mktemp(suffix=".pid")

    def tearDown(self):
        if os.path.exists(tts_control.TTS_PID_FILE):
            os.remove(tts_control.TTS_PID_FILE)
        tts_control.TTS_PID_FILE = self._real_pid_file


def _ps_output(comm):
    result = MagicMock()
    result.stdout = f"{comm}\n"
    return result


class TestTrackPid(IsolatedTtsControlTestCase):

    def test_track_pid_writes_the_pid_file(self):
        tts_control.track_pid(4242)
        with open(tts_control.TTS_PID_FILE) as f:
            self.assertEqual(f.read().strip(), "4242")


class TestIsSpeaking(IsolatedTtsControlTestCase):

    def test_no_pid_file_means_not_speaking(self):
        self.assertFalse(tts_control.is_speaking())

    @patch("agent.tts_control.subprocess.run")
    def test_tracked_say_process_means_speaking(self, mock_run):
        mock_run.return_value = _ps_output("say")
        tts_control.track_pid(111)
        self.assertTrue(tts_control.is_speaking())

    @patch("agent.tts_control.subprocess.run")
    def test_tracked_afplay_process_means_speaking(self, mock_run):
        mock_run.return_value = _ps_output("afplay")
        tts_control.track_pid(111)
        self.assertTrue(tts_control.is_speaking())

    @patch("agent.tts_control.subprocess.run")
    def test_stale_pid_reused_by_an_unrelated_process_is_not_speaking(self, mock_run):
        # A dead/stale pid can get reused by the OS for something else --
        # must not falsely report speaking just because *a* process
        # happens to be at that pid.
        mock_run.return_value = _ps_output("Finder")
        tts_control.track_pid(111)
        self.assertFalse(tts_control.is_speaking())

    @patch("agent.tts_control.subprocess.run")
    def test_exited_process_is_not_speaking(self, mock_run):
        mock_run.return_value = _ps_output("")  # ps returns nothing for a dead pid
        tts_control.track_pid(111)
        self.assertFalse(tts_control.is_speaking())

    def test_corrupt_pid_file_is_not_speaking(self):
        with open(tts_control.TTS_PID_FILE, "w") as f:
            f.write("not-a-pid")
        self.assertFalse(tts_control.is_speaking())


class TestStopSpeaking(IsolatedTtsControlTestCase):

    def test_no_pid_file_is_a_safe_no_op(self):
        tts_control.stop_speaking()  # must not raise

    @patch("agent.tts_control.os.kill")
    @patch("agent.tts_control.subprocess.run")
    def test_kills_a_tracked_say_process(self, mock_run, mock_kill):
        mock_run.return_value = _ps_output("say")
        tts_control.track_pid(222)
        tts_control.stop_speaking()
        mock_kill.assert_called_once_with(222, signal.SIGTERM)

    @patch("agent.tts_control.os.kill")
    @patch("agent.tts_control.subprocess.run")
    def test_kills_a_tracked_afplay_process(self, mock_run, mock_kill):
        mock_run.return_value = _ps_output("afplay")
        tts_control.track_pid(333)
        tts_control.stop_speaking()
        mock_kill.assert_called_once_with(333, signal.SIGTERM)

    @patch("agent.tts_control.os.kill")
    @patch("agent.tts_control.subprocess.run")
    def test_does_not_kill_an_unrelated_stale_pid(self, mock_run, mock_kill):
        mock_run.return_value = _ps_output("Finder")
        tts_control.track_pid(444)
        tts_control.stop_speaking()
        mock_kill.assert_not_called()

    @patch("agent.tts_control.os.kill")
    @patch("agent.tts_control.subprocess.run")
    def test_removes_the_pid_file_either_way(self, mock_run, mock_kill):
        mock_run.return_value = _ps_output("say")
        tts_control.track_pid(555)
        tts_control.stop_speaking()
        self.assertFalse(os.path.exists(tts_control.TTS_PID_FILE))

    @patch("agent.tts_control.os.kill", side_effect=ProcessLookupError)
    @patch("agent.tts_control.subprocess.run")
    def test_process_already_gone_is_handled_gracefully(self, mock_run, mock_kill):
        mock_run.return_value = _ps_output("say")
        tts_control.track_pid(666)
        tts_control.stop_speaking()  # must not raise despite the race


if __name__ == "__main__":
    unittest.main()
