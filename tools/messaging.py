import subprocess
import time

# Same two-step preview-then-explicit-confirm pattern as tools/autofill.py's
# fill_login/confirm_login: draft_email never sends anything, it only
# creates a real (visible, reviewable) draft and asks for confirmation.
# send_email only fires if draft_email was genuinely called first for the
# same recipient — real state, not a prompt suggestion the model could
# skip around. Sending an email is real-world external communication, the
# same risk class the human-confirmation gate exists for.
CONFIRMATION_WINDOW_SECONDS = 120

_pending_email = None


def _escape(value):
    return value.replace("\\", "\\\\").replace('"', '\\"')


def draft_email(to, subject, body):
    """Step 1 of 2: creates a real, visible draft in Mail — does NOT send
    it. Report the draft back to the user and wait for explicit
    confirmation before calling send_email."""

    global _pending_email

    escaped_to = _escape(to.strip())
    escaped_subject = _escape(subject.strip())
    escaped_body = _escape(body.strip())

    script = f'''
    tell application "Mail"
        set newMessage to make new outgoing message with properties {{subject:"{escaped_subject}", content:"{escaped_body}", visible:true}}
        tell newMessage
            make new to recipient at end of to recipients with properties {{address:"{escaped_to}"}}
        end tell
        activate
    end tell
    '''

    result = subprocess.run(["osascript", "-e", script], capture_output=True, text=True)

    if result.returncode != 0:
        return f"Could not create the email draft: {result.stderr.strip()}"

    _pending_email = {
        "to": to.strip(),
        "subject": subject.strip(),
        "expires": time.time() + CONFIRMATION_WINDOW_SECONDS,
    }

    return (
        f"Drafted an email to {to} — subject: \"{subject}\"\n\n{body}\n\n"
        "This is open in Mail but NOT sent. Show the user this exact "
        "content and ask them to confirm before calling send_email — only "
        "call it after they explicitly say yes in their own words."
    )


def send_email(to):
    """Step 2 of 2: sends the draft, but only if draft_email was just
    called for this exact recipient — calling this without a genuine
    preceding draft_email for this address will simply fail."""

    global _pending_email

    if not _pending_email or _pending_email["to"] != to.strip():
        return f"No pending draft to '{to}' — call draft_email first."

    if time.time() > _pending_email["expires"]:
        _pending_email = None
        return f"That draft confirmation expired — call draft_email again for '{to}'."

    subject = _pending_email["subject"]
    _pending_email = None

    escaped_subject = _escape(subject)

    # Mail's AppleScript collection iteration (`repeat ... in outgoing
    # messages`) is unreliable in practice — indexed access is not. Search
    # by index rather than iterating the collection directly.
    script = f'''
    tell application "Mail"
        set foundIndex to -1
        repeat with i from 1 to (count of outgoing messages)
            if subject of (outgoing message i) is "{escaped_subject}" then
                set foundIndex to i
                exit repeat
            end if
        end repeat
        if foundIndex is -1 then
            return "NOT_FOUND"
        end if
        send (outgoing message foundIndex)
        return "SENT"
    end tell
    '''

    result = subprocess.run(["osascript", "-e", script], capture_output=True, text=True)

    if result.returncode != 0:
        return f"Could not send the email: {result.stderr.strip()}"

    if "NOT_FOUND" in result.stdout:
        return f"Couldn't find the draft to '{to}' anymore — it may have been sent, closed, or edited by hand."

    return f"Sent the email to {to}."


if __name__ == "__main__":
    print(draft_email("test@example.com", "Test subject", "Test body."))
