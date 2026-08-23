"""Durable, local, searchable record of what actually happened in a
Jarvis interaction -- Phase 9 M4.1, the first implementation slice. See
docs/GRAPHIFY.md's neighbor doc (this module's own docstring below) and
ARCHITECTURE.md for the full HISTORY-vs-MEMORY boundary rationale.

HISTORY (this module) vs MEMORY (agent/memory/): distinct concerns, not
layers of the same thing. Memory is *distilled, durable, superseding* --
"the user prefers X" replaces "the user prefers Y" because only one can
currently be true. History is *append-only evidence* -- what was
actually said, when, is never superseded by a later, unrelated turn;
each row is independently true forever. Running conversation content
through agent/memory/manager.py's same-subject supersession logic would
be actively wrong for this reason. This module never imports from or
writes to agent/memory/, and vice versa is expected to remain true.

WHAT THIS MODULE OWNS (M4.1 only -- no capture wiring, no ToolSpecs, no
backfill; those are M4.2/M4.3/a later explicit step):
- the history database's location and safe/idempotent initialization
- schema creation and version enforcement (PRAGMA user_version)
- canonical `history_session`/`history_turn` records (the only
  authoritative copies -- see below)
- deterministic, write-time redaction and truncation
  (agent.memory.safety.redact_secrets(), reused rather than forked)
- an external-content FTS5 index kept in sync by SQL triggers, never
  written to directly by application code
- a safe internal search-query builder that never lets caller text
  reach SQLite's FTS5 MATCH syntax unescaped
- SQLite connection/concurrency policy (WAL, busy_timeout, foreign
  keys, secure_delete)

WHAT THIS MODULE DELIBERATELY DOES NOT DO YET: call an LLM, invoke a
ToolSpec, import graphify/graphifyy, touch agent/memory/, or write
anything automatically -- every write in M4.1 happens because a test
(or, later, M4.2's executor wiring) explicitly calls create_session()/
record_turn(). Importing this module is side-effect free: nothing here
runs at import time that creates a file, matching every other
file-backed store in this project (agent/execution_history.py,
agent/usage.py, ...), which likewise only touch disk when their public
functions are actually called.

STORAGE BOUNDARY: one dedicated SQLite database
(~/Library/Application Support/CampusPilot/history.db), never inside
the repo, following this project's existing "one file per concern"
convention (agent/execution_history.py's HISTORY_FILE,
agent/usage.py's USAGE_FILE, ...) -- just SQLite instead of JSON,
because FTS5 needs it and nothing else in this project currently owns
a SQLite database it writes to (agent/personal_context.py's sqlite3
use is read-only against Apple's own Photos database, a different
thing entirely).

CANONICAL-FIRST: `history_session`/`history_turn` are the only
authoritative data. `history_turn_fts` is a derived FTS5 index,
kept in sync purely by AFTER INSERT/UPDATE/DELETE triggers -- never
written to directly, and losing it (a corrupt index, a future
`DROP`+rebuild) never loses real data, only search capability.

CONNECTION POLICY: connection-per-operation (never one shared global
handle -- matches this project's existing load/modify/save-per-call
style everywhere else), stdlib `sqlite3` only, no new dependency.
Every write connection sets `foreign_keys=ON`, `journal_mode=WAL`,
`busy_timeout` (bounded), `synchronous=FULL` (reliability prioritized
over the small performance gain NORMAL/OFF would give -- this project
has no throughput requirement that would justify the corruption-on-
power-loss risk NORMAL/OFF trade in for), and `secure_delete=ON`
(verified empirically, not assumed -- see the module's test suite:
without it, a deleted row's bytes are still recoverable in the raw
file; with it, even without a VACUUM, they are not). Read-only
operations (`history_status`/`search_history`) open the database via a
`file:...?mode=ro` URI -- the same pattern agent/personal_context.py
already established for read-only SQLite access in this project --
which structurally *cannot* create a missing database file (SQLite
raises rather than silently creating one), proving the read-only
surface can never have creation side effects.
"""
import os
import re
import sqlite3
import time
import unicodedata
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import List, Optional

from agent.memory.safety import redact_secrets

HISTORY_DB = os.path.expanduser("~/Library/Application Support/CampusPilot/history.db")

SCHEMA_VERSION = 1

