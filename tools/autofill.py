import re
import time
from urllib.parse import urlparse

from tools.browser import BrowserBusyError, get_current_page
from tools.credential_store import get_login

USERNAME_SELECTORS = [
    "input[type='email']",
    "input[autocomplete='username']",
    "input[type='text'][name*='user' i]",
    "input[type='text'][name*='email' i]",
    "input[type='text'][id*='user' i]",
    "input[type='text'][id*='email' i]",
]

PASSWORD_SELECTORS = ["input[type='password']"]

SUBMIT_SELECTORS = ["button[type='submit']", "input[type='submit']"]

SUBMIT_TEXT_PATTERN = re.compile(r"sign in|log in|next|continue", re.IGNORECASE)

# How long a fill_login preview stays valid before confirm_login must be
# re-verified. Real state, not a prompt suggestion: confirm_login checks
# this directly, so the model can't call it without a genuine preceding
# preview call, and a stale confirmation can't fire on a page you've since
# navigated away from.
CONFIRMATION_WINDOW_SECONDS = 120

_pending_confirmation = None


def _domain_matches(page_url, allowed_domain):
    hostname = (urlparse(page_url).hostname or "").lower()
    allowed_domain = allowed_domain.lower()
    return hostname == allowed_domain or hostname.endswith("." + allowed_domain)


def _first_visible(page, selectors):
    for selector in selectors:
        locator = page.locator(selector).first
        try:
            if locator.count() > 0 and locator.is_visible():
                return locator
        except Exception:
            continue
    return None


def _settle(page):
    try:
        page.wait_for_load_state("networkidle", timeout=8000)
    except Exception:
        page.wait_for_timeout(1500)


def _click_submit(page):
    for selector in SUBMIT_SELECTORS:
        locator = page.locator(selector).first
        try:
            if locator.count() > 0 and locator.is_visible():
                locator.click(timeout=3000)
                return True
        except Exception:
            continue

    for role in ("button", "link"):
        try:
            locator = page.get_by_role(role, name=SUBMIT_TEXT_PATTERN).first
            if locator.count() > 0 and locator.is_visible():
                locator.click(timeout=3000)
                return True
        except Exception:
            continue

    return False


def fill_login(site):
    """Step 1 of 2: check everything out and ask for explicit confirmation.
    Never fills or submits anything — just verifies a matching login form
    is actually present so the confirmation prompt is meaningful."""

    global _pending_confirmation

    credential = get_login(site)

    if not credential:
        return (
            f"No saved login for '{site}'. Add one from a terminal with: "
            f"python -m tools.manage_logins add {site} <domain> <username> "
            "(that script prompts for the password securely — never share "
            "a password in chat)."
        )

    try:
        page = get_current_page()
    except BrowserBusyError as error:
        return f"{error} Try again in a moment."

    if not _domain_matches(page.url, credential["domain"]):
        return (
            f"Refusing to autofill: the current page "
            f"({urlparse(page.url).hostname or page.url}) doesn't match "
            f"the saved domain for '{site}' ({credential['domain']}). "
            "Navigate to the right login page first."
        )

    username_field = _first_visible(page, USERNAME_SELECTORS)
    password_field = _first_visible(page, PASSWORD_SELECTORS)

    if not username_field and not password_field:
        return f"Couldn't find a login form on this page to use '{site}' on."

    _pending_confirmation = {"site": site, "expires": time.time() + CONFIRMATION_WINDOW_SECONDS}

    return (
        f"Found a login form for '{site}' ({credential['username']}) on "
        f"{urlparse(page.url).hostname}. Ready to fill it in and sign in — "
        "ask the user to confirm first. Only call confirm_login after they "
        "explicitly say yes in their own words; don't assume or infer it."
    )


def confirm_login(site):
    """Step 2 of 2: actually fills and submits, but only if fill_login was
    just called for this exact site — this is a real check against stored
    state, so skipping straight to this tool without a preview fails."""

    global _pending_confirmation

    if not _pending_confirmation or _pending_confirmation["site"] != site:
        return f"No pending confirmation for '{site}' — call fill_login first."

    if time.time() > _pending_confirmation["expires"]:
        _pending_confirmation = None
        return f"That confirmation expired — call fill_login again for '{site}'."

    _pending_confirmation = None

    credential = get_login(site)
    if not credential:
        return f"'{site}' is no longer saved."

    try:
        page = get_current_page()
    except BrowserBusyError as error:
        return f"{error} Try again in a moment."

    # Re-check domain: the page could have navigated away since the preview.
    if not _domain_matches(page.url, credential["domain"]):
        return (
            f"Refusing to autofill: the current page "
            f"({urlparse(page.url).hostname or page.url}) no longer "
            f"matches the saved domain for '{site}' ({credential['domain']})."
        )

    username_field = _first_visible(page, USERNAME_SELECTORS)
    password_field = _first_visible(page, PASSWORD_SELECTORS)

    if username_field and not password_field:
        # Two-step login (e.g. Microsoft/Google SSO): username first, then
        # a password field appears on the next screen after advancing.
        username_field.fill(credential["username"])
        _click_submit(page)
        _settle(page)
        password_field = _first_visible(page, PASSWORD_SELECTORS)
    elif username_field and password_field:
        username_field.fill(credential["username"])

    if not password_field:
        return (
            f"Filled in the username for '{site}', but couldn't find a "
            "password field — you may need to finish signing in by hand, "
            "or this page's layout isn't recognized."
        )

    password_field.fill(credential["password"])
    submitted = _click_submit(page)

    return (
        f"Filled in your saved login for '{site}' ({credential['username']})"
        + (" and submitted it" if submitted else " — you may need to click sign in yourself")
        + ". If this account uses two-factor authentication, check your "
        "phone/authenticator app to approve the sign-in."
    )
