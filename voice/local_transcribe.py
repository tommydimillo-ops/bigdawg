"""On-device (offline) speech-to-text fallback for when the primary,
cloud-based transcription (OpenAI's gpt-4o-transcribe) is unavailable --
no network call, no API key, no cost. Uses macOS's built-in Speech
framework (SFSpeechRecognizer), restricted to on-device-only recognition
so this never silently trades one external dependency (OpenAI) for
another (Apple's servers).

Requires one-time user approval -- a native macOS permission prompt, the
same kind already granted for microphone access -- the first time this
actually runs. There is no way to grant this in advance from code; the
first real fallback attempt is what triggers the system dialog.

Degrades to unavailable (returns None) if the framework can't be
imported, permission hasn't been granted, or this Mac doesn't support
on-device recognition -- callers already treat "no local fallback
available" as an acceptable outcome, the same as any other transcription
failure.
"""
import os
import subprocess
import sys
import threading
from typing import Optional

from agent.observability import log_event

try:
    import Speech
    from Foundation import NSBundle
    _AVAILABLE = True
except ImportError:
    _AVAILABLE = False

_USAGE_DESCRIPTION_KEY = "NSSpeechRecognitionUsageDescription"


def _has_usage_description() -> bool:
    """True if the CURRENT process's own Info.plist declares
    NSSpeechRecognitionUsageDescription.

    CRITICAL: macOS's TCC privacy system hard-aborts (SIGABRT) any
    process that calls SFSpeechRecognizer.requestAuthorization_ without
    this key present -- not a Python exception, an OS-level crash that no
    try/except here can catch, confirmed live in production (the whole
    menu-bar app died within 4 seconds of startup, silently, with no
    Python traceback at all -- only a macOS crash report explained it).
    Every call site in this module must check this FIRST. Bundling as
    CampusPilotAgent.app (whose Info.plist does declare this key) avoids
    the crash; running via a bare `python3 -m ui.menu_bar` invocation
    does not, since that runs as the system Python.framework's own
    bundle, which never declares this key and never will."""
    try:
        info = NSBundle.mainBundle().infoDictionary()
        return bool(info and _USAGE_DESCRIPTION_KEY in info)
    except Exception:
        return False


def request_authorization(timeout: float = 10) -> bool:
    """Blocks until the user grants/denies Speech Recognition access, or
    returns immediately if already determined. Safe to call repeatedly --
    macOS only ever prompts once per app identity; every call after the
    first just returns the existing answer instantly."""
    if not _AVAILABLE:
        return False

    if not _has_usage_description():
        log_event(
            "local_transcribe_missing_usage_description", component="voice",
            level="warning",
        )
        return False

    status = Speech.SFSpeechRecognizer.authorizationStatus()
    if status == Speech.SFSpeechRecognizerAuthorizationStatusAuthorized:
        return True
    if status != Speech.SFSpeechRecognizerAuthorizationStatusNotDetermined:
        return False  # denied or restricted -- nothing more to do here

    done = threading.Event()
    result = {}

    def _on_status(new_status):
        result["authorized"] = (
            new_status == Speech.SFSpeechRecognizerAuthorizationStatusAuthorized
        )
        done.set()

    try:
        Speech.SFSpeechRecognizer.requestAuthorization_(_on_status)
    except Exception as error:
        log_event(
            "local_transcribe_authorization_failed", component="voice",
            level="warning", error_type=type(error).__name__,
        )
        return False

    done.wait(timeout=timeout)
    return result.get("authorized", False)


def is_available() -> bool:
    """True if on-device transcription can actually be used right now --
    never itself prompts for permission (see request_authorization); a
    status check should never have a side effect the caller didn't ask
    for."""
    if not _AVAILABLE:
        return False
    try:
        if (
            Speech.SFSpeechRecognizer.authorizationStatus()
            != Speech.SFSpeechRecognizerAuthorizationStatusAuthorized
        ):
            return False
        recognizer = Speech.SFSpeechRecognizer.alloc().init()
        return bool(
            recognizer
            and recognizer.isAvailable()
            and recognizer.supportsOnDeviceRecognition()
        )
    except Exception:
        return False


_WORKER_SCRIPT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_local_transcribe_worker.py")


def transcribe_local(path: str, timeout: float = 6) -> Optional[str]:
    """Transcribes the WAV file at `path` entirely on-device. Returns the
    recognized text, or None if unavailable, denied, errored, or nothing
    was recognized within `timeout` seconds.

    Runs the actual recognition in a SEPARATE OS process (see
    _local_transcribe_worker.py), not in-process --
    SFSpeechRecognitionTask.cancel() does not reliably stop macOS's
    on-device recognition work (confirmed live via CPU profiling: it
    left dispatch queues running and consuming hundreds of percent CPU
    minutes after both a timeout-triggered cancel() and an
    unconditional one on every completion path). Killing a subprocess
    on timeout is an OS-level guarantee no in-process API here could
    match -- whatever it was using is reliably reclaimed the instant
    it's terminated.

    Confirmed live: a wake-word-triggered clip that's actually going to
    produce a result does so in under ~2s; one that doesn't (ambiguous or
    non-speech audio) doesn't get more likely to succeed by waiting
    longer, it just hangs. This is the fallback for when the primary
    cloud transcription has ALREADY failed, so every second here is a
    second the user sits waiting with no response."""
    if not is_available():
        return None

    try:
        result = subprocess.run(
            [sys.executable, _WORKER_SCRIPT, path],
            capture_output=True, text=True, timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        # subprocess.run has already killed the child and waited for it
        # by the time this exception is raised -- nothing left running.
        log_event("local_transcribe_recognition_timed_out", component="voice", level="warning")
        return None
    except Exception as error:
        log_event(
            "local_transcribe_unexpected_error", component="voice",
            level="warning", error_type=type(error).__name__,
        )
        return None

    if result.returncode != 0:
        log_event(
            "local_transcribe_recognition_failed", component="voice",
            level="warning", returncode=result.returncode,
            reason=(result.stderr or "").strip()[:200],
        )
        return None

    text = result.stdout.strip()
    return text or None
