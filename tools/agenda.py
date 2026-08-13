import subprocess

DEFAULT_DAYS = 7


def list_upcoming(days=DEFAULT_DAYS):

    days = int(days)

    script = f'''
    set output to ""
    set startWindow to current date
    set endWindow to startWindow + ({days} * days)

    tell application "Reminders"
        repeat with r in reminders
            if completed of r is false and due date of r is not missing value then
                if due date of r >= startWindow and due date of r <= endWindow then
                    set output to output & "Reminder: " & (name of r) & " -- due " & ((due date of r) as string) & "\\n"
                end if
            end if
        end repeat
    end tell

    tell application "Calendar"
        repeat with c in calendars
            -- Large built-in calendars (holidays, birthdays, Siri
            -- suggestions) can hold hundreds of events; scanning them makes
            -- this query take minutes instead of seconds, and they aren't
            -- relevant to a student's agenda anyway.
            if name of c does not contain "Holiday" and name of c is not "Birthdays" and name of c is not "Siri Suggestions" then
                repeat with e in (events of c whose start date >= startWindow and start date <= endWindow)
                    set output to output & "Event: " & (summary of e) & " -- " & ((start date of e) as string) & "\\n"
                end repeat
            end if
        end repeat
    end tell

    return output
    '''

    try:
        result = subprocess.run(
            ["osascript", "-e", script],
            capture_output=True,
            text=True,
            timeout=30,
        )
    except subprocess.TimeoutExpired:
        return (
            "Checking your reminders and calendar took too long — try "
            "narrowing the time window or asking about just one of them."
        )

    if result.returncode != 0:
        return f"Could not check your reminders/calendar: {result.stderr.strip()}"

    output = result.stdout.strip()
    return output if output else f"Nothing due or scheduled in the next {days} days."


if __name__ == "__main__":
    print(list_upcoming())
