import atexit
import base64
import os
import re
from urllib.parse import quote, urlparse, parse_qs

from playwright.sync_api import sync_playwright

from agent import browser_lock

CHROME_PATH = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
MAX_PAGE_TEXT = 4000

# A dedicated, persistent Chrome profile just for CampusPilot — separate
# from the user's everyday Chrome profile (so the two don't fight over the
# same profile lock when both are open), but kept on disk across runs so
# cookies and login sessions carry over instead of starting from a
# logged-out blank slate every single time. Note: Playwright forces this
# profile's Chrome to use a mock in-memory keychain, so Chrome's own saved
# passwords still don't persist between runs — only cookies/sessions do.
PROFILE_DIR = os.path.expanduser("~/Library/Application Support/CampusPilot/chrome-profile")

# How long to wait for a page to actually finish loading/redirecting.
# Login flows commonly chain through 2-3 redirects (school portal -> SSO
# chooser -> identity provider) before landing on the real destination, so a
# short fixed delay isn't enough and was causing text to be grabbed from a
# half-loaded intermediate page.
NAV_TIMEOUT = 15000

# Bare domains (walmart.com) or full URLs get navigated to directly;
# anything else is treated as something to search for.
URL_LIKE = re.compile(r"^https?://|^[a-z0-9-]+\.[a-z]{2,}(/\S*)?$", re.IGNORECASE)

# After landing on a search result, if the page looks like a hub/info page
# rather than the destination itself, follow one login/portal-style link
# instead of leaving the user on a landing page (e.g. a university's
# Brightspace info page instead of the actual Brightspace login).
PORTAL_LINK_PATTERN = re.compile(
    r"log.?in|sign.?in|portal|digital learning|student login|my account",
    re.IGNORECASE,
)

# Human-verification challenges (Cloudflare, reCAPTCHA, hCaptcha, etc.) are
# deliberately not something this tool tries to solve or bypass — that's
# exactly what they exist to prevent. When one shows up, it's surfaced
# plainly so the user can clear it by hand in the browser window instead of
# the tool silently returning empty/garbled text.
CAPTCHA_PATTERN = re.compile(
    r"verify you are human|are you a robot|robot or human|prove you're human|"
    r"confirm (that )?you'?re human|hold the button|captcha|"
    r"checking your browser|attention required.*cloudflare|unusual traffic",
    re.IGNORECASE,
)

# Ordered by specificity, checked against the real live pages of two major
# sites during development (Walmart uses input[type='search'] with
# aria-label='Search'; Amazon uses input[type='text'] with
# aria-label='Search Amazon' and name='field-keywords') — sites vary
# enough that no single selector covers most of them, hence the fallback
# chain instead of one guess.
SEARCH_INPUT_SELECTORS = [
    "input[type='search']",
    "input[role='searchbox']",
    "input[aria-label*='search' i]",
    "input[placeholder*='search' i]",
    "input[name='q']",
    "input[name*='search' i]",
]


class BrowserBusyError(Exception):
    """Raised when another Jarvis process already owns the shared,
    persistent Chrome profile -- callers turn this into a clear, expected
    result string instead of letting a raw Playwright/Chrome error
    surface (see agent/browser_lock.py)."""


_playwright = None
_context = None
_shutdown_registered = False


def _get_context():
    global _playwright, _context, _shutdown_registered

    if _context is None:
        if not browser_lock.try_acquire():
            raise BrowserBusyError("Another Jarvis process is already using the browser.")

        os.makedirs(PROFILE_DIR, exist_ok=True)
        try:
            _playwright = sync_playwright().start()
            _context = _playwright.chromium.launch_persistent_context(
                PROFILE_DIR,
                executable_path=CHROME_PATH,
                headless=False,
            )
        except Exception:
            # The launch itself failed -- release the lock so a later,
            # healthy attempt (this process or another) isn't blocked by
            # a lock this process never actually put to use.
            browser_lock.release()
            raise

        if not _shutdown_registered:
            atexit.register(_shutdown)
            _shutdown_registered = True

    return _context


