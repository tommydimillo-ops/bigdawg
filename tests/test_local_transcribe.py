"""Tests for voice/local_transcribe.py -- the on-device Speech-framework
fallback used when the primary, cloud-based transcription fails. No real
recognition, permission dialogs, or subprocesses happen here: Speech and
subprocess.run are replaced with mocks entirely, since this module's own
logic (status checks, subprocess orchestration, graceful degradation) is
what's under test, not Apple's actual on-device model or the worker
script's internals.

Run with: python -m unittest tests.test_local_transcribe -v
"""
import subprocess
import sys
import unittest
from unittest.mock import MagicMock, patch

import voice.local_transcribe as local_transcribe


class TestFrameworkUnavailable(unittest.TestCase):

    @patch("voice.local_transcribe._AVAILABLE", False)
    def test_is_available_false_when_framework_missing(self):
        self.assertFalse(local_transcribe.is_available())

    @patch("voice.local_transcribe._AVAILABLE", False)
    def test_request_authorization_false_when_framework_missing(self):
        self.assertFalse(local_transcribe.request_authorization())

    @patch("voice.local_transcribe._AVAILABLE", False)
    def test_transcribe_local_none_when_framework_missing(self):
        self.assertIsNone(local_transcribe.transcribe_local("/tmp/x.wav"))


class TestIsAvailable(unittest.TestCase):

    @patch("voice.local_transcribe.Speech")
    def test_true_when_authorized_and_supported(self, mock_speech):
        mock_speech.SFSpeechRecognizerAuthorizationStatusAuthorized = 3
        mock_speech.SFSpeechRecognizer.authorizationStatus.return_value = 3
        recognizer = MagicMock()
        recognizer.isAvailable.return_value = True
        recognizer.supportsOnDeviceRecognition.return_value = True
        mock_speech.SFSpeechRecognizer.alloc.return_value.init.return_value = recognizer
        self.assertTrue(local_transcribe.is_available())

    @patch("voice.local_transcribe.Speech")
    def test_false_when_not_authorized(self, mock_speech):
        mock_speech.SFSpeechRecognizerAuthorizationStatusAuthorized = 3
        mock_speech.SFSpeechRecognizer.authorizationStatus.return_value = 0  # not determined
        self.assertFalse(local_transcribe.is_available())

    @patch("voice.local_transcribe.Speech")
    def test_false_when_on_device_unsupported(self, mock_speech):
        mock_speech.SFSpeechRecognizerAuthorizationStatusAuthorized = 3
        mock_speech.SFSpeechRecognizer.authorizationStatus.return_value = 3
        recognizer = MagicMock()
        recognizer.isAvailable.return_value = True
        recognizer.supportsOnDeviceRecognition.return_value = False
        mock_speech.SFSpeechRecognizer.alloc.return_value.init.return_value = recognizer
        self.assertFalse(local_transcribe.is_available())

    @patch("voice.local_transcribe.Speech")
    def test_exceptions_degrade_to_false_not_raise(self, mock_speech):
        mock_speech.SFSpeechRecognizer.authorizationStatus.side_effect = RuntimeError("boom")
        self.assertFalse(local_transcribe.is_available())


class TestUsageDescriptionGuard(unittest.TestCase):
    """Regression tests for a real, confirmed production crash: macOS's
    TCC privacy system hard-aborts (SIGABRT, not a catchable Python
    exception) any process that calls SFSpeechRecognizer.
    requestAuthorization_ without NSSpeechRecognitionUsageDescription in
    its own Info.plist. request_authorization() must never reach that
    call at all when the key is missing -- these confirm the guard runs
    first, unconditionally, before anything Speech-framework-related."""

    @patch("voice.local_transcribe.NSBundle")
    def test_has_usage_description_true_when_key_present(self, mock_bundle):
        mock_bundle.mainBundle.return_value.infoDictionary.return_value = {
            "NSSpeechRecognitionUsageDescription": "explains why",
        }
        self.assertTrue(local_transcribe._has_usage_description())

    @patch("voice.local_transcribe.NSBundle")
    def test_has_usage_description_false_when_key_missing(self, mock_bundle):
        mock_bundle.mainBundle.return_value.infoDictionary.return_value = {
            "CFBundleIdentifier": "org.python.python",
        }
        self.assertFalse(local_transcribe._has_usage_description())

    @patch("voice.local_transcribe.NSBundle")
    def test_has_usage_description_false_on_exception(self, mock_bundle):
        mock_bundle.mainBundle.side_effect = RuntimeError("boom")
        self.assertFalse(local_transcribe._has_usage_description())

    @patch("voice.local_transcribe.Speech")
    @patch("voice.local_transcribe._has_usage_description", return_value=False)
    def test_missing_usage_description_never_calls_request_authorization(
        self, mock_has_usage_description, mock_speech,
    ):
        result = local_transcribe.request_authorization()
        self.assertFalse(result)
        mock_speech.SFSpeechRecognizer.requestAuthorization_.assert_not_called()
        # Not even the read-only status check should run first -- the
        # guard is the very first thing this function does.
        mock_speech.SFSpeechRecognizer.authorizationStatus.assert_not_called()