# Conservative, channel-neutral cap, matching agent/openclaw_messaging.py's
# MAX_MESSAGE_LENGTH precedent exactly (same order of magnitude, same
# "reject/mark-truncated, never silently drop" philosophy).
MAX_TURN_LENGTH = 4000
MAX_QUERY_LENGTH = 200
MAX_QUERY_TERMS = 10
MAX_ID_LENGTH = 128
MAX_SOURCE_LENGTH = 32

_VALID_ROLES = frozenset({"user", "assistant"})
# The exact three source values real callers pass to
# agent.executor.execute_task_stream today (verified directly against
# app.py/agent/voice_session.py/agent/scheduler_daemon.py, not assumed).
_VALID_SOURCES = frozenset({"chat", "voice", "scheduled"})

_BUSY_TIMEOUT_MS = 5000
_CONNECT_TIMEOUT_SECONDS = 5

# SQLite's own stable ABI value for SQLITE_BUSY -- part of SQLite's public
# C API surface, never renumbered. Used to distinguish a genuine "another
# connection has this locked" condition from any other OperationalError
# (a real disk I/O failure, for instance), which must never be retried.
_SQLITE_BUSY = 5
_WAL_INIT_RETRY_INTERVAL_SECONDS = 0.05

_MAX_SEARCH_RESULTS = 20
_DEFAULT_SEARCH_RESULTS = 10
_SNIPPET_TOKENS = 32


# --- Errors --------------------------------------------------------------
#
# Distinct types so a caller (and M4.3's future ToolSpec) can tell "the
# store doesn't exist yet" apart from "your input was invalid" apart from
# "something is actually wrong with the database" -- collapsing these
# into one generic exception (or, worse, into a silent empty result)
# would hide a real corruption/lock problem behind what looks like an
# ordinary empty search.

class HistoryStoreError(Exception):
    """Base class for every error this module raises."""


class HistoryUnavailable(HistoryStoreError):
    """No usable database at HISTORY_DB (or the given path) -- distinct
    from a search that legitimately found zero rows."""


class HistorySchemaError(HistoryStoreError):
    """The database's PRAGMA user_version is newer than SCHEMA_VERSION.
    Fails closed -- this module never downgrades or overwrites a schema
    a newer version of this code created."""


class HistoryCorruption(HistoryStoreError):
    """SQLite itself reported corruption (an integrity/malformed-database
    error), not an ordinary empty result."""


class HistoryValidationError(HistoryStoreError):
    """Caller-supplied input (role/source/id/query) failed validation
    before any SQL ran."""


class HistoryBusy(HistoryStoreError):
    """A write could not acquire the database lock within busy_timeout."""


class HistoryUnsupportedRuntime(HistoryStoreError):
    """This SQLite/FTS5 build cannot honor a privacy invariant this module
    requires (currently: FTS5's own 'secure-delete' configuration option,
    which needs SQLite >= 3.42.0). Fails closed -- a history database is
    never created without every layer of its secure-delete protection, so
    an older runtime is a hard incompatibility, not something to silently
    degrade past."""


# --- Records ---------------------------------------------------------------

@dataclass(frozen=True)
class SessionRecord:
    session_id: str
    source: str
    started_at: str
    ended_at: Optional[str]


@dataclass(frozen=True)
class TurnRecord:
    turn_id: int
    session_id: str
    request_id: Optional[str]
    role: str
    created_at: str
    redacted: bool
    truncated: bool


@dataclass(frozen=True)
class SearchResult:
    turn_id: int
    session_id: str
    request_id: Optional[str]
    created_at: str
    source: str
    role: str
    snippet: str
    rank: float
    redacted: bool
    truncated: bool


@dataclass(frozen=True)
class HistoryStatus:
    available: bool
    schema_version: Optional[int] = None
    session_count: Optional[int] = None
    turn_count: Optional[int] = None
    oldest_turn_at: Optional[str] = None
    newest_turn_at: Optional[str] = None
    fts_available: Optional[bool] = None
    journal_mode: Optional[str] = None


# --- Validation ------------------------------------------------------------

def _validate_role(role: str) -> str:
    if role not in _VALID_ROLES:
        raise HistoryValidationError(f"role must be one of {sorted(_VALID_ROLES)}, got {role!r}")
    return role


def _validate_source(source: str) -> str:
    if not isinstance(source, str) or not source or len(source) > MAX_SOURCE_LENGTH:
        raise HistoryValidationError("source must be a non-empty string within the length bound")
    if source not in _VALID_SOURCES:
        raise HistoryValidationError(f"source must be one of {sorted(_VALID_SOURCES)}, got {source!r}")
    return source


