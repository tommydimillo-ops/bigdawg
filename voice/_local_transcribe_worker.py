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
import time

_start = time.monotonic()


def _log(message: str) -> None:
    # Timestamped, immediately-flushed breadcrumbs on stderr -- if the
    # PARENT's subprocess.run(timeout=...) kills this process partway
    # through, subprocess.TimeoutExpired still carries whatever was
    # written to the pipe before the kill, so this is what makes a
    # "it timed out" failure debuggable instead of a total black box
    # about which stage actually took the time.
    print(f"[{time.monotonic() - _start:6.2f}s] {message}", file=sys.stderr, flush=True)


def _fail(reason: str) -> int:
    _log(f"FAIL: {reason}")
    return 1


def main(path: str = None) -> int:
    _log("worker started")
    if path is None:
        if len(sys.argv) != 2:
            return _fail("wrong argument count")
        path = sys.argv[1]

    try:
        import Speech
        from Foundation import NSURL
        _log("Speech/Foundation imported")
    except ImportError:
        return _fail("Speech/Foundation not importable in this process")

    try:
        status = Speech.SFSpeechRecognizer.authorizationStatus()
        _log(f"authorization status: {status}")
        if status != Speech.SFSpeechRecognizerAuthorizationStatusAuthorized:
            return _fail(f"not authorized in this process (status={status})")

        recognizer = Speech.SFSpeechRecognizer.alloc().init()
        if not recognizer:
            return _fail("SFSpeechRecognizer.alloc().init() returned nothing")
        if not recognizer.isAvailable():
            return _fail("recognizer.isAvailable() is False")
        if not recognizer.supportsOnDeviceRecognition():
            return _fail("recognizer.supportsOnDeviceRecognition() is False")
        _log("recognizer available and supports on-device")

        request = Speech.SFSpeechURLRecognitionRequest.alloc().initWithURL_(
            NSURL.fileURLWithPath_(path)
        )
        request.setRequiresOnDeviceRecognition_(True)
        request.setShouldReportPartialResults_(False)

        done = threading.Event()
        result = {}

        def _on_result(speech_result, error):
            _log(f"callback fired: error={error!r} final={getattr(speech_result, 'isFinal', lambda: None)()}")
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
        _log("recognition task started, waiting")
        # NOT a plain done.wait() -- confirmed live that the completion
        # handler above never fires that way. This process has no
        # NSApplication/CFRunLoop actively running (unlike the full app,
        # where AppKit's own event loop happens to keep one spinning),
        # and Speech framework's async delivery needs an active run loop
        # somewhere to actually dispatch the callback -- a bare Python
        # threading.Event blocks the interpreter without pumping
        # anything Objective-C's runtime can use to deliver it. Running
        # the run loop in short increments both services that delivery
        # and lets the plain Python Event still be checked in between.
        from Foundation import NSDate, NSRunLoop
        run_loop = NSRunLoop.currentRunLoop()
        deadline = time.monotonic() + 25
        while not done.is_set() and time.monotonic() < deadline:
            run_loop.runUntilDate_(NSDate.dateWithTimeIntervalSinceNow_(0.1))
        finished = done.is_set()
        _log(f"wait finished={finished}")

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
