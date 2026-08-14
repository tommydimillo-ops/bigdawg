"""Tests for voice/local_transcribe.py -- the on-device Speech-framework
fallback used when the primary, cloud-based transcription fails. No real
recognition or permission dialogs happen here: Speech/NSURL are replaced
with mocks entirely, since this module's own logic (status checks,
blocking-wait-for-callback bridging, graceful degradation) is what's
under test, not Apple's actual on-device model.

Run with: python -m unittest tests.test_local_transcribe -v
"""
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

    @patch("voice.local_transcribe.NSURL")
    @patch("voice.local_transcribe.Speech")
    def test_returns_text_on_successful_final_result(self, mock_speech, mock_nsurl):
        recognizer = self._mock_available(mock_speech)

        speech_result = MagicMock()
        speech_result.isFinal.return_value = True
        speech_result.bestTranscription.return_value.formattedString.return_value = "what's the weather"

        def _fake_task(request, handler):
            handler(speech_result, None)
            return MagicMock()

        recognizer.recognitionTaskWithRequest_resultHandler_.side_effect = _fake_task

        result = local_transcribe.transcribe_local("/tmp/x.wav")
        self.assertEqual(result, "what's the weather")

    @patch("voice.local_transcribe.NSURL")
    @patch("voice.local_transcribe.Speech")
    def test_returns_none_on_recognition_error(self, mock_speech, mock_nsurl):
        recognizer = self._mock_available(mock_speech)

        def _fake_task(request, handler):
            handler(None, RuntimeError("recognition failed"))
            return MagicMock()

        recognizer.recognitionTaskWithRequest_resultHandler_.side_effect = _fake_task

        self.assertIsNone(local_transcribe.transcribe_local("/tmp/x.wav"))

    @patch("voice.local_transcribe.NSURL")
    @patch("voice.local_transcribe.Speech")
    def test_ignores_non_final_partial_results(self, mock_speech, mock_nsurl):
        recognizer = self._mock_available(mock_speech)

        partial = MagicMock()
        partial.isFinal.return_value = False
        final = MagicMock()
        final.isFinal.return_value = True
        final.bestTranscription.return_value.formattedString.return_value = "done"

        def _fake_task(request, handler):
            handler(partial, None)
            handler(final, None)
            return MagicMock()

        recognizer.recognitionTaskWithRequest_resultHandler_.side_effect = _fake_task

        result = local_transcribe.transcribe_local("/tmp/x.wav")
        self.assertEqual(result, "done")

    @patch("voice.local_transcribe.NSURL")
    @patch("voice.local_transcribe.Speech")
    def test_timeout_with_no_callback_returns_none(self, mock_speech, mock_nsurl):
        recognizer = self._mock_available(mock_speech)
        mock_task = MagicMock()
        recognizer.recognitionTaskWithRequest_resultHandler_.return_value = mock_task
        result = local_transcribe.transcribe_local("/tmp/x.wav", timeout=0.1)
        self.assertIsNone(result)

    @patch("voice.local_transcribe.NSURL")
    @patch("voice.local_transcribe.Speech")
    def test_timeout_cancels_the_abandoned_recognition_task(self, mock_speech, mock_nsurl):
        # Regression test for a real, confirmed production bug: an
        # abandoned recognition task that isn't explicitly cancelled on
        # timeout keeps running in the background indefinitely -- a live
        # CPU profile showed multiple leaked tasks from earlier timed-out
        # calls still consuming real CPU minutes later, concurrently.
        recognizer = self._mock_available(mock_speech)
        mock_task = MagicMock()
        recognizer.recognitionTaskWithRequest_resultHandler_.return_value = mock_task
        local_transcribe.transcribe_local("/tmp/x.wav", timeout=0.1)
        mock_task.cancel.assert_called_once()

    @patch("voice.local_transcribe.NSURL")
    @patch("voice.local_transcribe.Speech")
    def test_successful_result_still_cancels_the_task(self, mock_speech, mock_nsurl):
        # A completion handler firing doesn't mean the task's underlying
        # resources are actually released -- confirmed live via a CPU
        # profile that tasks which completed through the error callback
        # (not just ones that timed out) were still found running
        # minutes later. cancel() must run unconditionally.
        recognizer = self._mock_available(mock_speech)
        mock_task = MagicMock()

        speech_result = MagicMock()
        speech_result.isFinal.return_value = True
        speech_result.bestTranscription.return_value.formattedString.return_value = "done"

        def _fake_task(request, handler):
            handler(speech_result, None)
            return mock_task

        recognizer.recognitionTaskWithRequest_resultHandler_.side_effect = _fake_task
        local_transcribe.transcribe_local("/tmp/x.wav")
        mock_task.cancel.assert_called_once()

    @patch("voice.local_transcribe.NSURL")
    @patch("voice.local_transcribe.Speech")
    def test_error_result_still_cancels_the_task(self, mock_speech, mock_nsurl):
        recognizer = self._mock_available(mock_speech)
        mock_task = MagicMock()

        def _fake_task(request, handler):
            handler(None, RuntimeError("recognition failed"))
            return mock_task

        recognizer.recognitionTaskWithRequest_resultHandler_.side_effect = _fake_task
        local_transcribe.transcribe_local("/tmp/x.wav")
        mock_task.cancel.assert_called_once()

    @patch("voice.local_transcribe.NSURL")
    @patch("voice.local_transcribe.Speech")
    def test_unexpected_exception_returns_none_not_raise(self, mock_speech, mock_nsurl):
        self._mock_available(mock_speech)
        mock_speech.SFSpeechURLRecognitionRequest.alloc.return_value.initWithURL_.side_effect = RuntimeError("boom")
        result = local_transcribe.transcribe_local("/tmp/x.wav")
        self.assertIsNone(result)


if __name__ == "__main__":
    unittest.main()