def _validate_id(value: Optional[str], field_name: str, *, required: bool) -> Optional[str]:
    if value is None:
        if required:
            raise HistoryValidationError(f"{field_name} is required")
        return None
    if not isinstance(value, str) or not value.strip():
        raise HistoryValidationError(f"{field_name} must be a non-empty string")
    if len(value) > MAX_ID_LENGTH:
        raise HistoryValidationError(f"{field_name} exceeds the maximum length of {MAX_ID_LENGTH}")
    return value


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _redact_and_truncate(content: str) -> tuple:
    """Returns (stored_content, redacted, truncated). Redaction always
    runs first -- the unredacted form is never the thing that gets
    length-bounded or ever reaches sqlite3.execute()."""
    if not isinstance(content, str):
        raise HistoryValidationError("content must be a string")
    redacted_content = redact_secrets(content)
    redacted = redacted_content != content
    if len(redacted_content) > MAX_TURN_LENGTH:
        stored = redacted_content[:MAX_TURN_LENGTH]
        truncated = True
    else:
        stored = redacted_content
        truncated = False
    return stored, redacted, truncated


# --- Safe search-query builder ---------------------------------------------
#
# NO caller text ever reaches FTS5's MATCH syntax unescaped. Every
# extracted term is individually double-quoted before being placed in the
# query string; a double-quoted token is always a literal string to
# FTS5's parser, never re-interpreted as an operator (OR/AND/NOT/NEAR),
# column filter (col:term), wildcard (term*), or exclusion (-term) --
# verified empirically against exactly those categories (see
# tests/test_history_store.py's TestSafeQueryBuilder), not assumed from
# documentation alone. \w+ extraction also means a term can never itself
# contain a literal '"', so no per-term escaping is needed either.

def build_safe_match_query(raw_query: str) -> Optional[str]:
    """Returns a safe FTS5 MATCH expression, or None if `raw_query`
    contains no usable search terms."""
    if not isinstance(raw_query, str):
        return None
    normalized = unicodedata.normalize("NFKC", raw_query)
    # Strip C0/C1 control characters (\x00-\x1f, \x7f-\x9f) before
    # length-bounding, so a control-character-padded string can't be
    # used to smuggle more "real" characters past the cap.
    normalized = "".join(ch for ch in normalized if unicodedata.category(ch) != "Cc")
    normalized = normalized.strip()[:MAX_QUERY_LENGTH]

    terms = re.findall(r"\w+", normalized, re.UNICODE)[:MAX_QUERY_TERMS]
    if not terms:
        return None
    return " ".join(f'"{term}"' for term in terms)


# --- Connections -------------------------------------------------------

def _sqlite_ro_uri(path: str) -> str:
    # SQLite URI filenames use forward slashes and percent-encode
    # special characters; os.path.abspath + a minimal quote covers this
    # project's real deployment target (a user's home directory path).
    from urllib.parse import quote
    return f"file:{quote(os.path.abspath(path))}?mode=ro"


def _create_file_with_owner_only_permissions(path: str) -> None:
    """Creates an empty file at `path` with 0600 permissions BEFORE
    sqlite3 ever opens it, so there is no window where the file exists
    with default (often group/world-readable) permissions. Deliberately
    does not touch the process umask (that would affect every other file
    this process creates concurrently, including from other threads --
    not this file's problem to solve that way). O_EXCL makes creation
    race-safe: if another process/thread wins the race, this simply
    backs off rather than erroring the caller."""
    try:
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_RDWR, 0o600)
    except FileExistsError:
        return
    os.close(fd)
    os.chmod(path, 0o600)  # belt-and-suspenders against a widened umask