def _get_page(context):
    # Reuse one tab across calls instead of opening a new one every time —
    # keeps the browser window from filling up with tabs, and keeps the
    # user looking at the same window if they need to finish a login by
    # hand (we never touch password fields ourselves).
    for page in context.pages:
        if not page.is_closed():
            return page

    return context.new_page()


def _discard_dead_context():
    """The Chrome process behind _context died or was closed outside our
    control (crash, force-quit, the user closing the window, OS memory
    pressure) -- the stale playwright/context handles can't be reused, a
    real, reproduced failure mode: every browser call kept failing with
    "...has been closed" indefinitely, since _get_context() only ever
    checked `_context is None`, which stayed False forever once a context
    existed even after it died. Dropping both globals lets the next
    _get_live_page() call launch a fresh browser instead."""
    global _playwright, _context
    if _playwright is not None:
        try:
            _playwright.stop()
        except Exception:
            pass
    _context = None
    _playwright = None
    browser_lock.release()


def _get_live_page():
    """The one entry point every public function below uses to get a
    usable page -- retries once, from a clean slate, if the existing
    context turns out to be dead. BrowserBusyError means another process
    owns the profile right now, not that our own context is stale, so
    it's re-raised immediately rather than retried -- a retry here would
    just re-attempt (and fail) the same lock acquisition."""
    try:
        return _get_page(_get_context())
    except BrowserBusyError:
        raise
    except Exception:
        _discard_dead_context()
        return _get_page(_get_context())


def get_current_page():
    """The same live tab open_and_read drives, for other tools (like login
    autofill) that need to act on whatever page is currently on screen."""
    return _get_live_page()


def _shutdown():
    global _playwright, _context

    if _context:
        _context.close()
    if _playwright:
        _playwright.stop()
    browser_lock.release()


def _settle(page):
    """Wait for the page to actually finish loading instead of guessing a
    fixed delay. Falls back to a short grace period for pages that never go
    fully idle (analytics beacons, polling widgets)."""

    try:
        page.wait_for_load_state("networkidle", timeout=NAV_TIMEOUT)
    except Exception:
        page.wait_for_timeout(1500)


def _click_portal_link_if_present(page):
    link = page.get_by_role("link", name=PORTAL_LINK_PATTERN).first

    try:
        if link.count() == 0:
            return
        link.click(timeout=3000)
        page.wait_for_load_state("domcontentloaded", timeout=NAV_TIMEOUT)
        _settle(page)
    except Exception:
        pass


def _decode_bing_redirect(href):
    """Bing wraps organic results in a bing.com/ck/a tracking redirect whose
    real destination is base64-encoded in the 'u' query param (prefixed
    with 'a1') rather than an HTTP redirect, so it has to be decoded
    client-side instead of just following the link."""

    query = parse_qs(urlparse(href).query)
    encoded = query.get("u", [None])[0]

    if not encoded:
        return href

    body = encoded[2:] if encoded.startswith("a1") else encoded
    body += "=" * (-len(body) % 4)

    try:
        return base64.b64decode(body).decode()
    except Exception:
        return href


def _search_and_open(page, query):
    page.goto(f"https://www.bing.com/search?q={quote(query)}", wait_until="domcontentloaded")
    _settle(page)

    try:
        href = page.locator("#b_results h2 a").first.get_attribute("href", timeout=5000)
    except Exception:
        href = None

    if not href:
        # Couldn't confidently pick a top result — leave the search page open.
        return

    page.goto(_decode_bing_redirect(href), wait_until="domcontentloaded")
    _settle(page)
    _click_portal_link_if_present(page)


def _extract_page_result(page, verb="Opened"):
    """Shared by open_and_read and search_on_page: pull the current page's
    text, watch for a CAPTCHA, and format it the same way either time."""

    final_url = page.url
    text = page.inner_text("body").strip()[:MAX_PAGE_TEXT]

    if CAPTCHA_PATTERN.search(text[:500]):
        return (
            f"{verb} {final_url}\n\nThis site is showing a human-"
            "verification check (CAPTCHA/'are you a robot'). That's "
            "not something to automate around — please complete it "
            "yourself in the browser window, then ask again."
        )

    if len(text) < 40:
        text += (
            "\n\n(This page returned almost no text — it may still be "
            "loading, need JavaScript interaction, or be a login form. "
            "Check the browser window directly.)"
        )

    return f"{verb} {final_url}\n\nPage content:\n{text}"


