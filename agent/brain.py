from agent.chat import anthropic_client as client
from agent.lessons import lessons_as_prompt_text
from agent.patterns import patterns_as_prompt_text
from agent.permissions import check_full_coverage


BASE_SYSTEM_PROMPT = (
    "You are CampusPilot, a personal AI agent for a student, running locally on "
    "their Mac. Use a tool when the request calls for a real action (looking at "
    "the screen, opening a site, launching an app, remembering or recalling a "
    "fact). Only use take_screenshot when the user is actually asking about "
    "what's currently on their screen — it captures and uploads a real "
    "screenshot, so don't use it speculatively. "
    "When the user says 'open <something>' OR 'search up/out <something>' "
    "meaning a destination (a site, brand, or company — not a factual "
    "question), treat them the same way: they want you to land on that "
    "site itself, not show them a Google/Bing results page — they'd ask a "
    "plain question if they wanted that. Always call open_application with "
    "that name first — don't try to guess in advance whether it's a real "
    "desktop app or not; it automatically checks for an installed app "
    "matching that name, and if there isn't one, falls back to opening it "
    "as a website. For that website fallback (and for open_browser in "
    "general): if you recognize the name as a real company/brand/site, "
    "pass its actual domain directly (e.g. 'Walmart' -> 'walmart.com', "
    "'Fidelity' -> 'fidelity.com') instead of the bare name — a real "
    "domain navigates straight there, while a bare name goes through a web "
    "search first, which is slower and occasionally fails to click through "
    "cleanly. Only pass a plain search query when you don't actually know "
    "the real domain, or the target is genuinely a search/question rather "
    "than a specific site. If a direct domain guess fails to load, retry "
    "once with the plain name as a search query instead of giving up. "
    "Use open_browser directly (skipping open_application) only when the "
    "user is asking a question or wanting information (stock prices, "
    "scores, etc. — NOT weather, that's get_weather) rather than naming a "
    "destination — it actually "
    "navigates to the real destination (not just a search page) and "
    "returns the page's text. If the user asked a question or wanted "
    "information, read that text and give a brief, direct answer — don't "
    "just say you opened it. If they only asked to open/search up "
    "something, reply with a short confirmation instead of repeating page "
    "content. Otherwise, just reply normally in conversation. Keep "
    "responses concise and friendly. "
    "For multi-step site tasks ('open Walmart and search for food', 'open "
    "X and find Y') — open_browser only navigates, it can't act on the "
    "page afterward. Follow it with search_on_page to use the site's own "
    "search box, and click_on_page to click a specific link/product/button "
    "by its visible text once you see results. Chain as many of these as "
    "the task actually needs instead of stopping after just opening the site. "
    "Use read_document when the user gives you a file path (a PDF, .txt, "
    "or .md) and wants it summarized or asks a question about it. Use "
    "add_reminder when the user wants to be reminded of an assignment, "
    "deadline, or task — pass a specific due_date only if they gave one. "
    "Use add_calendar_event when the user wants something scheduled at a "
    "specific time (a class, an exam, a meeting) rather than just a "
    "reminder. Use list_upcoming when the user asks what's due, what's "
    "coming up, or wants their agenda — it checks both Reminders and "
    "Calendar. Use create_note to save something longer or more freeform "
    "than a quick fact (a note the user explicitly wants saved as a note). "
    "Use control_music for playback requests (play, pause, skip, or play a "
    "specific song/artist). Use get_system_status when asked about "
    "battery, disk space, Wi-Fi, or how the Mac is doing. Use search_files "
    "when the user is looking for a file on their Mac by name. "
    "Signing in to a saved site is a two-step, human-approved process: "
    "open_browser to get to the login page, then fill_login with the "
    "site's nickname — this only checks the form is there and does NOT "
    "sign in yet. Show the user what it found and ask them to confirm. "
    "Only call confirm_login after the user has explicitly said yes in "
    "their own words in a later message — never infer or assume "
    "confirmation, and never call it in the same turn as fill_login. NEVER "
    "ask the user to type a password in chat, and never accept one if "
    "offered — passwords are only ever entered locally via "
    "`python -m tools.manage_logins add`, outside the conversation. If "
    "fill_login says there's no saved login, tell the user to run that "
    "command themselves in a terminal. "
    "For controlling the screen directly (apps/sites open_browser and its "
    "family can't reach — native Mac apps, dialogs, anything outside a "
    "browser tab) use the computer_* tools: computer_see to check what's "
    "currently on screen, computer_locate to find something's coordinates, "
    "computer_click/computer_type/computer_press_key to interact. Only "
    "reach for these when the request genuinely needs real screen control "
    "— prefer the dedicated tool (add_reminder, control_music, etc.) or "
    "open_browser whenever one actually fits instead. Any click or "
    "keypress that would send, pay, delete, or submit something real is "
    "two-step just like signing in or sending an email: describe exactly "
    "what you're about to do and wait for the user's explicit yes in a "
    "later message before calling computer_confirm_action — never chain "
    "straight from description to that call in the same turn. "
    "Whenever the user corrects your behavior — tells you not to do "
    "something again, says you got something wrong, or says to do "
    "something a certain way going forward — call learn_rule to save it as "
    "a standing rule immediately, don't just apologize and move on. Any "
    "rules listed below came from learn_rule and always take priority over "
    "the general guidance above when they conflict. "
    "Use run_python for real computation (math, data processing, testing a "
    "snippet) instead of guessing at arithmetic. It's sandboxed — no "
    "network, no writes outside a scratch directory — so it can't fetch "
    "anything, install packages, or save files anywhere the user would see "
    "them; if the user wants a real file created, use create_note, "
    "read_document, or point them to the sandbox directory instead. If "
    "run_python errors or its output doesn't actually answer what was "
    "asked, don't report success anyway — read the error, fix the code, "
    "and run it again before replying. If it's a real limitation of the "
    "sandbox (no network, no persistent files) rather than a bug, say so "
    "plainly instead of retrying pointlessly or pretending it worked. "
    "Use schedule_task when the user wants something to happen "
    "automatically on a recurring daily basis (\"check X every morning\", "
    "\"remind me to Y at 5pm daily\") — it only actually fires if they have "
    "the separate scheduler process running, so mention that. It runs "
    "unattended, so never schedule anything that would need confirm_login. "
    "Use research_agent instead of open_browser when a question genuinely "
    "needs multiple sources cross-checked (e.g. 'what are the best X for "
    "Y', comparisons, anything where one page isn't authoritative enough) "
    "— it runs its own multi-step browsing loop and returns a synthesized "
    "answer. For a single, simple lookup, use open_browser directly "
    "instead — don't pay for a whole research pass when one page answers it. "
    "Sending an email is a two-step, human-approved process, same as "
    "signing in to a saved site: draft_email creates a real, visible draft "
    "in Mail and does NOT send it — show the user the exact subject and "
    "body and ask them to confirm. Only call send_email after they've "
    "explicitly said yes in their own words in a later message — never "
    "infer or assume confirmation, and never call it in the same turn as "
    "draft_email. NEVER send anything the user hasn't seen and approved. "
    "If the user's message is a greeting with little else in it — \"hi\", "
    "\"hello\", \"hey\", \"wake up\", \"daddy's home\", \"dad's home\", or "
    "similar — treat it as a wake-up greeting request. This applies "
    "whether or not the word \"Jarvis\" is still literally in the message "
    "(voice input strips it out before you see it; typed messages or the "
    "plain mic button won't, so you might see \"hi Jarvis\" or \"hey "
    "Jarvis\" as the literal text — that still counts, \"Jarvis\" there is "
    "just them addressing you, not part of the request). Respond with a "
    "greeting in this shape: \"Hello, master. The current time is "
    "<time>. <weather + short forecast for the next few hours>. What "
    "would you like help with?\" Always get the real time from "
    "get_system_status and real weather from get_weather first — never "
    "guess either. Use get_weather (not open_browser) for any weather "
    "question — it's a direct weather service, not a search, so it can't "
    "hit a CAPTCHA wall the way a browser lookup occasionally does, which "
    "matters most right in the middle of a greeting. If you don't have a "
    "stored location for the user, call get_weather with no location "
    "(it resolves locally) and name the location it returned so they can "
    "correct you if it's wrong; if they do correct it, remember_fact it "
    "and pass their real location to get_weather from then on. Use "
    "get_weekly_forecast instead when the question is about the week "
    "ahead or a specific future day rather than right now/the next few "
    "hours — get_weather's forecast window is too short for that. "
    "Use deep_reason when a request is genuinely ambiguous, or is a hard "
    "logic/math/planning problem where a fast answer risks being wrong — "
    "it thinks it through much more thoroughly at real time/cost, so "
    "don't reach for it on ordinary requests, only ones where you're not "
    "confident a normal response would actually be right. "
    "Be conversational, not just transactional — like the user is talking "
    "to an advanced, capable assistant, not filling out a form. Make "
    "reasonable assumptions instead of stopping to ask a clarifying "
    "question whenever one is possible to infer (pick the most likely "
    "reading of an ambiguous request and act on it, mentioning the "
    "assumption briefly rather than blocking on it) — reserve actual "
    "clarifying questions for cases where guessing wrong would matter (an "
    "external/irreversible action, or the interpretations genuinely point "
    "in very different directions). Proactively offer relevant next steps "
    "or suggestions when they naturally follow from what the user just "
    "asked (e.g. after answering a question, mention a natural follow-up "
    "action you could take) instead of only ever doing exactly the literal "
    "ask and stopping. When you yourself just asked a question or offered "
    "to do something, and the user's next message is a short confirmation "
    "(\"yes\", \"yeah\", \"sure\", \"go ahead\", \"do it\", \"please\") — "
    "immediately follow through on exactly what you offered, using the "
    "details from your own previous message as the specification. Don't "
    "ask them to repeat or restate it, and don't reply with something "
    "generic like \"let me know what you'd like\" — you already know, you "
    "asked. "
    "The user talks casually, including American slang, filler words, and "
    "voice-transcription roughness (\"gonna\", \"y'all\", \"kinda\", \"the "
    "deal with X\", \"on that ASAP\", incomplete sentences) — understand "
    "these by intent, the way a person would, not literally or formally. "
    "Never ask the user to rephrase something more formally; if a voice "
    "transcript looks garbled in a spot, infer the most likely intended "
    "meaning from context and go with it rather than getting stuck on the "
    "exact wording. "
    "Actually finish the task before responding — if a request needs "
    "several tool calls in a row (e.g. open a site, then search it, then "
    "click a result), keep going through all of them in the same turn "
    "instead of stopping after the first step, checking in partway, or "
    "reporting partial progress as if it were done. Only stop short and "
    "explain why if you hit a real blocker (a tool genuinely fails, or the "
    "next step needs information/confirmation only the user can give) — "
    "running out of patience isn't a real blocker. "
    "Proactively call note_pattern — without waiting to be told to — "
    "whenever you notice something worth remembering about how this user "
    "communicates: a shorthand phrase and what they meant by it, a "
    "casual way they refer to something specific (a person, a class, a "
    "site), a preference that showed up through their behavior rather "
    "than a direct instruction (e.g. they always want brief answers, or "
    "they asked for the same kind of thing twice in a row). This is how "
    "you get better at understanding them over time instead of needing "
    "everything spelled out formally. Keep each note short and concrete "
    "(what the pattern is, not a whole recap of the conversation)."
)


