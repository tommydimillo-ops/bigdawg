import os
import subprocess
import sys

from agent.secret_paths import seatbelt_read_rules
from config.dotenv_loader import NO_DOTENV_ENV_VAR

# A disposable directory the sandboxed code is allowed to write to. Nothing
# outside it can be written to, and network access is blocked entirely, so
# a failure or a bad instruction (including one smuggled in via a web page
# open_browser read) can't touch real files or exfiltrate anything.
SANDBOX_DIR = os.path.expanduser("~/Library/Application Support/CampusPilot/sandbox")
PROFILE_PATH = os.path.join(os.path.dirname(SANDBOX_DIR), "sandbox.sb")

TIMEOUT = 15
MAX_OUTPUT = 4000

# The profile is `(allow default)` plus targeted denies -- an earlier
# deny-by-default attempt that locked down reads wholesale couldn't be gotten
# working reliably (macOS's Seatbelt rule semantics are undocumented and finicky
# enough that a broken stricter profile felt worse than a verified, narrower
# one). What it enforces:
#   - no network, and no writes outside the disposable sandbox directory (the
#     original guarantees);
#   - since plan-b9, NO READS of secret files -- .env and its siblings, keys,
#     credentials, .netrc/.npmrc/token.json, and ~/Library/Keychains -- generated
#     from the same pattern list the file-reader chokepoint uses
#     (agent.secret_paths.seatbelt_read_rules). Before that, code run here could
#     open('.env') and print it, and the output went straight back to the model;
#   - since plan-b9, NO EXEC of the three tools that reach outside the sandbox
#     (/usr/bin/open -> LaunchServices, /usr/bin/security -> the Keychain,
#     /usr/bin/osascript -> AppleEvents), plus launchctl, plus NOTHING at all from
#     the sandbox directory itself: the only place this code can write is that
#     directory, so without the last rule it could simply copy `open` there and
#     run the copy (see _EXEC_DENIED_BINARIES).
#   - since plan-b9, NO Mach lookup of LaunchServices. Denying the three binaries
#     alone is NOT enough, and this was measured rather than assumed: PyObjC is
#     installed in this venv (the menu-bar app needs it), and with only the
#     exec-denies sandboxed code could still reach LaunchServices two ways
#     (NSWorkspace, and plain ctypes) and even send an AppleEvent to another
#     process (it asked Finder for its name) -- i.e. write a .command file, then
#     open it through LaunchServices, running it OUTSIDE the sandbox with
#     network. Denying com.apple.coreservices.launchservicesd / com.apple.lsd.*
#     flipped all three routes to blocked and left ordinary Python untouched.
#     (Denying AppleEvents separately added nothing: addressing an AppleEvent
#     goes through LaunchServices.)
# The environment the child sees is a separate layer (_child_environment).
#
# Known limits, stated rather than implied away. `(allow default)` cannot be
# sealed by enumerating denies: (1) direct Keychain access through the Security
# framework was NOT measured -- the probe fails identically with and without a
# Security-service deny, and a real lookup would mean querying the real
# Keychain -- so only the CLI (exec-deny) and the on-disk files (read-deny) are
# closed; (2) other Mach routes to processes outside the sandbox (launchd XPC,
# ...) were not enumerated. A deny-by-default profile is the real fix and was
# tried and abandoned earlier; see the plan-b9 report.
_EXEC_DENIED_BINARIES = (
    "/usr/bin/open",
    "/usr/bin/security",
    "/usr/bin/osascript",
    "/bin/launchctl",
)


def _sandbox_profile(home=None):
    # Built from the current SANDBOX_DIR at call time, not baked in as a
    # module-level string -- SANDBOX_DIR must stay redirectable (e.g. by
    # tests) without leaving behind a stale allow-path from whatever
    # value was live at import time.
    sandbox_paths = {SANDBOX_DIR, os.path.realpath(SANDBOX_DIR)}
    exec_denied = "\n".join(f'    (literal "{binary}")' for binary in _EXEC_DENIED_BINARIES)
    exec_denied_sandbox = "\n".join(f'    (subpath "{path}")' for path in sorted(sandbox_paths))
    return f"""(version 1)
(allow default)
(deny network*)
(deny file-write*)
(allow file-write*
    (subpath "{SANDBOX_DIR}")
    (subpath "/dev"))
{seatbelt_read_rules(home)}(deny process-exec
{exec_denied}
{exec_denied_sandbox})
(deny mach-lookup
    (global-name "com.apple.coreservices.launchservicesd")
    (global-name-prefix "com.apple.lsd."))
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


# The child's environment is built from this explicit allowlist, never from
# os.environ. Found by plan-b8/plan-b9: the child used to inherit everything,
# including ANTHROPIC_API_KEY, OPENAI_API_KEY and whatever the host process
# carried, and print(os.environ) sent it straight back to the model. Locale
# variables (LANG, LC_*) are copied only if the parent actually has them --
# never invented.
_ENV_ALLOWLIST = ("PATH", "HOME", "TMPDIR", "USER", "SHELL", "VIRTUAL_ENV")


def _child_environment(parent=None):
    # Defaults to None and reads os.environ here, not at definition time, so a
    # test's or a later change to the environment is honored.
    parent = os.environ if parent is None else parent
    child = {name: parent[name] for name in _ENV_ALLOWLIST if name in parent}
    for name, value in parent.items():
        if name == "LANG" or name.startswith("LC_"):
            child[name] = value
    # Tells config.dotenv_loader not to re-read .env into this child -- any
    # code that imports the project would otherwise put the keys straight back.
    child[NO_DOTENV_ENV_VAR] = "1"
    return child


# How long to keep waiting for a child that was told to die and did not.
_KILL_GRACE_SECONDS = 3


def _kill_and_abandon(process):
    """Kills the child's whole process group, waits a bounded time, and then
    GIVES UP rather than block. plan-b9's own sandbox probes proved this is not
    hypothetical: sandboxed code that touched a denied macOS service aborted
    and the dying process wedged in the kernel (state UE), where SIGKILL cannot
    reach it. subprocess.run(timeout=) kills the child and then waits on its
    pipes with no bound, so one such child would have frozen the whole agent
    thread that called run_python. An abandoned child is a leaked process; a
    hung agent is not acceptable."""
    for kill in (lambda: os.killpg(process.pid, 9), process.kill):
        try:
            kill()
        except (ProcessLookupError, PermissionError, OSError):
            pass
    try:
        process.communicate(timeout=_KILL_GRACE_SECONDS)
    except subprocess.TimeoutExpired:
        pass


def run_python(code):
    _ensure_profile()

    process = subprocess.Popen(
        ["sandbox-exec", "-f", PROFILE_PATH, sys.executable, "-c", code],
        cwd=SANDBOX_DIR,
        env=_child_environment(),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        start_new_session=True,  # its own process group, so the whole tree can be killed
    )
    try:
        stdout, stderr_text = process.communicate(timeout=TIMEOUT)
    except subprocess.TimeoutExpired:
        _kill_and_abandon(process)
        return f"Code timed out after {TIMEOUT}s (possible infinite loop)."

    output = stdout or ""
    stderr = _clean_stderr(stderr_text or "")
    if stderr:
        output += ("\n--- errors ---\n" if output else "") + stderr

    output = output.strip()[:MAX_OUTPUT] or "(no output)"
    status = "succeeded" if process.returncode == 0 else f"exited with code {process.returncode}"

    return (
        f"Ran in an isolated sandbox (no network, no writes outside "
        f"{SANDBOX_DIR}) — {status}:\n{output}"
    )


if __name__ == "__main__":
    print(run_python("print('hello from the sandbox')"))
