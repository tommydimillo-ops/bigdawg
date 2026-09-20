"""The one chokepoint every file-content read that a model can steer must
go through: refuse to read a file that holds secrets.

Why this exists: reads leave the machine irreversibly. A bad write is
recoverable -- that is the whole point of agent/coding_checkpoint.py -- but
file content that has been read into a prompt has been sent to the model
provider, and the only remedy is rotating every key it touched. Plan-b7
closed the WRITE side (CodingAgent can no longer overwrite `.env`); this
closes the READ side.

Why ONE module rather than a guard at each reader: the plan-b8 enumeration
found four separate model-reachable readers -- CodingAgent's `_read_file`
(agent/agents/coding.py), the registered `read_document` tool and
ResearchAgent's copy of it (both call documents/reader.py's
`read_document`), and the Obsidian vault's `read_note`/`search`. Each
would have needed to remember its own guard, which is exactly the shape
that produced the M10.0 bypasses. tests/test_read_paths_structural.py
re-derives the set of file-reading functions from real source on every run
and fails on any that neither calls this module nor is explicitly
accepted, so a FIFTH reader added later cannot ship ungated by accident.

Matching is on the RESOLVED path (symlinks followed) AND on the path as
given, and either one matching refuses: a symlink named `notes.txt` that
points at `.env` is caught by the resolved form, and a file literally named
`.env` is refused even if it is a symlink to something harmless (stricter,
never looser). Tracked example files (`.env.example`, `.env.sample`,
`.env.template`) are carved out: they are committed, so they hold no secret,
and a guard that refuses a harmless file teaches people to route around it.
The carve-out is by exact basename and is applied per candidate path, so a
symlink called `.env.example` that resolves to `.env` is still refused.

This deliberately does NOT refuse "everything gitignored" -- reading
`logs/` to debug is legitimate; the write-side rule does not transfer.

What this cannot cover, and is not pretending to: code execution.
`run_python` (tools/sandbox_python.py) and CodingAgent's `run_tests` run
model-written code with real read access, which can `open('.env')` without
touching any reader above. That needs a different fix (a sandbox read-deny
and a scrubbed environment) and is tracked explicitly, not hidden -- see
the ACKNOWLEDGED_* table in tests/test_read_paths_structural.py.
"""
import fnmatch
import os
from typing import Optional

# Matched against the lower-cased BASENAME with fnmatch. The plan's list,
# plus the other private-key filenames of the same class as `id_rsa*`
# (a guard that stops id_rsa but not id_ed25519 would be an obvious hole).
_SECRET_BASENAME_PATTERNS = (
    ".env",
    ".env.*",
    "*.pem",
    "*.key",
    "id_rsa*",
    "id_dsa*",
    "id_ecdsa*",
    "id_ed25519*",
    "*.p12",
    "*.keychain",
    "*.keychain-db",
    "credentials*",
    "*_token*",
    "*.secret",
)

# Committed template files, safe by construction. Exact basenames only.
_EXAMPLE_BASENAMES = frozenset({".env.example", ".env.sample", ".env.template"})

# macOS Keychain storage lives under ~/Library/Keychains; anything inside
# it is refused regardless of its filename.
_KEYCHAIN_DIR_MARKER = "/library/keychains/"


def _reason_for(candidate: str) -> Optional[str]:
    lowered = candidate.lower()
    basename = os.path.basename(lowered)
    if basename in _EXAMPLE_BASENAMES:
        return None
    if _KEYCHAIN_DIR_MARKER in lowered.rstrip("/") + "/":
        return "it is inside a macOS Keychain directory"
    for pattern in _SECRET_BASENAME_PATTERNS:
        if fnmatch.fnmatch(basename, pattern):
            return f"its name matches the secret-file pattern '{pattern}'"
    return None


def secret_path_reason(path: str) -> Optional[str]:
    """Why reading `path` must be refused, or None if it is allowed.
    Pure -- no I/O beyond resolving the path, no audit entry (use
    refuse_secret_read for a request that should be refused and logged)."""
    expanded = os.path.expanduser((path or "").strip())
    for candidate in {os.path.abspath(expanded), os.path.realpath(expanded)}:
        reason = _reason_for(candidate)
        if reason is not None:
            return reason
    return None


def refuse_secret_read(path: str, reader: str) -> Optional[str]:
    """The chokepoint. Returns None if the read may proceed, or an error
    string (to be returned to the caller in place of the content) if it
    must not -- in which case the refusal has been audited, and the file
    has not been opened: never a partial read. Call this BEFORE touching
    the file, so a refusal cannot be told apart from a missing file by
    whether the path exists.

    `reader` names the calling reader in the audit entry. The error names
    the path as given and never includes any file content."""
    reason = secret_path_reason(path)
    if reason is None:
        return None
    # Imported here, not at module top: agent.audit -> agent.permissions ->
    # tools.registry, and documents/reader.py (a tool implementation) must
    # be importable while the registry is still being populated.
    from agent.audit import log_action

    log_action("secret_read_refused", {"path": path, "reader": reader}, f"refused: {reason}")
    return (
        f"Error: refusing to read '{path}' -- {reason}. Files that hold secrets are never "
        "read into a prompt, because what is read here leaves the machine and cannot be taken back."
    )
