"""Inbound half of the direct Telegram bridge: long-polls the Bot API and
feeds the OWNER's text messages into the same agent loop every other
interface uses (agent.executor, source="telegram"), sending the reply
back to the same chat.

Standalone, like agent/scheduler_daemon.py -- nothing here runs unless
you start it yourself, and only after configuring the bridge
(docs/TELEGRAM.md):

    python -m agent.telegram_daemon

SECURITY: only messages whose chat id equals settings.telegram_owner_
chat_id are ever passed to the agent. Every other chat is logged and
dropped -- a bot token is effectively public. A single-instance flock
(agent/telegram_lock.py) prevents two daemons from racing getUpdates.

Inbound Telegram text is treated as an ordinary live conversation from
the authenticated owner: normal autonomy applies, and a tool that needs
confirmation will ask for it in one message and act on the owner's "yes"
in the next (the daemon keeps per-conversation history for exactly this).
It is NOT given voice-style misfire hardening -- unlike ambient room
audio, a typed Telegram message from the allowlisted chat is a
deliberate instruction.
"""
import time

from agent import telegram_lock
from agent.executor import execute_task
from agent.observability import log_event, preview
from agent.telegram_bridge import (
    TelegramError,
    get_updates,
    is_configured,
    is_owner,
    load_offset,
    save_offset,
    send_message,
)

# Kept small: enough for follow-ups and a confirm/"yes" exchange, not a
# full transcript (agent/history_capture.py owns the durable record).
_MAX_HISTORY_MESSAGES = 40

# Backoff after a transient getUpdates failure so a persistent outage
# (network down, token revoked) doesn't spin the loop.
_ERROR_BACKOFF_SECONDS = 15

_RESET_COMMANDS = {"/reset", "/new", "/clear"}


def _bound(history):
    if len(history) > _MAX_HISTORY_MESSAGES:
        del history[: len(history) - _MAX_HISTORY_MESSAGES]


def _handle_message(message: dict, history: list) -> None:
    chat = message.get("chat") or {}
    chat_id = chat.get("id")

    if not is_owner(chat_id):
        log_event(
            "telegram_message_ignored", component="telegram_daemon", level="warning",
            reason="non_owner_chat", chat_id=str(chat_id),
        )
        return

    if "text" not in message:
        # Voice notes / photos / stickers aren't handled yet.
        send_message("I can only handle text messages here right now.")
        return

    text = (message.get("text") or "").strip()
    if not text:
        return

    if text in _RESET_COMMANDS:
        history.clear()
        send_message("Started a fresh conversation.")
        return

    log_event(
        "telegram_message_received", component="telegram_daemon",
        message_preview=preview(text),
    )

    history.append({"role": "user", "content": text})
    try:
        reply = execute_task(text, history, source="telegram")
    except Exception as error:  # the agent loop should not, but never let it kill the daemon
        log_event(
            "telegram_turn_failed", component="telegram_daemon", level="error",
            error_type=type(error).__name__,
        )
        history.pop()  # don't keep a user turn that produced nothing
        send_message("Sorry — something went wrong handling that.")
        return

    history.append({"role": "assistant", "content": reply})
    _bound(history)
    send_message(reply)


def run_forever() -> None:
    if not is_configured():
        raise SystemExit(
            "Telegram bridge is not configured. Set TELEGRAM_ENABLED=true, "
            "TELEGRAM_OWNER_CHAT_ID, and the TELEGRAM_BOT_TOKEN secret -- "
            "see docs/TELEGRAM.md."
        )

    lock_handle = telegram_lock.acquire_or_exit()  # held for the process lifetime
    history: list = []
    offset = load_offset()
    print("CampusPilot Telegram daemon running. Press Ctrl+C to stop.")
    log_event("telegram_daemon_started", component="telegram_daemon")

    try:
        while True:
            try:
                updates = get_updates(offset)
            except TelegramError as error:
                log_event(
                    "telegram_poll_failed", component="telegram_daemon", level="warning",
                    error=str(error),
                )
                time.sleep(_ERROR_BACKOFF_SECONDS)
                continue

            for update in updates:
                offset = update["update_id"] + 1
                message = update.get("message")
                if message:
                    try:
                        _handle_message(message, history)
                    except Exception as error:
                        log_event(
                            "telegram_handler_crashed", component="telegram_daemon",
                            level="error", error_type=type(error).__name__,
                        )
                save_offset(offset)
    finally:
        lock_handle.close()


if __name__ == "__main__":
    try:
        run_forever()
    except KeyboardInterrupt:
        print("\nTelegram daemon stopped.")