def _set_journal_mode_wal(conn: sqlite3.Connection) -> None:
    """PRAGMA journal_mode=WAL's one-time transition -- creating a brand-
    new database's -wal/-shm files the first time anything switches it
    out of SQLite's default rollback-journal mode -- takes its own
    internal exclusive lock that does not reliably honor the connection's
    busy_timeout the way an ordinary statement does. Empirically
    reproduced (Phase 9 Reliability S1.1, following a real intermittent
    CI failure): with 12 threads racing to be the first writer to open a
    fresh database, this exact PRAGMA -- and no other statement in this
    function, verified by isolating each one -- occasionally raised a raw
    SQLITE_BUSY ("database is locked") well inside the busy_timeout
    window already in effect on that connection. Bounded, narrowly-scoped
    retry: only for this one operation, only for SQLITE_BUSY specifically
    (error code 5, checked via sqlite_errorcode -- never a blind `except
    OperationalError`, since a real disk I/O failure must still propagate
    immediately), bounded by the same window the rest of this module's
    busy_timeout already promises callers. Exceeding that window raises
    the same HistoryBusy a caller would see from a locked BEGIN IMMEDIATE
    elsewhere in this module -- "genuinely locked longer than the
    configured deadline" is one meaningful condition regardless of which
    statement hit it."""
    deadline = time.monotonic() + (_BUSY_TIMEOUT_MS / 1000.0)
    while True:
        try:
            conn.execute("PRAGMA journal_mode = WAL")
            return
        except sqlite3.OperationalError as error:
            if getattr(error, "sqlite_errorcode", None) != _SQLITE_BUSY:
                raise
            if time.monotonic() >= deadline:
                raise HistoryBusy("history database is locked by another writer") from error
            time.sleep(_WAL_INIT_RETRY_INTERVAL_SECONDS)


def _connect_writable(db_path: str, *, ensure_schema: bool = True) -> sqlite3.Connection:
    """Every writable connection is guaranteed to have a valid, current
    schema (or to have already failed closed on a corrupt/too-new one)
    before this returns -- callers never need to separately remember to
    initialize first. `ensure_schema=False` exists only for
    initialize_history_store()'s own direct use, to avoid this function
    calling _ensure_schema and then immediately being called again."""
    directory = os.path.dirname(db_path)
    os.makedirs(directory, exist_ok=True)  # same os.makedirs(..., exist_ok=True) convention as every other store; never touches the directory's own mode
    if not os.path.isfile(db_path):
        _create_file_with_owner_only_permissions(db_path)

    conn = sqlite3.connect(db_path, timeout=_CONNECT_TIMEOUT_SECONDS, isolation_level=None)
    conn.execute(f"PRAGMA busy_timeout = {_BUSY_TIMEOUT_MS}")
    conn.execute("PRAGMA foreign_keys = ON")
    _set_journal_mode_wal(conn)
    conn.execute("PRAGMA synchronous = FULL")
    conn.execute("PRAGMA secure_delete = ON")
    if ensure_schema:
        try:
            _ensure_schema(conn)
        except sqlite3.DatabaseError as error:
            conn.close()
            raise HistoryCorruption("history database appears corrupt") from error
        except HistoryStoreError:
            conn.close()
            raise
    return conn


def _connect_readonly(db_path: str, *, busy_timeout_ms: Optional[int] = None) -> sqlite3.Connection:
    """Structurally cannot create db_path -- SQLite's mode=ro URI raises
    rather than creating a missing file, which is how history_status()/
    search_history() prove (not just promise) they never create the
    database. Raises HistoryUnavailable for any failure to open,
    including "does not exist".

    `busy_timeout_ms` defaults to None, which falls back to the module's
    own `_BUSY_TIMEOUT_MS` -- every existing caller (history_status(),
    and search_history() when its own override isn't given) is
    byte-for-byte unaffected. The override exists for a future caller
    (Phase 9 M4.4's proactive retrieval) that must never let a real,
    ordinary conversation turn wait on a slow read. Note this is
    defense-in-depth, not a fix for a reproduced hazard: under this
    store's real WAL journal mode, a read-only connection does NOT wait
    on another connection's open write transaction at all (proven in
    tests/test_history_store.py -- a held BEGIN IMMEDIATE never delays a
    read, with or without this override) -- readers see the
    last-committed snapshot immediately, which is WAL's whole point.
    This parameter guards narrower cases a read connection could still
    plausibly hit (e.g. platform/SQLite-version differences, WAL
    recovery), not ordinary write/read contention."""
    if not os.path.isfile(db_path):
        raise HistoryUnavailable("no history database exists at this location")
    try:
        conn = sqlite3.connect(_sqlite_ro_uri(db_path), uri=True, timeout=_CONNECT_TIMEOUT_SECONDS)
    except sqlite3.OperationalError as error:
        raise HistoryUnavailable("history database could not be opened") from error
    effective_timeout_ms = busy_timeout_ms if busy_timeout_ms is not None else _BUSY_TIMEOUT_MS
    conn.execute(f"PRAGMA busy_timeout = {effective_timeout_ms}")
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


