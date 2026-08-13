"""Saved-login sign-in and email tools -- both are two-step, human-approved
processes: a preview tool (L0, no real effect) and a confirm tool (gated,
requires_live_confirmation=True) that finalizes it."""
from tools.autofill import confirm_login, fill_login
from tools.messaging import draft_email, send_email
from tools.registry import ToolSpec, register

register(ToolSpec(
    name="fill_login",
    description=(
        "Step 1 of 2 for signing in to a saved site: checks a saved "
        "login exists, the domain matches the current page, and a "
        "login form is present. Does NOT fill in or submit anything — "
        "report what it found and ask the user to confirm before "
        "calling confirm_login."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "site": {
                "type": "string",
                "description": "The saved site nickname, e.g. 'brightspace'.",
            }
        },
        "required": ["site"],
    },
    permission_level=0,
    handler=lambda ti: fill_login(ti["site"]),
))

register(ToolSpec(
    name="confirm_login",
    description=(
        "Step 2 of 2: actually fills in and submits the login. Only "
        "call this after fill_login has previewed the same site AND "
        "the user has explicitly confirmed in their own words in a "
        "later message — calling it without a genuine prior fill_login "
        "preview for this exact site will simply fail."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "site": {
                "type": "string",
                "description": "The saved site nickname, matching the fill_login call.",
            }
        },
        "required": ["site"],
    },
    permission_level=3,
    handler=lambda ti: confirm_login(ti["site"]),
    requires_live_confirmation=True,
    side_effect=True,
))

register(ToolSpec(
    name="draft_email",
    description=(
        "Step 1 of 2 for sending an email: creates a real, visible "
        "draft in Mail with the given recipient, subject, and body. "
        "Does NOT send it. Report the exact content back to the user "
        "and ask them to confirm before calling send_email."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "to": {"type": "string", "description": "Recipient email address."},
            "subject": {"type": "string", "description": "Email subject."},
            "body": {"type": "string", "description": "Email body text."},
        },
        "required": ["to", "subject", "body"],
    },
    permission_level=0,
    handler=lambda ti: draft_email(ti["to"], ti["subject"], ti["body"]),
    side_effect=True,
))

register(ToolSpec(
    name="send_email",
    description=(
        "Step 2 of 2: actually sends the draft. Only call this after "
        "draft_email has previewed the same recipient AND the user has "
        "explicitly confirmed in their own words in a later message — "
        "calling it without a genuine prior draft_email for this exact "
        "address will simply fail."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "to": {"type": "string", "description": "Recipient email address, matching draft_email."}
        },
        "required": ["to"],
    },
    permission_level=3,
    handler=lambda ti: send_email(ti["to"]),
    requires_live_confirmation=True,
    side_effect=True,
))
