import subprocess

TIMEOUT_SECONDS = 5


def get_clipboard():
    result = subprocess.run(["pbpaste"], capture_output=True, text=True, timeout=TIMEOUT_SECONDS)
    return result.stdout


def set_clipboard(text):
    subprocess.run(["pbcopy"], input=text, text=True, timeout=TIMEOUT_SECONDS)
    return "Copied to clipboard."
