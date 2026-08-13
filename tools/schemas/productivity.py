"""Reminders/Calendar/Notes/file lookup tools."""
from documents.reader import read_document
from tools.calendar import add_calendar_event
from tools.files import open_file, search_files
from tools.notes import create_note
from tools.reminders import add_reminder
from tools.registry import ToolSpec, register
from tools.agenda import list_upcoming

register(ToolSpec(
    name="add_reminder",
    description=(
        "Add a reminder to the macOS Reminders app for an assignment, "
        "deadline, or task. Use when the user asks to be reminded of "
        "something or wants to track a due date."
    ),
    input_schema={
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
    permission_level=1,
    handler=lambda ti: add_reminder(ti["title"], ti.get("due_date")),
    side_effect=True,
))

register(ToolSpec(
    name="add_calendar_event",
    description=(
        "Add a timed event (a class, exam, meeting, appointment) to the "
        "macOS Calendar app. Use this instead of add_reminder when the "
        "user gives a specific date/time something happens, not just a "
        "deadline."
    ),
    input_schema={
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
    permission_level=1,
    handler=lambda ti: add_calendar_event(ti["title"], ti["start_date"], ti.get("end_date")),
    side_effect=True,
))

register(ToolSpec(
    name="list_upcoming",
    description=(
        "Check what's due or scheduled soon, across both Reminders and "
        "Calendar. Use when the user asks what's coming up, what's due, "
        "or wants their agenda."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "days": {
                "type": "integer",
                "description": "How many days ahead to check. Defaults to 7.",
            }
        },
        "required": [],
    },
    permission_level=0,
    handler=lambda ti: list_upcoming(ti.get("days", 7)),
    parallel_safe=True,
))

register(ToolSpec(
    name="create_note",
    description=(
        "Save a longer or freeform note to the macOS Notes app. Use for "
        "actual notes (lecture notes, an essay outline, a saved "
        "explanation) rather than short facts — use remember_fact for "
        "those instead."
    ),
    input_schema={
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
    permission_level=1,
    handler=lambda ti: create_note(ti["title"], ti.get("body", "")),
    side_effect=True,
))

register(ToolSpec(
    name="read_document",
    description=(
        "Read the text content of a local file so you can summarize it "
        "or answer questions about it. Supports PDF, .txt, and .md "
        "files. Give the full file path, e.g. "
        "'/Users/tommy/Downloads/syllabus.pdf'."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "file_path": {
                "type": "string",
                "description": "Absolute or ~-relative path to the document.",
            }
        },
        "required": ["file_path"],
    },
    permission_level=0,
    handler=lambda ti: read_document(ti["file_path"]),
    parallel_safe=True,
))

register(ToolSpec(
    name="search_files",
    description=(
        "Search the user's home folder for files matching a name or "
        "keyword using Spotlight. Use when the user asks to find a "
        "file, document, or folder by name."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "Filename or keyword to search for.",
            }
        },
        "required": ["query"],
    },
    permission_level=0,
    handler=lambda ti: search_files(ti["query"]),
    parallel_safe=True,
))

register(ToolSpec(
    name="open_file",
    description="Open a local file or folder by path (e.g. one found via search_files) in its default app.",
    input_schema={
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Full path to the file or folder."}
        },
        "required": ["path"],
    },
    permission_level=1,
    handler=lambda ti: open_file(ti["path"]),
))
