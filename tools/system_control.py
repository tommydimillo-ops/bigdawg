import subprocess

TIMEOUT_SECONDS = 5


def lock_screen():
    subprocess.run(["pmset", "displaysleepnow"], capture_output=True, timeout=TIMEOUT_SECONDS)
    return "Locked the screen."


def sleep_mac():
    subprocess.run(
        ["osascript", "-e", 'tell application "System Events" to sleep'],
        capture_output=True,
        timeout=TIMEOUT_SECONDS,
    )
    return "Putting the Mac to sleep."
