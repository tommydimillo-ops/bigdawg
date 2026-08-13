"""Real mouse/keyboard control, driven by a vision model reading the
screen. This is the one tool family in this project that can act outside
its own sandbox in an open-ended way, so it follows a narrower safety
model than everything else here:

- computer_see / computer_locate / computer_click / computer_type /
  computer_press_key execute immediately -- no confirmation gate. These
  cover navigating, reading, and interacting with on-screen UI.
- computer_confirm_action is the ONLY way to perform a click or keypress
  that sends, pays, deletes, or submits something real. It follows the
  same two-step, human-approved pattern as confirm_login/send_email
  elsewhere in this project: describe the action, wait for the user to
  explicitly say yes in a later message, then call this.
- computer_type must never be used to type a password, PIN, or other
  credential -- fill_login/confirm_login handle real logins via Keychain
  without the password ever passing through chat.

Every action here is logged (via the normal executor -> audit.py path,
same as any other tool) and also saves its own screenshot for a visual
record, since "what did it actually click" matters more here than for a
tool that just calls an API.
"""
import base64
import os
import re
import time
from contextlib import contextmanager
from datetime import datetime

import pyautogui

from agent.chat import anthropic_client
from agent.computer_use_status import set_active
from tools.screen import MAX_DIMENSION
from tools.vision import analyze_image

SCREENSHOT_DIR = os.path.expanduser(
    "~/Library/Application Support/CampusPilot/computer_use_screenshots"
)

# A beat between actions -- real UI (page loads, menus opening, fields
# focusing) needs a moment to respond; firing clicks/keys back-to-back
# with zero pause is a common cause of actions landing before the UI
# catches up.
pyautogui.PAUSE = 0.3


@contextmanager
def _active_guard():
    set_active(True)
    try:
        yield
    finally:
        set_active(False)


def _capture():
    """Takes a full screenshot, saves a downscaled copy (for both vision
    analysis and the audit trail), and returns (path, scale_x, scale_y) --
    the factors needed to convert a pixel position in that downscaled
    image back into real pyautogui click coordinates. pyautogui's click
    coordinates use logical/points resolution, not the raw Retina pixel
    size a screenshot actually captures at (e.g. 1680x1050 vs 3360x2100
    on this Mac), so this scale has to go through pyautogui.size() rather
    than assuming the screenshot's own pixel dimensions are click-safe."""

    os.makedirs(SCREENSHOT_DIR, exist_ok=True)
    filename = f"computer_use_{datetime.now().strftime('%Y%m%d_%H%M%S%f')}.jpg"
    path = os.path.join(SCREENSHOT_DIR, filename)

    screenshot = pyautogui.screenshot()
    screenshot.thumbnail((MAX_DIMENSION, MAX_DIMENSION))
    screenshot.convert("RGB").save(path, "JPEG", quality=85)

    logical_width, logical_height = pyautogui.size()
    scale_x = logical_width / screenshot.width
    scale_y = logical_height / screenshot.height

    return path, scale_x, scale_y


def computer_see():
    """Takes a screenshot right now and describes what's on it."""
    path, _, _ = _capture()
    return analyze_image(path)


def computer_locate(description):
    """Finds the on-screen location of a described UI element (a button,
    field, link, icon) and returns real click-ready x/y coordinates for
    computer_click / computer_confirm_action."""

    path, scale_x, scale_y = _capture()

    with open(path, "rb") as f:
        encoded = base64.b64encode(f.read()).decode("utf-8")

    response = anthropic_client.messages.create(
        model="claude-sonnet-5",
        # 200 was tight enough that adaptive extended thinking could
        # consume the whole budget and leave zero tokens for the actual
        # "x,y" answer -- same failure mode already documented in
        # executor.py's main loop, hit here too on a live test.
        max_tokens=1024,
        messages=[{
            "role": "user",
            "content": [
                {
                    "type": "image",
                    "source": {"type": "base64", "media_type": "image/jpeg", "data": encoded},
                },
                {
                    "type": "text",
                    "text": (
                        f'Find "{description}" in this screenshot. Reply with ONLY '
                        'the pixel coordinates of its center as "x,y" (e.g. "412,290"), '
                        'using this image\'s own pixel dimensions. If it is not visible, '
                        'reply with exactly "NOT_FOUND".'
                    ),
                },
            ],
        }],
    )

    # response.content[0] isn't reliably the text block -- Claude Sonnet 5's
    # adaptive extended thinking can put a ThinkingBlock first.
    text = "".join(block.text for block in response.content if block.type == "text").strip()

    if "NOT_FOUND" in text.upper():
        return f"Couldn't find '{description}' on screen."

    match = re.search(r"(\d+)\s*,\s*(\d+)", text)
    if not match:
        return f"Couldn't parse a location for '{description}' (model said: {text})"

    img_x, img_y = int(match.group(1)), int(match.group(2))
    real_x, real_y = round(img_x * scale_x), round(img_y * scale_y)

    return f"Found '{description}' at x={real_x}, y={real_y}."


def computer_click(x, y):
    with _active_guard():
        pyautogui.click(x, y)
    return f"Clicked at ({x}, {y})."


def computer_type(text):
    with _active_guard():
        pyautogui.write(text, interval=0.02)
    preview = text if len(text) <= 60 else text[:60] + "..."
    return f"Typed: {preview}"


def computer_press_key(key):
    with _active_guard():
        keys = [k.strip() for k in key.lower().split("+") if k.strip()]
        if len(keys) > 1:
            pyautogui.hotkey(*keys)
        else:
            pyautogui.press(keys[0])
    return f"Pressed: {key}"


def computer_confirm_action(description, x=None, y=None, key=None):
    """Executes the actual send/pay/delete/submit step -- only call this
    after the user has explicitly confirmed in their own words in a later
    message. Provide either x/y (to click) or key (to press, e.g. 'enter'
    or 'cmd+return')."""

    if x is None and y is None and key is None:
        return "No action specified — provide x and y to click, or key to press."

    with _active_guard():
        if key:
            keys = [k.strip() for k in key.lower().split("+") if k.strip()]
            if len(keys) > 1:
                pyautogui.hotkey(*keys)
            else:
                pyautogui.press(keys[0])
        else:
            pyautogui.click(x, y)

        # Give the resulting page/app state a moment to settle before the
        # after-action screenshot, so the audit record shows the actual
        # result rather than a mid-transition frame.
        time.sleep(0.5)
        after_path, _, _ = _capture()

    return f"Confirmed action executed: {description} (after: {after_path})"