# --- Schema ------------------------------------------------------------

def _create_schema_v1(conn: sqlite3.Connection) -> None:
    conn.execute("""
        CREATE TABLE history_session (
            session_id TEXT PRIMARY KEY,
            source TEXT NOT NULL,
            started_at TEXT NOT NULL,
            ended_at TEXT
        )
    """)
    conn.execute("""
        CREATE TABLE history_turn (
            turn_id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT NOT NULL,
            request_id TEXT,
            role TEXT NOT NULL,
            content TEXT NOT NULL,
            created_at TEXT NOT NULL,
            redacted INTEGER NOT NULL,
            truncated INTEGER NOT NULL,
            FOREIGN KEY (session_id) REFERENCES history_session(session_id)
        )
    """)
    conn.execute("CREATE INDEX idx_turn_session_id ON history_turn(session_id)")
    conn.execute("CREATE INDEX idx_turn_request_id ON history_turn(request_id)")
    conn.execute("CREATE INDEX idx_turn_created_at ON history_turn(created_at)")
    conn.execute("CREATE INDEX idx_turn_role ON history_turn(role)")
    # Idempotency for real (non-backfilled) turns: at most one row per
    # (request_id, role) -- lets one user turn and one assistant turn
    # share a request_id without colliding, while NULL request_id rows
    # (backfilled/legacy data) are never deduplicated against each other.
    conn.execute("""
        CREATE UNIQUE INDEX idx_turn_request_role
        ON history_turn(request_id, role) WHERE request_id IS NOT NULL
    """)

    conn.execute("""
        CREATE VIRTUAL TABLE history_turn_fts USING fts5(
            content, content='history_turn', content_rowid='turn_id',
            tokenize='porter unicode61'
        )
    """)
    # Two independent secure-delete layers are both required, not one or
    # the other: the core `PRAGMA secure_delete=ON` set on every write
    # connection (see _connect_writable) only covers ordinary SQLite
    # table storage -- it does not by itself guarantee an FTS5 index's
    # own b-tree segments stop retaining old term data after a logical
    # delete/update. FTS5's 'secure-delete' config (added in SQLite
    # 3.42.0) is the layer that covers the index itself, and unlike the
    # core pragma it is a property of the table (persisted in this
    # table's own `_config` shadow table), not the connection -- set
    # once here, at schema creation, it survives every future reopen.
    # Real feature probing (attempt the command, handle failure) rather
    # than a version-string comparison: an OperationalError here means
    # this SQLite build's FTS5 doesn't recognize the config key at all
    # (verified empirically -- an unrecognized FTS5 special command
    # raises exactly this), which fails the whole schema-init
    # transaction closed rather than silently shipping a history
    # database whose FTS index lacks the invariant.
    try:
        conn.execute("INSERT INTO history_turn_fts(history_turn_fts, rank) VALUES ('secure-delete', 1)")
    except sqlite3.OperationalError as error:
        raise HistoryUnsupportedRuntime(
            "this SQLite build's FTS5 does not support the 'secure-delete' "
            "configuration option (requires SQLite >= 3.42.0) -- refusing "
            "to create a history database whose FTS index cannot honor "
            "the secure-delete privacy invariant"
        ) from error
    conn.execute("""
        CREATE TRIGGER history_turn_ai AFTER INSERT ON history_turn BEGIN
            INSERT INTO history_turn_fts(rowid, content) VALUES (new.turn_id, new.content);
        END
    """)
    conn.execute("""
        CREATE TRIGGER history_turn_ad AFTER DELETE ON history_turn BEGIN
            INSERT INTO history_turn_fts(history_turn_fts, rowid, content)
            VALUES ('delete', old.turn_id, old.content);
        END
    """)
    conn.execute("""
        CREATE TRIGGER history_turn_au AFTER UPDATE ON history_turn BEGIN
            INSERT INTO history_turn_fts(history_turn_fts, rowid, content)
            VALUES ('delete', old.turn_id, old.content);
            INSERT INTO history_turn_fts(rowid, content) VALUES (new.turn_id, new.content);
        END
    """)


