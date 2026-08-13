import subprocess


def _escape(value):
    return value.replace("\\", "\\\\").replace('"', '\\"')


def add_reminder(title, due_date=None, list_name="Reminders"):

    escaped_title = _escape(title.strip())
    escaped_list = _escape(list_name.strip())

    due_clause = ""
    if due_date:
        escaped_due = _escape(due_date.strip())
        due_clause = f'set due date of newReminder to date "{escaped_due}"\n'

    script = f'''
    tell application "Reminders"
        if not (exists list "{escaped_list}") then
            make new list with properties {{name:"{escaped_list}"}}
        end if
        tell list "{escaped_list}"
            set newReminder to make new reminder with properties {{name:"{escaped_title}"}}
            {due_clause}
        end tell
    end tell
    '''

    result = subprocess.run(
        ["osascript", "-e", script],
        capture_output=True,
        text=True
    )

    if result.returncode != 0:
        return f"Could not create reminder: {result.stderr.strip()}"

    if due_date:
        return f"Added reminder '{title}' due {due_date}"
    return f"Added reminder '{title}'"


if __name__ == "__main__":
    print(add_reminder("Test reminder from CampusPilot"))