class TestRequestAuthorization(unittest.TestCase):
    """These all assume a correctly-configured bundle (the usage
    description guard above is tested in isolation) -- patched True here
    so the rest of request_authorization()'s logic can be exercised."""

    @patch("voice.local_transcribe._has_usage_description", return_value=True)
    @patch("voice.local_transcribe.Speech")
    def test_already_authorized_returns_true_immediately(self, mock_speech, mock_has_usage_description):
        mock_speech.SFSpeechRecognizerAuthorizationStatusAuthorized = 3
        mock_speech.SFSpeechRecognizer.authorizationStatus.return_value = 3
        self.assertTrue(local_transcribe.request_authorization())
        mock_speech.SFSpeechRecognizer.requestAuthorization_.assert_not_called()

    @patch("voice.local_transcribe._has_usage_description", return_value=True)
    @patch("voice.local_transcribe.Speech")
    def test_denied_returns_false_immediately_without_prompting(self, mock_speech, mock_has_usage_description):
        mock_speech.SFSpeechRecognizerAuthorizationStatusAuthorized = 3
        mock_speech.SFSpeechRecognizerAuthorizationStatusNotDetermined = 0
        mock_speech.SFSpeechRecognizer.authorizationStatus.return_value = 1  # denied
        self.assertFalse(local_transcribe.request_authorization())
        mock_speech.SFSpeechRecognizer.requestAuthorization_.assert_not_called()

    @patch("voice.local_transcribe._has_usage_description", return_value=True)
    @patch("voice.local_transcribe.Speech")
    def test_not_determined_prompts_and_waits_for_callback(self, mock_speech, mock_has_usage_description):
        mock_speech.SFSpeechRecognizerAuthorizationStatusAuthorized = 3
        mock_speech.SFSpeechRecognizerAuthorizationStatusNotDetermined = 0
        mock_speech.SFSpeechRecognizer.authorizationStatus.return_value = 0

        def _fake_request(callback):
            callback(3)  # simulate the user granting access

        mock_speech.SFSpeechRecognizer.requestAuthorization_.side_effect = _fake_request

        self.assertTrue(local_transcribe.request_authorization())

    @patch("voice.local_transcribe._has_usage_description", return_value=True)
    @patch("voice.local_transcribe.Speech")
    def test_not_determined_and_denied_by_user(self, mock_speech, mock_has_usage_description):
        mock_speech.SFSpeechRecognizerAuthorizationStatusAuthorized = 3
        mock_speech.SFSpeechRecognizerAuthorizationStatusNotDetermined = 0
        mock_speech.SFSpeechRecognizer.authorizationStatus.return_value = 0

        def _fake_request(callback):
            callback(1)  # simulate the user denying access

        mock_speech.SFSpeechRecognizer.requestAuthorization_.side_effect = _fake_request

        self.assertFalse(local_transcribe.request_authorization())

    @patch("voice.local_transcribe._has_usage_description", return_value=True)
    @patch("voice.local_transcribe.Speech")
    def test_exception_requesting_authorization_returns_false(self, mock_speech, mock_has_usage_description):
        mock_speech.SFSpeechRecognizerAuthorizationStatusAuthorized = 3
        mock_speech.SFSpeechRecognizerAuthorizationStatusNotDetermined = 0
        mock_speech.SFSpeechRecognizer.authorizationStatus.return_value = 0
        mock_speech.SFSpeechRecognizer.requestAuthorization_.side_effect = RuntimeError("boom")
        self.assertFalse(local_transcribe.request_authorization())