def _begin_immediate(conn: sqlite3.Connection) -> None:
    """A lock that's still held after the full busy_timeout window
    surfaces as a distinct HistoryBusy, not a raw sqlite3.OperationalError
    or (worse) a generic HistoryStoreError indistinguishable from a real
    validation/corruption problem."""
    try:
        conn.execute("BEGIN IMMEDIATE")
    except sqlite3.OperationalError as error:
        raise HistoryBusy("history database is locked by another writer") from error


def _ensure_schema(conn: sqlite3.Connection) -> None:
    """Idempotent, concurrency-safe schema bootstrap. BEGIN IMMEDIATE
    takes the write lock up front, so two processes racing to initialize
    the same brand-new database serialize instead of both trying to
    CREATE TABLE at once -- the loser simply finds the schema already
    there once its own BEGIN IMMEDIATE is granted. Requires
    isolation_level=None (autocommit) on the connection so this function
    controls the transaction boundary explicitly rather than fighting
    Python sqlite3's own implicit-transaction heuristics."""
    _begin_immediate(conn)
    try:
        current_version = conn.execute("PRAGMA user_version").fetchone()[0]
        if current_version == 0:
            _create_schema_v1(conn)
            # PRAGMA does not support '?' parameter binding; SCHEMA_VERSION
            # is a fixed internal int constant, never caller-supplied, so
            # this f-string carries no injection risk.
            conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
        elif current_version == SCHEMA_VERSION:
            pass
        elif current_version > SCHEMA_VERSION:
            raise HistorySchemaError(
                f"history database schema version {current_version} is newer than "
                f"this code supports ({SCHEMA_VERSION}) -- refusing to touch it"
            )
        else:
            # No older supported version exists yet in M4.1 (only v1 has
            # ever shipped) -- a real migration path belongs here once a
            # v2 exists, never a silent best-effort upgrade attempt.
            raise HistorySchemaError(
                f"history database schema version {current_version} has no known migration to {SCHEMA_VERSION}"
            )
    except Exception:
        conn.execute("ROLLBACK")
        raise
    else:
        conn.execute("COMMIT")


def initialize_history_store(db_path: Optional[str] = None) -> None:
    """The only function callers need to invoke explicitly to create a
    database file that didn't already exist -- though every write
    function below also self-initializes via _connect_writable, so this
    is mainly useful for a caller that wants to force creation/
    validation up front without also writing a row. Safe to call
    repeatedly/concurrently."""
    db_path = db_path if db_path is not None else HISTORY_DB
    _connect_writable(db_path).close()


# --- Sessions ------------------------------------------------------------

def create_session(source: str, session_id: Optional[str] = None, db_path: Optional[str] = None) -> SessionRecord:
    """Idempotent when `session_id` is explicitly supplied and already
    exists WITH a matching source -- returns the existing record rather
    than creating a duplicate or silently overwriting it. A conflicting
    source for an existing session_id is a validation error, never a
    silent overwrite."""
    db_path = db_path if db_path is not None else HISTORY_DB
    source = _validate_source(source)
    if session_id is not None:
        session_id = _validate_id(session_id, "session_id", required=True)
    else:
        session_id = uuid.uuid4().hex

    conn = _connect_writable(db_path)
    try:
        _begin_immediate(conn)
        try:
            existing = conn.execute(
                "SELECT session_id, source, started_at, ended_at FROM history_session WHERE session_id = ?",
                (session_id,),
            ).fetchone()
            if existing is not None:
                if existing[1] != source:
                    raise HistoryValidationError(
                        f"session {session_id!r} already exists with source {existing[1]!r}, "
                        f"refusing to silently change it to {source!r}"
                    )
                conn.execute("COMMIT")
                return SessionRecord(session_id=existing[0], source=existing[1], started_at=existing[2], ended_at=existing[3])

            started_at = _utc_now_iso()
            conn.execute(
                "INSERT INTO history_session (session_id, source, started_at, ended_at) VALUES (?, ?, ?, NULL)",
                (session_id, source, started_at),
            )
            conn.execute("COMMIT")
            return SessionRecord(session_id=session_id, source=source, started_at=started_at, ended_at=None)
        except Exception:
            conn.execute("ROLLBACK")
            raise
    finally:
        conn.close()


