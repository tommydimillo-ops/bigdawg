"""Tests for agent/voice_state.py -- the voice-interface state machine and
the busy-state protection lock. STATE_FILE (via agent.jarvis_state, which
get_status() reads through to) is redirected to a temp file for every test
here, matching every other file-backed store's tests in this project, so
none of this touches the real ~/Library/.../jarvis_state.json.

Run with: python -m unittest tests.test_voice_state -v
"""
import tempfile
import unittest

import agent.jarvis_state as jarvis_state
import agent.voice_state as voice_state
from agent.execution_state import ExecutionStatus
from agent.voice_state import VoiceState


class IsolatedVoiceStateTestCase(unittest.TestCase):

    def setUp(self):
        self._real_state_file = jarvis_state.STATE_FILE
        jarvis_state.STATE_FILE = tempfile.mktemp(suffix=".json")
        voice_state.reset_to_idle()
        # The busy-lock is process-global and independent of the state
        # file -- make sure a prior test's lock never leaks into the next.
        voice_state.finish()

    def tearDown(self):
        jarvis_state.STATE_FILE = self._real_state_file
        voice_state.finish()


class TestSetAndGetStatus(IsolatedVoiceStateTestCase):

    def test_defaults_to_idle(self):
        self.assertEqual(voice_state.get_status(), VoiceState.IDLE)

    def test_set_status_is_reflected_by_get_status(self):
        voice_state.set_status(VoiceState.LISTENING)
        self.assertEqual(voice_state.get_status(), VoiceState.LISTENING)

    def test_reset_to_idle(self):
        voice_state.set_status(VoiceState.SPEAKING)
        voice_state.reset_to_idle()
        self.assertEqual(voice_state.get_status(), VoiceState.IDLE)

    def test_error_and_cancelled_are_reflected_directly(self):
        voice_state.set_status(VoiceState.ERROR)
        self.assertEqual(voice_state.get_status(), VoiceState.ERROR)
        voice_state.set_status(VoiceState.CANCELLED)
        self.assertEqual(voice_state.get_status(), VoiceState.CANCELLED)


class TestTaskPhaseTakesPrecedence(IsolatedVoiceStateTestCase):
    """A real task in flight (reflected via agent.jarvis_state, Phase 5)
    must always be what get_status() reports, regardless of whatever
    voice-local phase was last set -- this is the whole point of composing
    the two instead of tracking task phases a second time here."""

    def test_jarvis_thinking_overrides_voice_local_listening(self):
        voice_state.set_status(VoiceState.LISTENING)
        jarvis_state.set_status(ExecutionStatus.THINKING)
        self.assertEqual(voice_state.get_status(), VoiceState.THINKING)

    def test_jarvis_executing_overrides_voice_local_speaking(self):
        voice_state.set_status(VoiceState.SPEAKING)
        jarvis_state.set_status(ExecutionStatus.EXECUTING)
        self.assertEqual(voice_state.get_status(), VoiceState.EXECUTING)

    def test_jarvis_waiting_for_confirmation_is_reflected(self):
        jarvis_state.set_status(ExecutionStatus.WAITING_FOR_CONFIRMATION)
        self.assertEqual(voice_state.get_status(), VoiceState.WAITING_FOR_CONFIRMATION)

    def test_jarvis_planning_is_reflected(self):
        jarvis_state.set_status(ExecutionStatus.PLANNING)
        self.assertEqual(voice_state.get_status(), VoiceState.PLANNING)

    def test_jarvis_idle_falls_back_to_voice_local_phase(self):
        # IDLE is not one of the "task in flight" phases -- once the task
        # is done, voice's own last-set phase (e.g. SPEAKING the reply)
        # should show through again.
        voice_state.set_status(VoiceState.SPEAKING)
        jarvis_state.reset_to_idle()
        self.assertEqual(voice_state.get_status(), VoiceState.SPEAKING)

    def test_jarvis_completed_falls_back_to_voice_local_phase(self):
        voice_state.set_status(VoiceState.IDLE)
        jarvis_state.set_status(ExecutionStatus.COMPLETED)
        self.assertEqual(voice_state.get_status(), VoiceState.IDLE)


class TestBusyStateProtection(IsolatedVoiceStateTestCase):

    def test_starts_not_busy(self):
        self.assertFalse(voice_state.is_busy())

    def test_try_start_succeeds_when_free(self):
        self.assertTrue(voice_state.try_start())
        self.assertTrue(voice_state.is_busy())

    def test_try_start_fails_when_already_busy(self):
        voice_state.try_start()
        self.assertFalse(voice_state.try_start())

    def test_finish_releases_the_lock(self):
        voice_state.try_start()
        voice_state.finish()
        self.assertFalse(voice_state.is_busy())

    def test_finish_when_not_busy_is_a_safe_no_op(self):
        voice_state.finish()  # never started -- must not raise
        self.assertFalse(voice_state.is_busy())

    def test_second_request_cannot_start_while_first_is_active(self):
        # The exact scenario section 10 is about: a voice request and a
        # scheduled task (or two voice requests) racing to both start.
        self.assertTrue(voice_state.try_start())  # request A
        self.assertFalse(voice_state.try_start())  # request B -- must be refused
        voice_state.finish()
        self.assertTrue(voice_state.try_start())  # now free again


if __name__ == "__main__":
    unittest.main()
