"""Structured application-diagnostics logging -- separate from
agent/audit.py's security/action audit trail on purpose. audit.py answers
"what did Jarvis actually do" (a record for the user to review: which
tool, what input, what result). This module answers "what is the system
doing internally, and where did something go wrong" (for debugging,
correlated by request_id).

Every line is one JSON object to stderr, so it's greppable/parseable
without needing a log aggregator, and doesn't interleave with anything a
UI might print to stdout.

NEVER log API keys, passwords, tokens, cookies, or other raw secrets --
nothing in this codebase currently would (no call site here has access to
raw secret values in the first place; agent/secrets.py's get_secret() is
never called anywhere near a log_event call), but it's still the reason
free-form `**fields` is a deliberate risk: callers must pass short,
reviewed values, never a raw object or full payload. User content is
capped to a short preview (_preview) for the same reason full
conversations shouldn't end up sitting in a diagnostics log by default.
"""
import json
import logging
import os
import sys
import time
from contextlib import contextmanager
from typing import List, Optional

from config.settings import settings

PREVIEW_LENGTH = 120

# M4.5: the one place in this codebase that redirects log_event's stderr
# output to a real, durable file -- ui/menu_bar.py, when launched via the
# actual .app bundle (its __CFBundleIdentifier check). Streamlit (app.py)
# and agent/scheduler_daemon.py do not redirect stderr anywhere durable,
# so events logged from either path are invisible to events_since()
# below; that is a real architectural gap this module cannot paper over,
# not something a smarter read function could fix. See ROADMAP.md's
# M4.5 entry.
MENUBAR_LOG_FILE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "logs", "menubar.err.log",
)

_logger = logging.getLogger("jarvis")
_logger.setLevel(logging.DEBUG if settings.debug else logging.INFO)
if not _logger.handlers:
    _handler = logging.StreamHandler(sys.stderr)
    _handler.setFormatter(logging.Formatter("%(message)s"))
    _logger.addHandler(_handler)
    _logger.propagate = False


def preview(value, limit: int = PREVIEW_LENGTH) -> str:
    text = str(value)
    return text if len(text) <= limit else text[:limit] + "…"


def log_event(
    event: str,
    request_id: Optional[str] = None,
    component: Optional[str] = None,
    level: str = "info",
    duration: Optional[float] = None,
    **fields,
) -> None:
    """Emits one structured log line. `duration` is in seconds and gets
    recorded as duration_ms. `fields` are short, caller-reviewed extra
    details -- never pass a raw secret, full conversation, or large
    payload here; pass a preview() of it instead."""

    entry = {"timestamp": time.time(), "level": level, "event": event}
    if request_id is not None:
        entry["request_id"] = request_id
    if component is not None:
        entry["component"] = component
    if duration is not None:
        entry["duration_ms"] = round(duration * 1000, 1)
    entry.update(fields)

    log_fn = getattr(_logger, level, _logger.info)
    log_fn(json.dumps(entry, default=str))


@contextmanager
def timed_event(event: str, request_id: Optional[str] = None, component: Optional[str] = None, **fields):
    """Logs `<event>_started`, then either `<event>_completed` or
    `<event>_failed` (with duration_ms and, on failure, the exception type
    -- not its full message, which could include user content) around the
    wrapped block. Re-raises whatever the block raised, unchanged."""

    start = time.time()
    log_event(f"{event}_started", request_id=request_id, component=component, **fields)
    try:
        yield
    except Exception as error:
        log_event(
            f"{event}_failed",
            request_id=request_id,
            component=component,
            level="error",
            duration=time.time() - start,
            error_type=type(error).__name__,
        )
        raise
    else:
        log_event(
            f"{event}_completed",
            request_id=request_id,
            component=component,
            duration=time.time() - start,
        )


def events_since(cutoff_timestamp: float, event: Optional[str] = None, log_path: Optional[str] = None) -> Optional[List[dict]]:
    """M4.5: read-only query over this module's own persisted output --
    mirrors agent.usage.get_since()'s shape (a time-window filter over
    parsed records) applied to log_event's JSON lines instead of
    UsageRecord. Returns None if the log can't be opened at all, distinct
    from a real empty window with zero matching events -- same
    None-vs-empty-list convention agent.usage.cost_since() uses for
    "can't tell" vs "genuinely zero," so a caller like the menu-bar
    readout can fail safely by omitting the figure rather than ever
    showing a wrong-looking "zero."

    `log_path` defaults to None and reads MENUBAR_LOG_FILE inside this
    function body, not as the default value itself -- a default bound at
    definition time would not pick up tests/_safety.py's later redirect
    (see CLAUDE.md's "How to test" section for the exact class of bug
    this pattern avoids; agent/history_store.py and
    agent/personal_context.py both had it for real once).

    Never mutates or rotates the log -- opens for reading only. A
    malformed or partial line (e.g. a write caught mid-append) is
    skipped, never allowed to break the whole read; this file is a
    live-appended log, not an atomically-replaced document like
    usage_history.json, so tolerating a torn line is the correct
    default, not defensive overkill."""
    if log_path is None:
        log_path = MENUBAR_LOG_FILE

    try:
        with open(log_path) as file:
            lines = file.readlines()
    except OSError:
        return None

    matches = []
    for line in lines:
        try:
            record = json.loads(line)
        except (json.JSONDecodeError, ValueError):
            continue
        if not isinstance(record, dict):
            continue
        timestamp = record.get("timestamp")
        if not isinstance(timestamp, (int, float)) or timestamp < cutoff_timestamp:
            continue
        if event is not None and record.get("event") != event:
            continue
        matches.append(record)
    return matches