def close_session(session_id: str, db_path: Optional[str] = None) -> bool:
    """Marks a session's ended_at. Returns False if the session doesn't
    exist rather than raising -- closing an already-closed or unknown
    session is not exceptional the way a malformed input is."""
    db_path = db_path if db_path is not None else HISTORY_DB
    session_id = _validate_id(session_id, "session_id", required=True)
    conn = _connect_writable(db_path)
    try:
        _begin_immediate(conn)
        try:
            cur = conn.execute(
                "UPDATE history_session SET ended_at = ? WHERE session_id = ? AND ended_at IS NULL",
                (_utc_now_iso(), session_id),
            )
            found = cur.rowcount > 0
            conn.execute("COMMIT")
            return found
        except Exception:
            conn.execute("ROLLBACK")
            raise
    finally:
        conn.close()


# --- Turns -----------------------------------------------------------------

def record_turn(
    session_id: str, role: str, content: str, request_id: Optional[str] = None, db_path: Optional[str] = None,
) -> TurnRecord:
    """Redacts, then bounds, then persists -- in that order, always. The
    unredacted form of `content` never reaches sqlite3.execute(). If
    (request_id, role) was already recorded (request_id not None), this
    returns the EXISTING row's metadata rather than creating a second
    one -- see the idx_turn_request_role partial unique index."""
    db_path = db_path if db_path is not None else HISTORY_DB
    session_id = _validate_id(session_id, "session_id", required=True)
    role = _validate_role(role)
    request_id = _validate_id(request_id, "request_id", required=False)
    stored_content, redacted, truncated = _redact_and_truncate(content)

    conn = _connect_writable(db_path)
    try:
        _begin_immediate(conn)
        try:
            session_exists = conn.execute(
                "SELECT 1 FROM history_session WHERE session_id = ?", (session_id,),
            ).fetchone()
            if session_exists is None:
                raise HistoryValidationError(f"no such session: {session_id!r} -- call create_session() first")

            created_at = _utc_now_iso()
            cur = conn.execute(
                """
                INSERT INTO history_turn (session_id, request_id, role, content, created_at, redacted, truncated)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(request_id, role) WHERE request_id IS NOT NULL DO NOTHING
                """,
                (session_id, request_id, role, stored_content, created_at, int(redacted), int(truncated)),
            )
            if cur.rowcount == 0 and request_id is not None:
                # Duplicate (request_id, role) -- identify and return the
                # row that already exists rather than silently no-op-ing
                # with fabricated metadata.
                existing = conn.execute(
                    """
                    SELECT turn_id, session_id, request_id, role, created_at, redacted, truncated
                    FROM history_turn WHERE request_id = ? AND role = ?
                    """,
                    (request_id, role),
                ).fetchone()
                conn.execute("COMMIT")
                return TurnRecord(
                    turn_id=existing[0], session_id=existing[1], request_id=existing[2], role=existing[3],
                    created_at=existing[4], redacted=bool(existing[5]), truncated=bool(existing[6]),
                )

            turn_id = cur.lastrowid
            conn.execute("COMMIT")
            return TurnRecord(
                turn_id=turn_id, session_id=session_id, request_id=request_id, role=role,
                created_at=created_at, redacted=redacted, truncated=truncated,
            )
        except sqlite3.IntegrityError as error:
            conn.execute("ROLLBACK")
            raise HistoryValidationError(f"could not record turn: {error}") from error
        except Exception:
            conn.execute("ROLLBACK")
            raise
    finally:
        conn.close()


# --- Status ------------------------------------------------------------

def history_status(db_path: Optional[str] = None) -> HistoryStatus:
    """Read-only, never creates db_path (see _connect_readonly)."""
    db_path = db_path if db_path is not None else HISTORY_DB
    try:
        conn = _connect_readonly(db_path)
    except HistoryUnavailable:
        return HistoryStatus(available=False)

    try:
        try:
            schema_version = conn.execute("PRAGMA user_version").fetchone()[0]
        except sqlite3.DatabaseError as error:
            raise HistoryCorruption("history database appears corrupt") from error

        if schema_version > SCHEMA_VERSION:
            raise HistorySchemaError(
                f"history database schema version {schema_version} is newer than this code supports ({SCHEMA_VERSION})"
            )
        if schema_version != SCHEMA_VERSION:
            # 0 (never initialized) or an unrecognized older version --
            # not "corrupt", just not a usable v1 database yet.
            return HistoryStatus(available=False, schema_version=schema_version or None)

        session_count = conn.execute("SELECT COUNT(*) FROM history_session").fetchone()[0]
        turn_count, oldest, newest = conn.execute(
            "SELECT COUNT(*), MIN(created_at), MAX(created_at) FROM history_turn"
        ).fetchone()
        journal_mode = conn.execute("PRAGMA journal_mode").fetchone()[0]

        fts_available = True
        try:
            conn.execute("SELECT count(*) FROM history_turn_fts").fetchone()
        except sqlite3.OperationalError:
            fts_available = False

        return HistoryStatus(
            available=True,
            schema_version=schema_version,
            session_count=session_count,
            turn_count=turn_count,
            oldest_turn_at=oldest,
            newest_turn_at=newest,
            fts_available=fts_available,
            journal_mode=journal_mode,
        )
    finally:
        conn.close()