def open_and_read(target):

    target = target.strip()
    try:
        page = _get_live_page()
    except BrowserBusyError as error:
        return f"{error} Try again in a moment."

    try:
        if URL_LIKE.match(target):
            url = target if target.startswith(("http://", "https://")) else f"https://{target}"
            page.goto(url, wait_until="domcontentloaded")
        else:
            _search_and_open(page, target)

        _settle(page)
        return _extract_page_result(page, verb="Opened")

    except Exception as error:
        return f"Opened the browser but had trouble loading the page: {error}"


def search_on_page(query):
    """Types into whatever search box is on the current page and submits
    it — the missing piece for "open Walmart and search for X": open_browser
    can only navigate/search externally, it has no way to act on the page
    once it's loaded. This finds the page's own search field (trying
    several real-world patterns, since sites vary — Walmart uses
    input[type='search'], Amazon uses input[type='text'] with a matching
    aria-label) and drives it directly."""

    try:
        page = _get_live_page()
    except BrowserBusyError as error:
        return f"{error} Try again in a moment."

    try:
        search_field = None
        for selector in SEARCH_INPUT_SELECTORS:
            locator = page.locator(selector).first
            try:
                if locator.count() > 0 and locator.is_visible():
                    search_field = locator
                    break
            except Exception:
                continue

        if search_field is None:
            return (
                f"Couldn't find a search box on {page.url}. It might use a "
                "non-standard search UI (e.g. a button that opens a search "
                "overlay) — check the browser window directly."
            )

        # Heavily-scripted commercial sites often have a transient overlay
        # (a loading spinner, an autocomplete container, an ad) that
        # intercepts pointer events for a moment even after the field
        # itself is visible and enabled — confirmed live against Walmart,
        # where this is intermittent rather than constant. A short retry
        # covers the transient case; force-filling is the last resort for
        # a decorative overlay that never actually clears, with a
        # readback check so a silent no-op is caught rather than assumed
        # to have worked.
        filled = False
        for _ in range(2):
            try:
                search_field.click(timeout=5000)
                search_field.fill(query, timeout=5000)
                filled = True
                break
            except Exception:
                page.wait_for_timeout(1000)

        if not filled:
            search_field.fill(query, timeout=5000, force=True)

        if search_field.input_value() != query:
            return (
                f"Found a search box on {page.url} but couldn't get it to "
                "actually hold the search text — it may be blocked by an "
                "overlay. Check the browser window directly."
            )

        search_field.press("Enter")
        _settle(page)

        return _extract_page_result(page, verb="Searched")

    except Exception as error:
        return f"Found a search box but had trouble using it: {error}"


def click_on_page(text):
    """Clicks a link or button on the current page by its visible text —
    the rest of "open it up and do something": after search_on_page lands
    on results, this is how Jarvis picks a specific product/link rather
    than stopping there. Page-scoped only (Playwright's DOM, not screen
    coordinates) — it can't reach outside the one browser tab it already
    controls, same boundary as every other browser tool here."""

    try:
        page = _get_live_page()
    except BrowserBusyError as error:
        return f"{error} Try again in a moment."

    try:
        target = page.get_by_role("link", name=text, exact=False).first
        if target.count() == 0:
            target = page.get_by_role("button", name=text, exact=False).first
        if target.count() == 0:
            target = page.get_by_text(text, exact=False).first

        if target.count() == 0:
            return f"Couldn't find anything matching '{text}' to click on {page.url}."

        try:
            target.click(timeout=5000)
        except Exception:
            target.click(timeout=5000, force=True)

        _settle(page)
        return _extract_page_result(page, verb="Clicked into")

    except Exception as error:
        return f"Found '{text}' but had trouble clicking it: {error}"
