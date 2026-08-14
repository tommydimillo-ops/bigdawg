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

Invoked two ways, both ending up here:
  - `python3 _local_transcribe_worker.py <path>` for standalone/manual
    testing.
  - `CampusPilotAgent.app/Contents/MacOS/CampusPilotAgent
    --transcribe-worker <path>` -- what local_transcribe.py actually
    runs in production. Confirmed live that a plain python subprocess
    gets its OWN, never-authorized TCC identity (Speech Recognition
    authorization does not carry over from the parent, even though the
    parent .app itself is properly authorized); running the SAME
    signed app binary again, in worker mode, keeps the same
    com.tommy.campuspilot.jarvis identity and its existing grant. See
    ui/menu_bar.py's early argv check for the second path -- it exits
    before any of the app's own heavy imports so this stays fast.

Prints the recognized text to stdout and exits 0 on success; exits
non-zero with nothing on stdout (a reason on stderr instead) on any
failure (unavailable, denied, no result, recognition error).
"""
import sys
import threading


def _fail(reason: str) -> int:
    # Diagnostic detail on stderr, not stdout -- the parent only treats
    # stdout as the transcript, but captures stderr too (subprocess.run
    # with capture_output=True) specifically so a failure here is
    # debuggable from the parent's own logs instead of a bare "it didn't
    # work," which is exactly the kind of thing that made this whole
    # class of bug slow to track down in the first place.
    print(reason, file=sys.stderr)
    return 1


def main(path: str = None) -> int:
    if path is None:
        if len(sys.argv) != 2:
            return _fail("wrong argument count")
        path = sys.argv[1]

    try:
        import Speech
        from Foundation import NSURL
    except ImportError:
        return _fail("Speech/Foundation not importable in this process")

    try:
        status = Speech.SFSpeechRecognizer.authorizationStatus()
        if status != Speech.SFSpeechRecognizerAuthorizationStatusAuthorized:
            return _fail(f"not authorized in this process (status={status})")

        recognizer = Speech.SFSpeechRecognizer.alloc().init()
        if not recognizer:
            return _fail("SFSpeechRecognizer.alloc().init() returned nothing")
        if not recognizer.isAvailable():
            return _fail("recognizer.isAvailable() is False")
        if not recognizer.supportsOnDeviceRecognition():
            return _fail("recognizer.supportsOnDeviceRecognition() is False")

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
        finished = done.wait(timeout=25)

        if not finished:
            return _fail("recognition callback never fired within the internal 25s wait")
        if "text" in result:
            print(result["text"])
            return 0
        return _fail(f"recognition error callback fired: {result.get('error')!r}")

    except Exception as error:
        return _fail(f"unexpected exception: {type(error).__name__}: {error}")


if __name__ == "__main__":
    sys.exit(main())