# --- Search ------------------------------------------------------------

def search_history(
    query: str,
    source: Optional[str] = None,
    role: Optional[str] = None,
    session_id: Optional[str] = None,
    max_results: int = _DEFAULT_SEARCH_RESULTS,
    db_path: Optional[str] = None,
    busy_timeout_ms: Optional[int] = None,
) -> List[SearchResult]:
    """Read-only, never creates db_path. Raises HistoryUnavailable if no
    usable database exists -- distinct from a real, empty result set.

    `busy_timeout_ms` defaults to None (the module's normal
    `_BUSY_TIMEOUT_MS`) -- see `_connect_readonly()`'s own docstring.
    Every existing caller that doesn't pass this explicitly, including
    M4.3's `search_conversation_history` tool, is unaffected."""
    db_path = db_path if db_path is not None else HISTORY_DB
    if source is not None:
        source = _validate_source(source)
    if role is not None:
        role = _validate_role(role)
    if session_id is not None:
        session_id = _validate_id(session_id, "session_id", required=True)
    max_results = max(1, min(int(max_results), _MAX_SEARCH_RESULTS))

    match_query = build_safe_match_query(query)
    if match_query is None:
        return []

    conn = _connect_readonly(db_path, busy_timeout_ms=busy_timeout_ms)
    try:
        schema_version = conn.execute("PRAGMA user_version").fetchone()[0]
        if schema_version > SCHEMA_VERSION:
            raise HistorySchemaError(
                f"history database schema version {schema_version} is newer than this code supports ({SCHEMA_VERSION})"
            )
        if schema_version != SCHEMA_VERSION:
            raise HistoryUnavailable("history database is not initialized")

        sql = """
            SELECT
                history_turn.turn_id, history_turn.session_id, history_turn.request_id,
                history_turn.created_at, history_session.source, history_turn.role,
                snippet(history_turn_fts, 0, '[', ']', '...', ?) AS snip,
                bm25(history_turn_fts) AS rank,
                history_turn.redacted, history_turn.truncated
            FROM history_turn_fts
            JOIN history_turn ON history_turn.turn_id = history_turn_fts.rowid
            JOIN history_session ON history_session.session_id = history_turn.session_id
            WHERE history_turn_fts MATCH ?
        """
        params: list = [_SNIPPET_TOKENS, match_query]
        if source is not None:
            sql += " AND history_session.source = ?"
            params.append(source)
        if role is not None:
            sql += " AND history_turn.role = ?"
            params.append(role)
        if session_id is not None:
            sql += " AND history_turn.session_id = ?"
            params.append(session_id)
        # bm25() is the primary ordering key; it returns more-negative
        # values for better matches, so plain ascending order is correct
        # -- not naturally a normalized 0-1 score, so no weighted
        # combination with recency is invented here (M4A's speculative
        # 0.7*BM25 + 0.3*recency formula is explicitly deferred). Ties in
        # rank fall back to created_at DESC as a deterministic recency
        # tie-breaker only, never as a second ranking signal.
        sql += " ORDER BY rank ASC, history_turn.created_at DESC LIMIT ?"
        params.append(max_results)

        try:
            rows = conn.execute(sql, params).fetchall()
        except sqlite3.OperationalError as error:
            raise HistoryCorruption("history search index appears unavailable") from error

        return [
            SearchResult(
                turn_id=row[0], session_id=row[1], request_id=row[2], created_at=row[3],
                source=row[4], role=row[5], snippet=row[6], rank=row[7],
                redacted=bool(row[8]), truncated=bool(row[9]),
            )
            for row in rows
        ]
    finally:
        conn.close()
