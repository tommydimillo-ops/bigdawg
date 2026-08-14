"""Standalone worker for on-device speech transcription -- run as a
separate OS process (via subprocess.run(..., timeout=...) in
voice/local_transcribe.py) specifically because
SFSpeechRecognitionTask.cancel() does not reliably stop macOS's
underlying recognition work. Confirmed live via CPU profiling: calling
cancel() on timeout, and even unconditionally on every completion path,
still left abandoned Speech.Task.Internal/SFLocalSpeechRecognitionClient
dispatch queues consuming hundreds of percent CPU minutes later.

A subprocess killed by subprocess.run's own timeout handling is an OS-
level guarantee no in-process cancellation API can match: whatever the
process was using is reliably reclaimed the instant it's terminated,
regardless of whether Apple's framework itself would have released it.

Not meant to be imported -- run directly with a single argument (the
path to the WAV file to transcribe). Prints the recognized text to
stdout and exits 0 on success; exits non-zero with nothing on stdout on
any failure (unavailable, denied, no result, recognition error).
"""
import sys
import threading


def main() -> int:
    if len(sys.argv) != 2:
        return 1
    path = sys.argv[1]

    try:
        import Speech
        from Foundation import NSURL
    except ImportError:
        return 1

    try:
        if (
            Speech.SFSpeechRecognizer.authorizationStatus()
            != Speech.SFSpeechRecognizerAuthorizationStatusAuthorized
        ):
            return 1

        recognizer = Speech.SFSpeechRecognizer.alloc().init()
        if not (
            recognizer
            and recognizer.isAvailable()
            and recognizer.supportsOnDeviceRecognition()
        ):
            return 1

        request = Speech.SFSpeechURLRecognitionRequest.alloc().initWithURL_(
            NSURL.fileURLWithPath_(path)
        )
        request.setRequiresOnDeviceRecognition_(True)
        request.setShouldReportPartialResults_(False)

        done = threading.Event()
        result = {}

        def _on_result(speech_result, error):
            if error is not None:
                result["error"] = error
                done.set()
                return
            if speech_result is not None and speech_result.isFinal():
                result["text"] = str(
                    speech_result.bestTranscription().formattedString()
                )
                done.set()

        recognizer.recognitionTaskWithRequest_resultHandler_(request, _on_result)
        # Generous -- the PARENT process enforces the real deadline via
        # subprocess.run(timeout=...) and kills this whole process if
        # it's exceeded, so there's no benefit to a tight wait here.
        done.wait(timeout=25)

        if "text" in result:
            print(result["text"])
            return 0
        return 1

    except Exception:
        return 1


if __name__ == "__main__":
    sys.exit(main())
