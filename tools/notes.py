import subprocess


def _escape(value):
    return value.replace("\\", "\\\\").replace('"', '\\"')


def create_note(title, body="", folder="Notes"):

    escaped_folder = _escape(folder.strip())
    # Notes derives the visible title from the first line of the body
    # rather than a separate title property, so the title is folded in.
    escaped_body = _escape(title.strip()) + "<br><br>" + _escape(body.strip())

    script = f'''
    tell application "Notes"
        if not (exists folder "{escaped_folder}") then
            make new folder with properties {{name:"{escaped_folder}"}}
        end if
        tell folder "{escaped_folder}"
            make new note with properties {{body:"{escaped_body}"}}
        end tell
    end tell
    '''

    result = subprocess.run(
        ["osascript", "-e", script],
        capture_output=True,
        text=True
    )

    if result.returncode != 0:
        return f"Could not create note: {result.stderr.strip()}"

    return f"Created note '{title}'"


if __name__ == "__main__":
    print(create_note("Test note from CampusPilot", "This is a test."))
