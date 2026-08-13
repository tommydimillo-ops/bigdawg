import os
import signal
import subprocess

# The Mac only has one speaker, so speech state is tracked globally via a
# pid file rather than per browser session — a phone asking a new question
# should be able to interrupt speech a request from the Mac started, and
# vice versa (see app.py's multi-device conversation sharing).
TTS_PID_FILE = os.path.expanduser("~/Library/Application Support/CampusPilot/tts.pid")


def stop_speaking():
    """Kill any speech still playing from a previous reply."""

    if not os.path.exists(TTS_PID_FILE):
        return

    try:
        with open(TTS_PID_FILE) as file:
            pid = int(file.read().strip())

        # Confirm it's actually still a `say` process before killing —
        # pids get reused, and this file could be stale.
        comm = subprocess.run(
            ["ps", "-p", str(pid), "-o", "comm="], capture_output=True, text=True
        ).stdout.strip()
        if comm.endswith("say"):
            os.kill(pid, signal.SIGTERM)
    except (ValueError, ProcessLookupError, PermissionError):
        pass

    os.remove(TTS_PID_FILE)


def speak(text):
    stop_speaking()
    process = subprocess.Popen(["say", text])
    os.makedirs(os.path.dirname(TTS_PID_FILE), exist_ok=True)
    with open(TTS_PID_FILE, "w") as file:
        file.write(str(process.pid))
