import os
import signal
import subprocess

# The Mac only has one speaker, so speech state is tracked globally via a
# pid file rather than per browser session — a phone asking a new question
# should be able to interrupt speech a request from the Mac started, and
# vice versa (see app.py's multi-device conversation sharing). The same
# file now also tracks the menu-bar app's higher-quality voice.speak
# playback (`afplay`) -- one shared mechanism, not two, so a wake word
# heard mid-speech (Phase 6) can interrupt either kind of playback the
# same way.
TTS_PID_FILE = os.path.expanduser("~/Library/Application Support/CampusPilot/tts.pid")

# The two commands ever tracked here -- `say` (this module's own
# speak_interruptible, and voice.speak's fallback) and `afplay` (voice.
# speak's primary OpenAI-TTS playback).
_TRACKED_COMMANDS = ("say", "afplay")


def track_pid(pid: int) -> None:
    """Records `pid` as the currently-playing speech process, for
    stop_speaking()/is_speaking() to find later. Shared by this module's
    own speak_interruptible and by voice.speak's playback, so both kinds
    of speech participate in the same interruption mechanism."""
    os.makedirs(os.path.dirname(TTS_PID_FILE), exist_ok=True)
    with open(TTS_PID_FILE, "w") as file:
        file.write(str(pid))


def untrack_pid(pid: int) -> None:
    """Remove the playback marker only if it still belongs to ``pid``."""
    try:
        with open(TTS_PID_FILE) as file:
            tracked_pid = int(file.read().strip())
        if tracked_pid == pid:
            os.remove(TTS_PID_FILE)
    except (FileNotFoundError, ValueError, OSError):
        pass


def _tracked_pid_and_command():
    """Returns (pid, comm) for whatever's currently tracked, or (None,
    None) if there's nothing tracked or the tracked process has already
    exited (a stale pid -- pids get reused, so this always double-checks
    against the real process table rather than trusting the file alone)."""
    if not os.path.exists(TTS_PID_FILE):
        return None, None
    try:
        with open(TTS_PID_FILE) as file:
            pid = int(file.read().strip())
        comm = subprocess.run(
            ["ps", "-p", str(pid), "-o", "comm="], capture_output=True, text=True
        ).stdout.strip()
    except (ValueError, OSError):
        return None, None
    if any(comm.endswith(cmd) for cmd in _TRACKED_COMMANDS):
        return pid, comm
    return None, None


def stop_speaking():
    """Kill any speech still playing from a previous reply, whether it's
    this module's `say` or voice.speak's `say`/`afplay`."""

    pid, comm = _tracked_pid_and_command()
    if pid is not None:
        try:
            os.kill(pid, signal.SIGTERM)
        except (ProcessLookupError, PermissionError):
            pass

    if os.path.exists(TTS_PID_FILE):
        os.remove(TTS_PID_FILE)


def is_speaking() -> bool:
    """True if a tracked speech process is currently actually running --
    for voice code to check before deciding whether an interruption is
    even meaningful."""
    pid, _ = _tracked_pid_and_command()
    return pid is not None


def speak_interruptible(text):
    """System `say` TTS, tracked by PID so a later reply (from any device
    sharing this conversation) can cut it off mid-sentence via
    stop_speaking(). Used by the Streamlit multi-device chat flow. For the
    native menu-bar app's higher-quality voice responses, see
    voice.speak.speak_natural instead -- different trade-off (instant and
    free vs. higher quality), not a duplicate."""
    stop_speaking()
    process = subprocess.Popen(["say", text])
    track_pid(process.pid)
