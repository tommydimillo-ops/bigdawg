"""Alexa entry point -- an Echo Dot as a fourth way into the executor.

Shape, and why it differs from every other entry point: an Alexa skill
gets roughly eight seconds to answer before Amazon kills the request,
which is far less than a real agent turn. So this is deliberately
fire-and-forget. The skill POSTs a task, gets an immediate 202, and says
"on it" out loud; the task then runs to completion here with no one
waiting on the other end. The answer is delivered afterwards over
Telegram (agent/telegram_bridge.py), and the last one is also readable
by voice via /last.

That "no one waiting" property is exactly why source="alexa" is in
agent/autonomy.py's _NON_INTERACTIVE_SOURCES -- a CONFIRM verdict could
never be answered here -- and why it is also in _AMBIENT_VOICE_SOURCES:
a Dot listens to a whole room and will transcribe the television, so it
carries strictly more misfire risk than the menu-bar mic.

SECURITY -- the bearer token is load-bearing. This process is reachable
from the public internet through whatever tunnel forwards to it, and the
POST /task body is an instruction to a full agent on this Mac. Requests
without the exact ALEXA_BRIDGE_TOKEN are rejected before the body is
read or logged. The listener binds to 127.0.0.1 only, so the tunnel is
the single ingress and nothing on the local network reaches it directly.
is_configured() is the single check; without a token the bridge is inert
rather than open.

No new HTTP dependency: the listener is stdlib http.server and outbound
delivery reuses telegram_bridge, matching this project's existing
convention (agent/telegram_bridge.py shells out to curl for the same
reason).
"""
import fcntl
import json
import os
import threading
import time

from agent.observability import log_event, preview
from agent.secrets import get_secret

BRIDGE_TOKEN_SECRET = "ALEXA_BRIDGE_TOKEN"

# Read back by voice through /last, and the durable record of what the
# Dot was last asked to do. Same Application Support directory and the
# same atomic-write convention as telegram_bridge's offset file.
LAST_RESULT_FILE = os.path.expanduser(
    "~/Library/Application Support/CampusPilot/alexa_last_result.json"
)

# Spoken back by Alexa, so it has to fit comfortably in one breath.
# The full text always goes to Telegram regardless.
SPOKEN_RESULT_LIMIT = 600

# One task at a time. Two agent turns racing over the same tools and the
# same vault is not a thing this project's locking assumes, and a Dot
# that misfires twice in a row shouldn't start two of them.
_run_lock = threading.Lock()


def is_configured() -> bool:
    """True only when a bridge token exists. Every entry point checks
    this first; without it the bridge refuses everything rather than
    accepting unauthenticated instructions."""
    return bool(_bridge_token())


def _bridge_token():
    return get_secret(BRIDGE_TOKEN_SECRET)


def verify_token(presented) -> bool:
    """Constant-time comparison against the configured bridge token.

    Returns False when unconfigured, so an absent token can never be
    satisfied by an absent header."""
    expected = _bridge_token()
    if not expected or not presented:
        return False
    presented = str(presented).strip()
    if presented.lower().startswith("bearer "):
        presented = presented[7:].strip()
    # hmac.compare_digest over equal-length byte strings; length itself
    # is not secret here but the comparison shouldn't short-circuit.
    import hmac
    return hmac.compare_digest(presented.encode(), str(expected).encode())


def is_busy() -> bool:
    """Whether a task is currently running. The skill uses this to say
    'still working on the last one' rather than silently queueing."""
    return _run_lock.locked()


def load_last_result() -> dict:
    """The most recent finished task, or a neutral placeholder when there
    isn't one yet or the file is unreadable. Never raises -- a missing or
    corrupt result file means 'nothing to report', not a crash."""
    try:
        with open(LAST_RESULT_FILE) as handle:
            data = json.load(handle)
        if isinstance(data, dict):
            return data
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        pass
    return {"task": None, "result": None, "ok": None, "finished_at": None}


def save_last_result(record: dict) -> None:
    """Persist atomically. Best-effort: a write failure is logged, never
    raised -- losing the read-back copy must not fail a task that already
    ran and already went out over Telegram."""
    try:
        os.makedirs(os.path.dirname(LAST_RESULT_FILE), exist_ok=True)
        tmp = f"{LAST_RESULT_FILE}.tmp"
        with open(tmp, "w") as handle:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            json.dump(record, handle)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, LAST_RESULT_FILE)
    except OSError as error:
        log_event(
            "alexa_result_persist_failed", component="alexa_bridge",
            level="warning", error_type=type(error).__name__,
        )


def spoken_summary(record: dict) -> str:
    """What Alexa reads aloud for /last. Trimmed to one breath; the full
    text already went to Telegram when the task finished."""
    if not record or not record.get("finished_at"):
        return "I haven't finished anything yet."
    text = (record.get("result") or "").strip()
    if not text:
        return "The last task finished, but it didn't say anything."
    if len(text) > SPOKEN_RESULT_LIMIT:
        return text[:SPOKEN_RESULT_LIMIT].rsplit(" ", 1)[0] + "... the rest is in Telegram."
    return text


def run_task(task: str) -> dict:
    """Run one task to completion as source="alexa", then deliver it.

    Blocking and meant to be called on a worker thread -- the HTTP layer
    has already answered 202 by the time this starts. Never raises: the
    agent loop failing is a result to report, not a reason to take the
    listener down with it."""
    started = time.time()
    log_event(
        "alexa_task_started", component="alexa_bridge",
        task_preview=preview(task),
    )

    # Imported here, not at module scope: agent.executor pulls in the
    # whole tool registry (and on this Mac, the screen-control stack).
    # Keeping it lazy means the listener starts, and /health answers,
    # even if that import is broken.
    from agent.executor import execute_task

    try:
        result = execute_task(task, [], source="alexa")
        ok = True
    except Exception as error:  # noqa: BLE001 -- see docstring
        result = f"Something went wrong running that: {type(error).__name__}"
        ok = False
        log_event(
            "alexa_task_failed", component="alexa_bridge", level="error",
            error_type=type(error).__name__,
        )

    record = {
        "task": task,
        "result": result,
        "ok": ok,
        "finished_at": time.time(),
        "duration_seconds": round(time.time() - started, 1),
    }
    save_last_result(record)

    # Delivery is best-effort and deliberately after the result is
    # already persisted: if Telegram is down or unconfigured, the task
    # still happened and /last can still read it back.
    try:
        from agent.telegram_bridge import is_configured as telegram_ready, send_message
        if telegram_ready():
            send_message(f"Alexa task: {task}\n\n{result}")
    except Exception as error:  # noqa: BLE001
        log_event(
            "alexa_delivery_failed", component="alexa_bridge", level="warning",
            error_type=type(error).__name__,
        )

    log_event(
        "alexa_task_completed", component="alexa_bridge",
        ok=ok, duration_seconds=record["duration_seconds"],
        result_preview=preview(result or ""),
    )
    return record


def start_task(task: str) -> bool:
    """Kick a task off on a worker thread. Returns False when one is
    already running -- the caller turns that into speech rather than
    queueing, so a misfired repeat doesn't start a second agent."""
    if not _run_lock.acquire(blocking=False):
        return False

    def _worker():
        try:
            run_task(task)
        finally:
            _run_lock.release()

    threading.Thread(target=_worker, name="alexa-task", daemon=True).start()
    return True
