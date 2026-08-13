import subprocess

TRANSPORT_ACTIONS = {"play", "pause", "playpause", "next track", "previous track"}


def _escape(value):
    return value.replace("\\", "\\\\").replace('"', '\\"')


def control_music(action, query=None):

    action = action.strip().lower()

    if query:
        escaped_query = _escape(query.strip())
        script = f'''
        tell application "Music"
            activate
            set searchResults to (search playlist "Library" for "{escaped_query}")
            if (count of searchResults) > 0 then
                play (item 1 of searchResults)
                return "Playing " & (name of item 1 of searchResults) & " by " & (artist of item 1 of searchResults)
            else
                return "Couldn't find anything matching '{escaped_query}' in your library."
            end if
        end tell
        '''
    elif action in TRANSPORT_ACTIONS:
        script = f'''
        tell application "Music"
            {action}
        end tell
        '''
    else:
        return f"Unknown music action: {action}"

    try:
        result = subprocess.run(
            ["osascript", "-e", script],
            capture_output=True,
            text=True,
            timeout=45,
        )
    except subprocess.TimeoutExpired:
        return "Music is taking a while to respond (it may be launching) — try again in a moment."

    if result.returncode != 0:
        return f"Could not control Music: {result.stderr.strip()}"

    return result.stdout.strip() or f"Music: {action}"


if __name__ == "__main__":
    print(control_music("play"))
