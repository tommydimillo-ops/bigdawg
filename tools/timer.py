import subprocess
import threading


def _notify_after(seconds, label):

    def fire():
        subprocess.run(
            ["osascript", "-e", f'display notification "{label}" with title "Timer done"'],
            capture_output=True,
        )

    threading.Timer(seconds, fire).start()


def set_timer(minutes, label="Time's up"):

    seconds = max(1, float(minutes) * 60)
    label = label.replace('"', "'")

    _notify_after(seconds, label)

    return f"Timer set for {minutes} minute(s)."
