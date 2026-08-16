"""Obsidian vault integration -- an optional, human-readable knowledge
layer Jarvis can read from, write to, and search, separate from
agent/memory/ (Jarvis's own structured, typed memory store, untouched by
this module). A vault is nothing more than a directory of Markdown files
(plus an .obsidian/ config folder Obsidian itself manages) -- there is no
local Obsidian API or server involved; this reads and writes vault files
directly on disk, the same way Obsidian's own desktop app does.

Deliberately optional and fail-soft everywhere: is_available() is the one
gate every other function checks first, so a missing/unconfigured vault
degrades to a clear "not available" result rather than raising -- Jarvis
must keep working with Obsidian never installed at all (see
config/settings.py's obsidian_vault_path and _resolve_vault_root() below
for how the path is found).

Modular on purpose (agent/agents/base.py's Agent-wrapping-a-plain-function
pattern -- see agent/agents/memory.py for the precedent): every function
here is called directly by tools/schemas/obsidian.py today, but nothing
about that call shape would need to change if a future ObsidianAgent
wrapped these same functions the way MemoryAgent already wraps
agent/memory_agent.py.

See docs/OBSIDIAN_VAULT.md for the recommended vault folder structure --
this module doesn't enforce it, only documents/nudges toward it, since
it's a human organizing convention, not a technical constraint.
"""
import os
import re
from dataclasses import dataclass
from typing import List, Optional, Tuple

from agent.memory.safety import is_safe_to_remember
from config.settings import settings

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Convenience default matching the vault already sitting at the project
# root in normal use -- never a hardcoded personal absolute path.
# settings.obsidian_vault_path (OBSIDIAN_VAULT_PATH) always wins when set.
_DEFAULT_VAULT_DIR = os.path.join(_PROJECT_ROOT, "JarvisVault")

_STOPWORDS = {
    "i", "a", "an", "the", "is", "are", "was", "were", "to", "of", "in",
    "on", "for", "and", "or", "my", "me", "it", "that", "this", "be",
    "with", "as", "at", "by", "from",
}

_SNIPPET_LENGTH = 200


@dataclass(frozen=True)
class SearchResult:
    path: str
    snippet: str


def _significant_words(text: str) -> set:
    words = re.findall(r"[a-z0-9']+", text.lower())
    return {w for w in words if w not in _STOPWORDS and len(w) > 1}


def _resolve_vault_root() -> Optional[str]:
    """settings.obsidian_vault_path first (an explicit choice always
    wins); otherwise the project-relative ./JarvisVault convenience
    default if it already exists on disk. Returns None (vault not
    available) if neither resolves to a real directory -- callers treat
    that as "Obsidian integration disabled," not an error."""
    configured = settings.obsidian_vault_path
    if configured and os.path.isdir(configured):
        return configured
    if os.path.isdir(_DEFAULT_VAULT_DIR):
        return _DEFAULT_VAULT_DIR
    return None


def is_available() -> bool:
    return _resolve_vault_root() is not None


def _normalize_relative_path(relative_path: str) -> str:
    relative_path = relative_path.strip().lstrip("/")
    if not relative_path.lower().endswith(".md"):
        relative_path += ".md"
    return relative_path


def _resolve_note_path(vault_root: str, relative_path: str) -> Optional[str]:
    """Resolves `relative_path` against `vault_root` and refuses anything
    that would escape it (a `../` traversal, an absolute path elsewhere)
    -- returns None for a rejected path rather than raising, so callers
    turn it into the same kind of clear error message as any other
    failure here rather than a crash."""
    normalized = _normalize_relative_path(relative_path)
    candidate = os.path.realpath(os.path.join(vault_root, normalized))
    vault_real = os.path.realpath(vault_root)
    if candidate != vault_real and not candidate.startswith(vault_real + os.sep):
        return None
    return candidate


def read_note(relative_path: str) -> Tuple[Optional[str], Optional[str]]:
    """Returns (content, None) on success, or (None, error). A missing
    vault, a path that escapes the vault, a missing note, and an
    unreadable/undecodable file are each a distinct, clear error message
    rather than an exception."""
    vault_root = _resolve_vault_root()
    if vault_root is None:
        return None, "Obsidian vault is not configured or not available."

    resolved = _resolve_note_path(vault_root, relative_path)
    if resolved is None:
        return None, f"'{relative_path}' is outside the vault."

    if not os.path.isfile(resolved):
        return None, f"No note found at '{relative_path}'."

    try:
        with open(resolved, "r", encoding="utf-8") as file:
            return file.read(), None
    except (OSError, UnicodeDecodeError) as error:
        return None, f"Could not read '{relative_path}': {error}"


def write_note(relative_path: str, content: str, append: bool = False) -> Tuple[bool, Optional[str]]:
    """Creates or updates a note. `append=True` adds to the end of an
    existing note (creating it if missing) instead of replacing its
    content. Every write is checked against agent.memory.safety's
    credential/injection/destructive-action filter first -- reused, not
    reimplemented, so a vault note is never a second, weaker place to
    accidentally persist something agent/memory/ would have refused."""
    vault_root = _resolve_vault_root()
    if vault_root is None:
        return False, "Obsidian vault is not configured or not available."

    safe, reason = is_safe_to_remember(content)
    if not safe:
        return False, f"Refused to write: {reason}"

    resolved = _resolve_note_path(vault_root, relative_path)
    if resolved is None:
        return False, f"'{relative_path}' is outside the vault."

    try:
        os.makedirs(os.path.dirname(resolved), exist_ok=True)

        if append and os.path.isfile(resolved):
            with open(resolved, "r", encoding="utf-8") as file:
                existing = file.read()
            new_content = existing.rstrip("\n") + "\n\n" + content.strip() + "\n"
        else:
            new_content = content if content.endswith("\n") else content + "\n"

        tmp_file = f"{resolved}.tmp"
        with open(tmp_file, "w", encoding="utf-8") as file:
            file.write(new_content)
        os.replace(tmp_file, resolved)
    except OSError as error:
        return False, f"Could not write '{relative_path}': {error}"

    return True, None


def search_notes(query: str, limit: int = 10) -> Tuple[List[SearchResult], Optional[str]]:
    """Deterministic keyword-overlap search across every .md file in the
    vault (skips .obsidian/ and any other dotdirs) -- no embeddings or
    vector index, consistent with agent/memory/'s own retrieval
    philosophy, kept as a small self-contained scorer here rather than
    reaching into agent.memory.manager's private one."""
    vault_root = _resolve_vault_root()
    if vault_root is None:
        return [], "Obsidian vault is not configured or not available."

    query_words = _significant_words(query)
    if not query_words:
        return [], None

    scored: List[Tuple[int, SearchResult]] = []
    for dirpath, dirnames, filenames in os.walk(vault_root):
        dirnames[:] = [d for d in dirnames if not d.startswith(".")]
        for filename in filenames:
            if not filename.lower().endswith(".md"):
                continue
            full_path = os.path.join(dirpath, filename)
            try:
                with open(full_path, "r", encoding="utf-8") as file:
                    text = file.read()
            except (OSError, UnicodeDecodeError):
                continue

            overlap = len(query_words & _significant_words(text))
            if overlap == 0:
                continue

            relative = os.path.relpath(full_path, vault_root)
            snippet = " ".join(text.strip().split())[:_SNIPPET_LENGTH]
            scored.append((overlap, SearchResult(path=relative, snippet=snippet)))

    scored.sort(key=lambda pair: pair[0], reverse=True)
    return [result for _, result in scored[:limit]], None