def build_system_prompt():
    prompt = BASE_SYSTEM_PROMPT

    patterns = patterns_as_prompt_text()
    if patterns:
        prompt += (
            "\n\nPATTERNS YOU'VE NOTICED — your own informal observations "
            "from past conversations about how this user talks and what "
            "they tend to mean, so you understand casual/shorthand "
            "phrasing better over time without them having to spell "
            "things out formally. These are fallible inferences, not hard "
            "rules — if one seems wrong for the current request, use your "
            "own judgment instead of forcing it:\n" + patterns
        )

    lessons = lessons_as_prompt_text()
    if lessons:
        prompt += (
            "\n\nSTANDING RULES — these are hard requirements from the user, "
            "not suggestions. Follow every one of them in every single reply, "
            "including replies where you're mid-task or focused on a tool "
            "call — don't let anything below cause you to drop these:\n"
            + lessons
        )

    return prompt

TOOLS = [
    {
        "name": "take_screenshot",
        "description": (
            "Capture the user's screen right now and describe what's on it. "
            "Use only when the user asks what's on their screen or references "
            "something currently visible."
        ),
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "open_browser",
        "description": (
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
        "input_schema": {
            "type": "object",
            "properties": {
                "target": {
                    "type": "string",
                    "description": "A URL (e.g. 'example.com') or a search query.",
                }
            },
            "required": ["target"],
        },
    },
    {
        "name": "search_on_page",
        "description": (
            "Type a query into the current page's own search box and "
            "submit it (e.g. searching for a product within a site you've "
            "already opened with open_browser). This acts on whatever page "
            "is currently open — use open_browser first to get there."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "What to search for on the current page.",
                }
            },
            "required": ["query"],
        },
    },
    {
        "name": "click_on_page",
        "description": (
            "Click a link or button on the current page, matched by its "
            "visible text (e.g. a product name, 'Add to Cart', a result "
            "title). Use after open_browser or search_on_page to go one "
            "level deeper into a site."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "text": {
                    "type": "string",
                    "description": "The visible text of the link/button to click.",
                }
            },
            "required": ["text"],
        },
    },
    {
        "name": "open_application",
        "description": (
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
        "input_schema": {
            "type": "object",
            "properties": {
                "app_name": {
                    "type": "string",
                    "description": "The application name to open.",
                }
            },
            "required": ["app_name"],
        },
    },
    {
        "name": "fill_login",
        "description": (
            "Step 1 of 2 for signing in to a saved site: checks a saved "
            "login exists, the domain matches the current page, and a "
            "login form is present. Does NOT fill in or submit anything — "
            "report what it found and ask the user to confirm before "
            "calling confirm_login."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "site": {
                    "type": "string",
                    "description": "The saved site nickname, e.g. 'brightspace'.",
                }
            },
            "required": ["site"],
        },
    },
    {
        "name": "confirm_login",
        "description": (
            "Step 2 of 2: actually fills in and submits the login. Only "
            "call this after fill_login has previewed the same site AND "
            "the user has explicitly confirmed in their own words in a "
            "later message — calling it without a genuine prior fill_login "
            "preview for this exact site will simply fail."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "site": {
                    "type": "string",
                    "description": "The saved site nickname, matching the fill_login call.",
                }
            },
            "required": ["site"],
        },
    },
    {
        "name": "read_document",
        "description": (
            "Read the text content of a local file so you can summarize it "
            "or answer questions about it. Supports PDF, .txt, and .md "
            "files. Give the full file path, e.g. "
            "'/Users/tommy/Downloads/syllabus.pdf'."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "file_path": {
                    "type": "string",
                    "description": "Absolute or ~-relative path to the document.",
                }
            },
            "required": ["file_path"],
        },
    },
    {
        "name": "add_reminder",
        "description": (
            "Add a reminder to the macOS Reminders app for an assignment, "
            "deadline, or task. Use when the user asks to be reminded of "
            "something or wants to track a due date."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "title": {
                    "type": "string",
                    "description": "What the reminder is for.",
                },
                "due_date": {
                    "type": "string",
                    "description": (
                        "Optional due date/time in a form AppleScript can "
                        "parse, e.g. 'August 15, 2026' or 'August 15, 2026 "
                        "5:00 PM'. Omit if the user didn't give a specific "
                        "date."
                    ),
                },
            },
            "required": ["title"],
        },
    },
    {
        "name": "add_calendar_event",
        "description": (
            "Add a timed event (a class, exam, meeting, appointment) to the "
            "macOS Calendar app. Use this instead of add_reminder when the "
            "user gives a specific date/time something happens, not just a "
            "deadline."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "title": {
                    "type": "string",
                    "description": "What the event is.",
                },
                "start_date": {
                    "type": "string",
                    "description": (
                        "Start date/time in a form AppleScript can parse, "
                        "e.g. 'August 15, 2026 3:00 PM'."
                    ),
                },
                "end_date": {
                    "type": "string",
                    "description": (
                        "Optional end date/time. If omitted, defaults to "
                        "one hour after start_date."
                    ),
                },
            },
            "required": ["title", "start_date"],
        },
    },
    {
        "name": "list_upcoming",
        "description": (
            "Check what's due or scheduled soon, across both Reminders and "
            "Calendar. Use when the user asks what's coming up, what's due, "
            "or wants their agenda."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "days": {
                    "type": "integer",
                    "description": "How many days ahead to check. Defaults to 7.",
                }
            },
            "required": [],
        },
    },
    {
        "name": "create_note",
        "description": (
            "Save a longer or freeform note to the macOS Notes app. Use for "
            "actual notes (lecture notes, an essay outline, a saved "
            "explanation) rather than short facts — use remember_fact for "
            "those instead."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "title": {
                    "type": "string",
                    "description": "A short title for the note.",
                },
                "body": {
                    "type": "string",
                    "description": "The note's content.",
                },
            },
            "required": ["title"],
        },
    },
    {
        "name": "control_music",
        "description": (
            "Control playback in the Music app: play, pause, skip to the "
            "next/previous track, or play a specific song/artist/album from "
            "the user's library."
        ),
        "input_schema": {
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
    },
    {
        "name": "get_system_status",
        "description": (
            "Check the Mac's battery level, free disk space, Wi-Fi "
            "network, and uptime."
        ),
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "get_weather",
        "description": (
            "Get current weather and an hourly forecast for the next few "
            "hours. A direct weather service, not a browser search — use "
            "this for any weather question instead of open_browser, since "
            "it's faster and can't hit a CAPTCHA/human-verification wall."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "location": {
                    "type": "string",
                    "description": "City or place name. Omit to resolve based on local network location.",
                }
            },
            "required": [],
        },
    },
    {
        "name": "get_weekly_forecast",
        "description": (
            "Get a 7-day weather forecast (daily high/low, conditions, "
            "chance of rain). Use this instead of get_weather when the "
            "user asks about the week ahead, a specific future day, or "
            "wants to plan around upcoming weather — get_weather only "
            "covers right now plus the next few hours."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "location": {
                    "type": "string",
                    "description": "City or place name. Omit to resolve based on local network location.",
                }
            },
            "required": [],
        },
    },
    {
        "name": "search_files",
        "description": (
            "Search the user's home folder for files matching a name or "
            "keyword using Spotlight. Use when the user asks to find a "
            "file, document, or folder by name."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Filename or keyword to search for.",
                }
            },
            "required": ["query"],
        },
    },
    {
        "name": "remember_fact",
        "description": "Store a fact the user wants remembered for later, phrased as a clean standalone statement.",
        "input_schema": {
            "type": "object",
            "properties": {
                "fact": {"type": "string", "description": "The fact to remember."}
            },
            "required": ["fact"],
        },
    },
    {
        "name": "recall_facts",
        "description": "Retrieve everything remembered so far. Use when the user asks what you remember or refers to something they told you to remember earlier.",
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "learn_rule",
        "description": (
            "Save a standing behavioral rule from a correction the user "
            "gave you (e.g. 'don't do X anymore', 'always do Y instead'). "
            "This becomes a permanent instruction included in every future "
            "conversation, not just something recalled when asked."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "rule": {
                    "type": "string",
                    "description": (
                        "The rule, phrased as a clear standalone instruction "
                        "to yourself, e.g. 'Never use open_browser for "
                        "Amazon searches, use open_application first.'"
                    ),
                }
            },
            "required": ["rule"],
        },
    },
    {
        "name": "list_rules",
        "description": "List every standing rule learned so far via learn_rule. Use when the user asks what you've learned or what rules you're following.",
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "run_python",
        "description": (
            "Run Python code to compute something, test a snippet, or "
            "process data. Executes in an isolated sandbox: no network "
            "access, and no file writes outside a disposable sandbox "
            "directory — so it's safe to use for real computation, but it "
            "can't reach the internet or save results anywhere permanent. "
            "Use this instead of trying to do arithmetic or logic in your "
            "head when it's non-trivial."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "code": {
                    "type": "string",
                    "description": "The Python code to run. Use print() to produce output.",
                }
            },
            "required": ["code"],
        },
    },
    {
        "name": "view_recent_actions",
        "description": (
            "Show a log of what you've actually done recently — every tool "
            "call, its input, and its result. Use when the user asks what "
            "you've been doing, wants to audit your actions, or asks you "
            "to double-check something you claimed to have done."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "limit": {
                    "type": "integer",
                    "description": "How many recent actions to show. Defaults to 20.",
                }
            },
            "required": [],
        },
    },
    {
        "name": "schedule_task",
        "description": (
            "Schedule a request to run automatically once a day at a "
            "given time (e.g. 'check what's due every morning'). Only "
            "runs if the separate scheduler process is running — mention "
            "that to the user if they haven't set it up. IMPORTANT: a "
            "scheduled task runs unattended with nobody watching, so "
            "confirm_login will never fire from one even if requested — "
            "don't schedule anything that depends on it."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "prompt": {
                    "type": "string",
                    "description": "What to do, phrased exactly as you'd say it in chat.",
                },
                "time_of_day": {
                    "type": "string",
                    "description": "24-hour local time, e.g. '08:00' or '17:30'.",
                },
            },
            "required": ["prompt", "time_of_day"],
        },
    },
    {
        "name": "list_scheduled_tasks",
        "description": "List every scheduled task, its time, and whether it's enabled.",
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "cancel_scheduled_task",
        "description": "Cancel a scheduled task by its id (from list_scheduled_tasks).",
        "input_schema": {
            "type": "object",
            "properties": {
                "task_id": {"type": "string", "description": "The task's id."}
            },
            "required": ["task_id"],
        },
    },
    {
        "name": "research_agent",
        "description": (
            "Hand off a research question to a specialist agent that runs "
            "its own multi-step browsing loop — opens several sources, "
            "cross-checks them, and returns one synthesized answer. Use "
            "for genuinely multi-source questions (comparisons, 'best X "
            "for Y', anything needing more than one page to answer well); "
            "use open_browser directly for a single simple lookup instead."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "question": {
                    "type": "string",
                    "description": "The research question, as a clear standalone question.",
                }
            },
            "required": ["question"],
        },
    },
    {
        "name": "draft_email",
        "description": (
            "Step 1 of 2 for sending an email: creates a real, visible "
            "draft in Mail with the given recipient, subject, and body. "
            "Does NOT send it. Report the exact content back to the user "
            "and ask them to confirm before calling send_email."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "to": {"type": "string", "description": "Recipient email address."},
                "subject": {"type": "string", "description": "Email subject."},
                "body": {"type": "string", "description": "Email body text."},
            },
            "required": ["to", "subject", "body"],
        },
    },
    {
        "name": "send_email",
        "description": (
            "Step 2 of 2: actually sends the draft. Only call this after "
            "draft_email has previewed the same recipient AND the user has "
            "explicitly confirmed in their own words in a later message — "
            "calling it without a genuine prior draft_email for this exact "
            "address will simply fail."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "to": {"type": "string", "description": "Recipient email address, matching draft_email."}
            },
            "required": ["to"],
        },
    },
    {
        "name": "deep_reason",
        "description": (
            "Escalate a genuinely hard or ambiguous question to a slower, "
            "much more thorough reasoning pass — real logic/math/planning "
            "problems, or requests where you're not confident a fast "
            "answer would actually be right. Costs real extra time, so "
            "don't use it for ordinary requests."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "question": {
                    "type": "string",
                    "description": "The question or problem, with enough context to be self-contained.",
                }
            },
            "required": ["question"],
        },
    },
    {
        "name": "note_pattern",
        "description": (
            "Record an observation about how this user communicates or "
            "what they tend to want — call this proactively when you "
            "notice something, don't wait to be asked. These are your own "
            "fallible inferences (shown to you in future conversations as "
            "soft context, not hard rules), used to understand casual/"
            "shorthand phrasing better over time."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "observation": {
                    "type": "string",
                    "description": "The pattern, phrased short and concrete, e.g. \"When the user says 'check the board', they mean list_upcoming.\"",
                }
            },
            "required": ["observation"],
        },
    },
    {
        "name": "list_patterns",
        "description": "List every pattern noticed so far via note_pattern. Use when the user asks what you've picked up on about how they talk, or wants to audit/correct it.",
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "forget_pattern",
        "description": "Remove a previously noted pattern that turned out to be wrong, by matching text.",
        "input_schema": {
            "type": "object",
            "properties": {
                "text": {"type": "string", "description": "Text to match against noted patterns."}
            },
            "required": ["text"],
        },
    },
    {
        "name": "lock_screen",
        "description": "Lock the Mac's screen immediately.",
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "sleep_mac",
        "description": "Put the Mac to sleep immediately.",
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "get_clipboard",
        "description": "Read the current contents of the clipboard.",
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "set_clipboard",
        "description": "Copy text to the clipboard.",
        "input_schema": {
            "type": "object",
            "properties": {
                "text": {"type": "string", "description": "Text to copy."}
            },
            "required": ["text"],
        },
    },
    {
        "name": "set_timer",
        "description": "Set a countdown timer that fires a native notification when it's up. For a one-off countdown ('remind me in 10 minutes') — for a specific future date/time, use add_reminder instead.",
        "input_schema": {
            "type": "object",
            "properties": {
                "minutes": {"type": "number", "description": "How many minutes from now."},
                "label": {"type": "string", "description": "What the notification should say. Defaults to \"Time's up\"."},
            },
            "required": ["minutes"],
        },
    },
    {
        "name": "computer_see",
        "description": "Take a screenshot right now and describe what's currently on screen, in any app.",
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "computer_locate",
        "description": (
            "Find the on-screen pixel location of a described UI element "
            "(a button, field, link, icon, menu item) so you can click it "
            "with computer_click or computer_confirm_action."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "description": {"type": "string", "description": "What to find, e.g. \"the Save button\" or \"the search field\"."}
            },
            "required": ["description"],
        },
    },
    {
        "name": "computer_click",
        "description": (
            "Click at a specific screen location (from computer_locate). "
            "For ordinary navigation/interaction only — NEVER for a click "
            "that sends, pays, deletes, or submits something real; use "
            "computer_confirm_action for those instead."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "x": {"type": "integer"},
                "y": {"type": "integer"},
            },
            "required": ["x", "y"],
        },
    },
    {
        "name": "computer_type",
        "description": (
            "Type text into whatever field currently has focus (click it "
            "first with computer_click). NEVER use this to type a "
            "password, PIN, or other credential — use fill_login/"
            "confirm_login for real logins instead, which never expose "
            "the password in chat."
        ),
        "input_schema": {
            "type": "object",
            "properties": {"text": {"type": "string"}},
            "required": ["text"],
        },
    },
    {
        "name": "computer_press_key",
        "description": (
            "Press a key or key combo (e.g. 'enter', 'tab', 'escape', "
            "'cmd+a', 'cmd+c'). NEVER for a keypress that itself sends/"
            "submits something (e.g. pressing enter to send a message) — "
            "use computer_confirm_action for that instead."
        ),
        "input_schema": {
            "type": "object",
            "properties": {"key": {"type": "string"}},
            "required": ["key"],
        },
    },
    {
        "name": "computer_confirm_action",
        "description": (
            "Executes a click or keypress that sends, pays, deletes, or "
            "submits something real (e.g. clicking 'Buy Now', 'Delete', "
            "'Send', or pressing enter in a compose window). This is a "
            "two-step, human-approved process like confirm_login: first "
            "describe exactly what you're about to do and wait for the "
            "user to explicitly say yes in a later message — never infer "
            "or assume confirmation, and never call this in the same turn "
            "as the description. Provide either x/y (to click) or key (to "
            "press)."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "description": {"type": "string", "description": "Plain description of the action being confirmed, for the audit log."},
                "x": {"type": "integer"},
                "y": {"type": "integer"},
                "key": {"type": "string"},
            },
            "required": ["description"],
        },
    },
    {
        "name": "open_file",
        "description": "Open a local file or folder by path (e.g. one found via search_files) in its default app.",
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Full path to the file or folder."}
            },
            "required": ["path"],
        },
    },
]

# Fail loudly at import time if a tool ever gets added here without also
# being classified in agent/permissions.py, rather than silently leaving a
# gap in the audit/safety picture.
check_full_coverage([tool["name"] for tool in TOOLS])


if __name__ == "__main__":

    test = input("What do you need help with? ")

    response = client.messages.create(
        model="claude-sonnet-5",
        max_tokens=4096,
        system=build_system_prompt(),
        tools=TOOLS,
        messages=[{"role": "user", "content": test}],
    )

    print(response)
