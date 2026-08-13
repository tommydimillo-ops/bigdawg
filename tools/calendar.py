import subprocess


def _escape(value):
    return value.replace("\\", "\\\\").replace('"', '\\"')


def add_calendar_event(title, start_date, end_date=None, calendar_name="Calendar"):

    escaped_title = _escape(title.strip())
    escaped_calendar = _escape(calendar_name.strip())
    escaped_start = _escape(start_date.strip())

    if end_date:
        end_clause = f'date "{_escape(end_date.strip())}"'
    else:
        end_clause = f'(date "{escaped_start}") + (1 * hours)'

    script = f'''
    tell application "Calendar"
        if not (exists calendar "{escaped_calendar}") then
            make new calendar with properties {{name:"{escaped_calendar}"}}
        end if
        tell calendar "{escaped_calendar}"
            make new event with properties {{summary:"{escaped_title}", start date:date "{escaped_start}", end date:{end_clause}}}
        end tell
    end tell
    '''

    result = subprocess.run(
        ["osascript", "-e", script],
        capture_output=True,
        text=True
    )

    if result.returncode != 0:
        return f"Could not create calendar event: {result.stderr.strip()}"

    when = start_date if not end_date else f"{start_date} to {end_date}"
    return f"Added '{title}' to your calendar for {when}"


if __name__ == "__main__":
    print(add_calendar_event("Test event from CampusPilot", "August 15, 2026 3:00 PM"))
