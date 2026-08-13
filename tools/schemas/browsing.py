"""Screen/browser/app-launching tools."""
import os

from tools.browser import click_on_page, open_and_read, search_on_page
from tools.computer import open_application
from tools.registry import ToolSpec, register
from tools.screen import take_screenshot
from tools.vision import analyze_image


def _handle_take_screenshot(tool_input):
    image = take_screenshot()
    try:
        return analyze_image(image)
    finally:
        os.remove(image)


register(ToolSpec(
    name="take_screenshot",
    description=(
        "Capture the user's screen right now and describe what's on it. "
        "Use only when the user asks what's on their screen or references "
        "something currently visible."
    ),
    input_schema={"type": "object", "properties": {}, "required": []},
    permission_level=0,
    handler=_handle_take_screenshot,
))

register(ToolSpec(
    name="open_browser",
    description=(
        "Open a real browser window and navigate to the actual "
        "destination — not a search-results page. Prefer giving it a "
        "real domain when you know one (e.g. 'walmart.com', "
        "'fidelity.com') — that goes straight there. If given plain "
        "words instead (e.g. 'stock price of Apple'), it searches, "
        "clicks through to the most relevant real page (following a "
        "login/portal link one level deep if the top result is a hub "
        "page), and returns that page's visible text — but that route "
        "depends on a search engine's page layout and can occasionally "
        "fail to click through cleanly, so it's the fallback, not the "
        "first choice, for a site you can actually name the domain of. "
        "Use this for anything that isn't a literal installed Mac app — "
        "school/course portals, specific websites, stock prices, "
        "sports scores, or any other general web lookup. NOT weather — "
        "use get_weather for that instead, always."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "target": {
                "type": "string",
                "description": "A URL (e.g. 'example.com') or a search query.",
            }
        },
        "required": ["target"],
    },
    permission_level=1,
    handler=lambda ti: open_and_read(ti["target"]),
))

register(ToolSpec(
    name="search_on_page",
    description=(
        "Type a query into the current page's own search box and "
        "submit it (e.g. searching for a product within a site you've "
        "already opened with open_browser). This acts on whatever page "
        "is currently open — use open_browser first to get there."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "What to search for on the current page.",
            }
        },
        "required": ["query"],
    },
    permission_level=1,
    handler=lambda ti: search_on_page(ti["query"]),
))

register(ToolSpec(
    name="click_on_page",
    description=(
        "Click a link or button on the current page, matched by its "
        "visible text (e.g. a product name, 'Add to Cart', a result "
        "title). Use after open_browser or search_on_page to go one "
        "level deeper into a site."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "text": {
                "type": "string",
                "description": "The visible text of the link/button to click.",
            }
        },
        "required": ["text"],
    },
    permission_level=1,
    handler=lambda ti: click_on_page(ti["text"]),
))


def _handle_open_application(tool_input):
    app_name = tool_input["app_name"]
    result = open_application(app_name)
    if result.startswith("Could not open"):
        return open_and_read(app_name)
    return result


register(ToolSpec(
    name="open_application",
    description=(
        "The default tool for any 'open <something>' or 'search up "
        "<something>' request naming a destination. Tries <something> "
        "as an installed macOS app first (e.g. Calculator, Safari, "
        "Notes, Calendar, Mail, Finder, Google Chrome); if it's not a "
        "real app, it automatically falls back to opening it as a "
        "website instead — pass a real domain here when you know one "
        "(e.g. 'walmart.com' rather than 'Walmart') so that fallback "
        "goes straight to the real site instead of through a web "
        "search first."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "app_name": {
                "type": "string",
                "description": "The application name to open.",
            }
        },
        "required": ["app_name"],
    },
    permission_level=1,
    handler=_handle_open_application,
))
