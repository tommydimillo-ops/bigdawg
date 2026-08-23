import os
import subprocess
import sys

# A disposable directory the sandboxed code is allowed to write to. Nothing
# outside it can be written to, and network access is blocked entirely, so
# a failure or a bad instruction (including one smuggled in via a web page
# open_browser read) can't touch real files or exfiltrate anything.
SANDBOX_DIR = os.path.expanduser("~/Library/Application Support/CampusPilot/sandbox")
PROFILE_PATH = os.path.join(os.path.dirname(SANDBOX_DIR), "sandbox.sb")

TIMEOUT = 15
MAX_OUTPUT = 4000

# File reads are NOT restricted by this profile (only writes and network) —
# an earlier deny-by-default attempt that also locked down reads couldn't
# be gotten working reliably (macOS's Seatbelt rule semantics are
# undocumented and finicky enough that a broken stricter profile felt
# worse than a verified, narrower one). So: safe against persisting
# changes to real files and safe against exfiltrating anything over the
# network, but code run here could still read other files on this Mac if
# specifically told to — don't run untrusted code that has a reason to go
# looking for secrets.
def _sandbox_profile():
    # Built from the current SANDBOX_DIR at call time, not baked in as a
    # module-level string -- SANDBOX_DIR must stay redirectable (e.g. by
    # tests) without leaving behind a stale allow-path from whatever
    # value was live at import time.
    return f"""(version 1)
(allow default)
(deny network*)
(deny file-write*)
(allow file-write*
    (subpath "{SANDBOX_DIR}")
    (subpath "/dev"))
"""


_NOISE_MARKERS = ("xcrun_db", "couldn't create cache file")


def _ensure_profile():
    os.makedirs(SANDBOX_DIR, exist_ok=True)
    with open(PROFILE_PATH, "w") as file:
        file.write(_sandbox_profile())


def _clean_stderr(stderr):
    lines = [
        line for line in stderr.splitlines()
        if not any(marker in line for marker in _NOISE_MARKERS)
    ]
    return "\n".join(lines).strip()


def run_python(code):
    _ensure_profile()

    try:
        result = subprocess.run(
            ["sandbox-exec", "-f", PROFILE_PATH, sys.executable, "-c", code],
            cwd=SANDBOX_DIR,
            capture_output=True,
            text=True,
            timeout=TIMEOUT,
        )
    except subprocess.TimeoutExpired:
        return f"Code timed out after {TIMEOUT}s (possible infinite loop)."

    output = result.stdout or ""
    stderr = _clean_stderr(result.stderr or "")
    if stderr:
        output += ("\n--- errors ---\n" if output else "") + stderr

    output = output.strip()[:MAX_OUTPUT] or "(no output)"
    status = "succeeded" if result.returncode == 0 else f"exited with code {result.returncode}"

    return (
        f"Ran in an isolated sandbox (no network, no writes outside "
        f"{SANDBOX_DIR}) — {status}:\n{output}"
    )


if __name__ == "__main__":
    print(run_python("print('hello from the sandbox')"))
