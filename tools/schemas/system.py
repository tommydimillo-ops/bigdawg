"""Music/clipboard/timer/weather/Mac-status tools."""
from tools.clipboard import get_clipboard, set_clipboard
from tools.music import control_music
from tools.registry import ToolSpec, register
from tools.system_control import lock_screen, sleep_mac
from tools.system_status import get_system_status
from tools.timer import set_timer
from tools.weather import get_weather, get_weekly_forecast

register(ToolSpec(
    name="control_music",
    description=(
        "Control playback in the Music app: play, pause, skip to the "
        "next/previous track, or play a specific song/artist/album from "
        "the user's library."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "description": (
                    "One of: play, pause, playpause, 'next track', "
                    "'previous track'. Ignored if query is given."
                ),
            },
            "query": {
                "type": "string",
                "description": (
                    "Optional song, artist, or album to search for and "
                    "play instead of a plain transport action."
                ),
            },
        },
        "required": ["action"],
    },
    permission_level=1,
    handler=lambda ti: control_music(ti["action"], ti.get("query")),
))

register(ToolSpec(
    name="get_system_status",
    description=(
        "Check the Mac's battery level, free disk space, Wi-Fi "
        "network, and uptime."
    ),
    input_schema={"type": "object", "properties": {}, "required": []},
    permission_level=0,
    handler=lambda ti: get_system_status(),
    parallel_safe=True,
))

register(ToolSpec(
    name="get_weather",
    description=(
        "Get current weather and an hourly forecast for the next few "
        "hours. A direct weather service, not a browser search — use "
        "this for any weather question instead of open_browser, since "
        "it's faster and can't hit a CAPTCHA/human-verification wall."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "location": {
                "type": "string",
                "description": "City or place name. Omit to resolve based on local network location.",
            }
        },
        "required": [],
    },
    permission_level=0,
    handler=lambda ti: get_weather(ti.get("location")),
    parallel_safe=True,
))

register(ToolSpec(
    name="get_weekly_forecast",
    description=(
        "Get a 7-day weather forecast (daily high/low, conditions, "
        "chance of rain). Use this instead of get_weather when the "
        "user asks about the week ahead, a specific future day, or "
        "wants to plan around upcoming weather — get_weather only "
        "covers right now plus the next few hours."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "location": {
                "type": "string",
                "description": "City or place name. Omit to resolve based on local network location.",
            }
        },
        "required": [],
    },
    permission_level=0,
    handler=lambda ti: get_weekly_forecast(ti.get("location")),
    parallel_safe=True,
))

register(ToolSpec(
    name="lock_screen",
    description="Lock the Mac's screen immediately.",
    input_schema={"type": "object", "properties": {}, "required": []},
    permission_level=1,
    handler=lambda ti: lock_screen(),
))

register(ToolSpec(
    name="sleep_mac",
    description="Put the Mac to sleep immediately.",
    input_schema={"type": "object", "properties": {}, "required": []},
    permission_level=1,
    handler=lambda ti: sleep_mac(),
))

register(ToolSpec(
    name="get_clipboard",
    description="Read the current contents of the clipboard.",
    input_schema={"type": "object", "properties": {}, "required": []},
    permission_level=0,
    handler=lambda ti: get_clipboard(),
    parallel_safe=True,
))

register(ToolSpec(
    name="set_clipboard",
    description="Copy text to the clipboard.",
    input_schema={
        "type": "object",
        "properties": {
            "text": {"type": "string", "description": "Text to copy."}
        },
        "required": ["text"],
    },
    permission_level=1,
    handler=lambda ti: set_clipboard(ti["text"]),
))

register(ToolSpec(
    name="set_timer",
    description="Set a countdown timer that fires a native notification when it's up. For a one-off countdown ('remind me in 10 minutes') — for a specific future date/time, use add_reminder instead.",
    input_schema={
        "type": "object",
        "properties": {
            "minutes": {"type": "number", "description": "How many minutes from now."},
            "label": {"type": "string", "description": "What the notification should say. Defaults to \"Time's up\"."},
        },
        "required": ["minutes"],
    },
    permission_level=1,
    handler=lambda ti: set_timer(ti["minutes"], ti.get("label", "Time's up")),
))
