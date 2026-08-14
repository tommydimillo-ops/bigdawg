import difflib
import glob
import os
import subprocess

# Common shorthand the model might use that doesn't match the real
# macOS application name `open -a` expects.
APP_ALIASES = {
    "calc": "Calculator",
    "chrome": "Google Chrome",
    "browser": "Safari",
}

# Filler words that show up naturally in spoken requests ("open the
# calculator app for me") but aren't part of any real macOS application
# name. `open -a` requires a close-to-exact name and fails outright on
# anything extra -- confirmed live: "Calculator" opens fine, "the
# calculator app" doesn't, even though both mean the same thing to a
# person, and casual/voice-transcribed phrasing includes words like
# these constantly.
_FILLER_WORDS = {"the", "a", "an", "app", "application", "please"}

_APP_DIRS = (
    "/Applications",
    "/System/Applications",
    "/System/Applications/Utilities",
    os.path.expanduser("~/Applications"),
)


def _strip_filler_words(name: str) -> str:
    words = [w for w in name.split() if w.lower() not in _FILLER_WORDS]
    return " ".join(words) or name


def _installed_app_names():
    names = []
    for directory in _APP_DIRS:
        for path in glob.glob(os.path.join(directory, "*.app")):
            names.append(os.path.splitext(os.path.basename(path))[0])
    return names


def _find_close_match(name: str):
    """Best-effort match against actually-installed applications --
    `open -a` itself does no fuzzy matching at all, so a name that's
    close but not exact (a typo, a missing/extra word) fails outright
    with nothing else to fall back on unless something here tries
    harder first."""
    installed = _installed_app_names()
    lowered = name.lower()
    for candidate in installed:
        if candidate.lower() == lowered:
            return candidate
    for candidate in installed:
        if lowered in candidate.lower() or candidate.lower() in lowered:
            return candidate
    matches = difflib.get_close_matches(name, installed, n=1, cutoff=0.7)
    return matches[0] if matches else None


def _open(app: str):
    return subprocess.run(["open", "-a", app], capture_output=True, text=True)


def open_application(app_name):

    raw = app_name.strip()
    app = APP_ALIASES.get(raw.lower(), raw)

    try:
        result = _open(app)
    except Exception as error:
        return f"Could not open {app}: {error}"

    if result.returncode == 0:
        return f"Opening {app}"

    # Exact name failed -- try again against actually-installed
    # applications before giving up, since a filler word ("the
    # calculator app") or a small typo shouldn't be the difference
    # between working and not.
    cleaned = _strip_filler_words(app)
    match = _find_close_match(cleaned)
    if match:
        try:
            match_result = _open(match)
        except Exception as error:
            return f"Could not open {app}: {error}"
        if match_result.returncode == 0:
            return f"Opening {match}"

    return f"Could not open {app}: {result.stderr.strip()}"
