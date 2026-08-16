"""Optional Obsidian vault access -- a human-readable knowledge layer
separate from agent/memory/ (Jarvis's own structured memory, untouched by
this). See agent/obsidian_vault.py and docs/OBSIDIAN_VAULT.md."""
from agent.obsidian_vault import read_note, search_notes, write_note
from tools.registry import ToolSpec, register

_FOLDER_HINT = (
    "Notes are organized under Memory/, Knowledge/, Projects/, "
    "Conversations/, or Agents/ -- see docs/OBSIDIAN_VAULT.md. "
)


def _handle_read_obsidian_note(tool_input):
    content, error = read_note(tool_input["path"])
    if error:
        return error
    return content


register(ToolSpec(
    name="read_obsidian_note",
    description=(
        "Read a note from the user's Obsidian vault (an optional, "
        "human-readable knowledge layer separate from your own memory). "
        "Only works if a vault is configured -- if not, says so clearly "
        "rather than failing."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "Note path relative to the vault root, e.g. 'Knowledge/python-tips.md' (.md is added automatically if omitted).",
            }
        },
        "required": ["path"],
    },
    permission_level=0,
    handler=_handle_read_obsidian_note,
    parallel_safe=True,
))


def _handle_search_obsidian_vault(tool_input):
    results, error = search_notes(tool_input["query"], limit=tool_input.get("limit", 10))
    if error:
        return error
    if not results:
        return "No matching notes found."
    return "\n\n".join(f"[{r.path}] {r.snippet}" for r in results)


register(ToolSpec(
    name="search_obsidian_vault",
    description=(
        "Search the user's Obsidian vault for notes relevant to a query "
        "(deterministic keyword matching, not semantic search). Use "
        "before creating a new note, to check whether related knowledge "
        "already exists."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "What to search for."},
            "limit": {"type": "integer", "description": "Max results. Defaults to 10."},
        },
        "required": ["query"],
    },
    permission_level=0,
    handler=_handle_search_obsidian_vault,
    parallel_safe=True,
))


def _handle_write_obsidian_note(tool_input):
    success, error = write_note(
        tool_input["path"], tool_input["content"], append=tool_input.get("append", False),
    )
    if not success:
        return f"Could not save note: {error}"
    return f"Saved note: {tool_input['path']}"


register(ToolSpec(
    name="write_obsidian_note",
    description=(
        "Create or update a note in the user's Obsidian vault. Only use "
        "when the user explicitly asks you to save/write/note something "
        "down in Obsidian -- never proactively. " + _FOLDER_HINT +
        "Refuses content that looks like a credential or secret."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "Note path relative to the vault root, e.g. 'Projects/jarvis-notes.md' (.md is added automatically if omitted). Parent folders are created automatically.",
            },
            "content": {"type": "string", "description": "The note content (Markdown)."},
            "append": {
                "type": "boolean",
                "description": "If true, adds to the end of an existing note instead of replacing it. Defaults to false (replace/create).",
            },
        },
        "required": ["path", "content"],
    },
    permission_level=1,
    handler=_handle_write_obsidian_note,
    side_effect=True,
))