class TestTranscribeLocal(unittest.TestCase):
    """transcribe_local() runs the actual on-device recognition in a
    SEPARATE OS process (voice/_local_transcribe_worker.py), not
    in-process -- confirmed live that SFSpeechRecognitionTask.cancel()
    doesn't reliably stop macOS's recognition work, even called
    unconditionally on every completion path. A subprocess killed by
    subprocess.run's own timeout handling is an OS-level guarantee no
    in-process cancellation could match. These tests mock subprocess.run
    itself -- the worker script's own internal Speech-framework logic
    isn't exercised here (it needs a real recognizer to test meaningfully
    and is intentionally a thin, directly-readable script)."""

    def _mock_available(self, mock_speech):
        mock_speech.SFSpeechRecognizerAuthorizationStatusAuthorized = 3
        mock_speech.SFSpeechRecognizer.authorizationStatus.return_value = 3
        recognizer = MagicMock()
        recognizer.isAvailable.return_value = True
        recognizer.supportsOnDeviceRecognition.return_value = True
        mock_speech.SFSpeechRecognizer.alloc.return_value.init.return_value = recognizer
        return recognizer

    @patch("voice.local_transcribe.Speech")
    def test_returns_none_when_unavailable(self, mock_speech):
        mock_speech.SFSpeechRecognizerAuthorizationStatusAuthorized = 3
        mock_speech.SFSpeechRecognizer.authorizationStatus.return_value = 0
        self.assertIsNone(local_transcribe.transcribe_local("/tmp/x.wav"))

    @patch("voice.local_transcribe.subprocess.run")
    @patch("voice.local_transcribe.Speech")
    def test_returns_text_on_successful_final_result(self, mock_speech, mock_run):
        self._mock_available(mock_speech)
        mock_run.return_value = MagicMock(returncode=0, stdout="what's the weather\n")

        result = local_transcribe.transcribe_local("/tmp/x.wav")
        self.assertEqual(result, "what's the weather")

    @patch("voice.local_transcribe.subprocess.run")
    @patch("voice.local_transcribe.Speech")
    def test_nonzero_exit_returns_none(self, mock_speech, mock_run):
        self._mock_available(mock_speech)
        mock_run.return_value = MagicMock(returncode=1, stdout="")

        self.assertIsNone(local_transcribe.transcribe_local("/tmp/x.wav"))

    @patch("voice.local_transcribe.subprocess.run")
    @patch("voice.local_transcribe.Speech")
    def test_empty_stdout_on_success_returns_none(self, mock_speech, mock_run):
        self._mock_available(mock_speech)
        mock_run.return_value = MagicMock(returncode=0, stdout="  \n")

        self.assertIsNone(local_transcribe.transcribe_local("/tmp/x.wav"))

    @patch("voice.local_transcribe.subprocess.run")
    @patch("voice.local_transcribe.Speech")
    def test_timeout_returns_none(self, mock_speech, mock_run):
        # subprocess.run itself kills the child and waits for it before
        # raising TimeoutExpired -- by the time this exception exists,
        # the OS has already reclaimed everything the worker was using.
        self._mock_available(mock_speech)
        mock_run.side_effect = subprocess.TimeoutExpired(cmd=["worker"], timeout=6)

        result = local_transcribe.transcribe_local("/tmp/x.wav", timeout=6)
        self.assertIsNone(result)

    @patch("voice.local_transcribe.subprocess.run")
    @patch("voice.local_transcribe.Speech")
    def test_timeout_passed_through_to_subprocess_run(self, mock_speech, mock_run):
        self._mock_available(mock_speech)
        mock_run.return_value = MagicMock(returncode=0, stdout="hi\n")

        local_transcribe.transcribe_local("/tmp/x.wav", timeout=9)

        self.assertEqual(mock_run.call_args.kwargs.get("timeout"), 9)

    @patch("voice.local_transcribe.subprocess.run")
    @patch("voice.local_transcribe.Speech")
    def test_worker_invoked_with_the_venv_python_and_audio_path(self, mock_speech, mock_run):
        self._mock_available(mock_speech)
        mock_run.return_value = MagicMock(returncode=0, stdout="hi\n")

        local_transcribe.transcribe_local("/tmp/x.wav")

        args = mock_run.call_args.args[0]
        self.assertEqual(args[0], sys.executable)
        self.assertIn("_local_transcribe_worker.py", args[1])
        self.assertEqual(args[2], "/tmp/x.wav")

    @patch("voice.local_transcribe.subprocess.run")
    @patch("voice.local_transcribe.Speech")
    def test_unexpected_exception_returns_none_not_raise(self, mock_speech, mock_run):
        self._mock_available(mock_speech)
        mock_run.side_effect = OSError("boom")
        result = local_transcribe.transcribe_local("/tmp/x.wav")
        self.assertIsNone(result)


if __name__ == "__main__":
    unittest.main()
