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
import sys
import time
from contextlib import contextmanager
from typing import Optional

from config.settings import settings

PREVIEW_LENGTH = 120

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
