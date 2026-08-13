import subprocess

# Common shorthand the model might use that doesn't match the real
# macOS application name `open -a` expects.
APP_ALIASES = {
    "calc": "Calculator",
    "chrome": "Google Chrome",
    "browser": "Safari",
}


def open_application(app_name):

    app = APP_ALIASES.get(app_name.strip().lower(), app_name.strip())

    try:
        result = subprocess.run(
            ["open", "-a", app],
            capture_output=True,
            text=True
        )
    except Exception as error:
        return f"Could not open {app}: {error}"

    if result.returncode != 0:
        return f"Could not open {app}: {result.stderr.strip()}"

    return f"Opening {app}"
