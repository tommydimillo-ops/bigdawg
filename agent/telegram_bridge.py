"""Direct Telegram Bot API bridge -- NOT routed through OpenClaw.

Talks straight to api.telegram.org with a bot token
(agent/secrets.py's TELEGRAM_BOT_TOKEN). Two-way:

- Outbound: send_message() -- backs the send_telegram_message tool
  (tools/schemas/telegram.py) and is also called directly by the inbound
  daemon to deliver replies.
- Inbound: get_updates() long-polls; agent/telegram_daemon.py owns the
  loop that feeds owner messages into agent.executor.execute_task_stream.

SECURITY -- the owner-chat-id allowlist is load-bearing. A bot token is
effectively public: anyone who finds the bot's username can message it.
is_owner() gates every inbound message against settings.
telegram_owner_chat_id, and send_message() will only ever send to that
same id (there is no arbitrary-recipient path). settings.telegram_enabled
plus a non-empty owner id plus the token must ALL be present or the
bridge is inert (is_configured() is the single check).

HTTP is a fixed-argv, shell=False `curl` subprocess -- the same pattern
tools/weather.py already uses for a plain third-party HTTP service, so
tests mock at the subprocess boundary and no new HTTP dependency is
added. Every function that the daemon calls in its loop either returns a
normalized dict or raises TelegramError; nothing here raises a bare
exception at the caller.
"""
import fcntl
import json
import os
import subprocess

from agent.observability import log_event, preview
from agent.secrets import get_secret
from config.settings import settings

TELEGRAM_API_BASE = "https://api.telegram.org"
BOT_TOKEN_SECRET = "TELEGRAM_BOT_TOKEN"

# Telegram hard-caps a single sendMessage at 4096 UTF-16 code units. A
# longer reply is split across several messages rather than truncated --
# silently dropping the tail of an assistant answer would be worse.
MAX_MESSAGE_LENGTH = 4096

_HTTP_TIMEOUT_SECONDS = 15

# Persists the getUpdates offset so a daemon restart neither reprocesses
# old messages nor skips ones that arrived while it was down. Atomic
# tmp-file-then-os.replace under an flock, matching
# agent/execution_history.py's _persist() convention.
OFFSET_FILE = os.path.expanduser(
    "~/Library/Application Support/CampusPilot/telegram_offset.json"
)


class TelegramError(Exception):
    """Any failure reaching or parsing the Telegram Bot API."""


def _bot_token():
    return get_secret(BOT_TOKEN_SECRET)


def is_configured() -> bool:
    """True only when the bridge is fully set up: enabled, an owner chat
    id, and a bot token. Every entry point checks this first."""
    return bool(
        settings.telegram_enabled
        and str(settings.telegram_owner_chat_id).strip()
        and _bot_token()
    )


def is_owner(chat_id) -> bool:
    """Whether chat_id is the one allowlisted owner. Compared as trimmed
    strings -- Telegram chat ids are integers on the wire but configured
    as text, and an empty owner id never matches anything."""
    owner = str(settings.telegram_owner_chat_id).strip()
    return bool(owner) and str(chat_id).strip() == owner


def _api(method: str, params: dict) -> object:
    """One Bot API call. Returns the `result` field on success, raises
    TelegramError otherwise. Never logs the token or full message text."""
    token = _bot_token()
    if not token:
        raise TelegramError("no Telegram bot token configured")
    url = f"{TELEGRAM_API_BASE}/bot{token}/{method}"
    try:
        completed = subprocess.run(
            [
                "curl", "-s", "--max-time", str(_HTTP_TIMEOUT_SECONDS),
                "-X", "POST", "-H", "Content-Type: application/json",
                "-d", json.dumps(params), url,
            ],
            capture_output=True, text=True, timeout=_HTTP_TIMEOUT_SECONDS + 5,
        )
    except (subprocess.SubprocessError, OSError) as error:
        raise TelegramError(f"Telegram API call failed: {type(error).__name__}") from error

    if completed.returncode != 0:
        raise TelegramError(f"curl exited {completed.returncode} calling {method}")
    try:
        body = json.loads(completed.stdout)
    except (json.JSONDecodeError, ValueError) as error:
        raise TelegramError(f"unreadable Telegram response for {method}") from error
    if not body.get("ok"):
        raise TelegramError(f"Telegram API error for {method}: {body.get('description', 'unknown')}")
    return body.get("result")


def _chunk(text: str):
    for start in range(0, len(text), MAX_MESSAGE_LENGTH):
        yield text[start:start + MAX_MESSAGE_LENGTH]


def send_message(text: str, chat_id=None) -> dict:
    """Send plain text to the owner chat (chat_id defaults to the
    configured owner; passing a non-owner id is rejected). Splits an
    over-long message across several sends rather than truncating. Never
    raises -- returns {"sent": bool, "chunks": int, "error": str|None}."""
    if not is_configured():
        return {"sent": False, "chunks": 0, "error": "Telegram bridge is not configured"}

    target = str(chat_id).strip() if chat_id is not None else str(settings.telegram_owner_chat_id).strip()
    if not is_owner(target):
        return {"sent": False, "chunks": 0, "error": "refusing to send to a non-owner chat id"}

    body = text if (text and text.strip()) else "(empty response)"
    sent = 0
    for piece in _chunk(body):
        try:
            _api("sendMessage", {"chat_id": target, "text": piece})
            sent += 1
        except TelegramError as error:
            log_event(
                "telegram_send_failed", component="telegram_bridge", level="warning",
                chunks_sent=sent, error=str(error),
            )
            return {"sent": False, "chunks": sent, "error": str(error)}

    log_event(
        "telegram_send_completed", component="telegram_bridge",
        chunks=sent, message_preview=preview(body),
    )
    return {"sent": True, "chunks": sent, "error": None}


def get_updates(offset=None, timeout=None) -> list:
    """Long-poll for new updates. `timeout` defaults to
    settings.telegram_poll_timeout_seconds. Restricted to message updates.
    Raises TelegramError on any API/transport failure (the daemon decides
    how to back off)."""
    poll_timeout = settings.telegram_poll_timeout_seconds if timeout is None else timeout
    params = {"timeout": poll_timeout, "allowed_updates": ["message"]}
    if offset is not None:
        params["offset"] = offset
    result = _api("getUpdates", params)
    return result if isinstance(result, list) else []


def load_offset() -> int:
    """The next getUpdates offset to request, or 0 if none is stored yet
    or the file is unreadable (a corrupt/missing offset just means we
    start from Telegram's own default, never a crash)."""
    try:
        with open(OFFSET_FILE) as handle:
            return int(json.load(handle).get("offset", 0))
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return 0


def save_offset(offset: int) -> None:
    """Persist the next offset atomically. Best-effort: a write failure is
    logged, never raised -- the daemon keeps running off its in-memory
    offset and just risks reprocessing on a restart."""
    try:
        os.makedirs(os.path.dirname(OFFSET_FILE), exist_ok=True)
        tmp = f"{OFFSET_FILE}.tmp"
        with open(tmp, "w") as handle:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            json.dump({"offset": int(offset)}, handle)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, OFFSET_FILE)
    except OSError as error:
        log_event(
            "telegram_offset_persist_failed", component="telegram_bridge",
            level="warning", error_type=type(error).__name__,
        )
