# Telegram bridge (direct, two-way)

Lets you talk to Jarvis from Telegram — text it from your phone, get
replies, and let Jarvis ping you (e.g. when a scheduled job finishes).

This is a **direct** integration: `agent/telegram_bridge.py` talks
straight to `api.telegram.org` with a bot token. It does **not** go
through OpenClaw. Nothing here runs until you configure it and start the
inbound daemon yourself.

---

## 1. You need a bot token and your chat ID

- **Bot token** — from [@BotFather](https://t.me/BotFather) (`/newbot`).
- **Your numeric chat ID** — message your new bot once from your own
  Telegram account, then open
  `https://api.telegram.org/bot<TOKEN>/getUpdates` in a browser and read
  `result[0].message.chat.id` (a bare integer for a direct chat).

That chat ID is the **only** chat this bridge will ever send to or accept
a message from. A bot token is effectively public — anyone who finds the
bot's username can message it — so this owner-only allowlist is
load-bearing.

---

## 2. Store the token as a secret

The token is a real secret — it goes in the Keychain (or `.env`), never
in `config/settings.py` or any tracked file.

```bash
python -m tools.manage_secrets   # add TELEGRAM_BOT_TOKEN
```

or in `.env`:

```
TELEGRAM_BOT_TOKEN=123456:ABC-your-token
```

---

## 3. Set the config

Environment variables (or `.env`), read by `config/settings.py`:

| Variable | Value |
|---|---|
| `TELEGRAM_ENABLED` | `true` |
| `TELEGRAM_OWNER_CHAT_ID` | your numeric chat ID from step 1 |
| `TELEGRAM_POLL_TIMEOUT_SECONDS` | `50` (default; leave it) |

The bridge is inert unless `TELEGRAM_ENABLED` is true **and**
`TELEGRAM_OWNER_CHAT_ID` is set **and** the token secret is present.

---

## 4. Start the inbound daemon

```bash
python -m agent.telegram_daemon
```

It long-polls Telegram, feeds **your** messages into the same agent loop
every other interface uses (`source="telegram"`), and sends the reply
back. Leave it running (a `launchd` plist, `tmux`, etc.). Only one copy
can run at a time — a second exits immediately (a single-instance lock).

- `/reset` (or `/new`, `/clear`) in the chat starts a fresh conversation.
- Voice notes, photos, and stickers aren't handled yet — you'll get a
  "text only" reply.
- If the agent errors on a turn, the daemon stays up and you get a short
  apology rather than silence.

---

## 5. Outbound (Jarvis → you)

The `send_telegram_message` tool sends plain text to your configured chat
(no recipient parameter — it can only ever reach you). It's
`permission_level=3` but does not require live confirmation and **is**
allowed unattended, so a scheduled task can use it to notify you.

---

## 6. Security model

- **Owner-only.** Inbound messages from any other chat ID are logged and
  dropped before the agent ever sees them. Outbound refuses any chat ID
  that isn't the configured owner.
- **Inbound text is treated as an ordinary live conversation** from the
  authenticated owner — normal autonomy applies, and a tool that needs
  confirmation asks in one message and acts on your "yes" in the next. It
  is *not* given the voice-misfire hardening (`agent/autonomy.py`'s
  always-confirm list), because a typed Telegram message from the
  allowlisted chat is a deliberate instruction, not ambient noise.
- **Account security is yours.** Anyone with access to your Telegram
  account (or an unlocked phone logged into it) can drive Jarvis. Treat
  it like any other authenticated session.
- The bot token never leaves `agent/secrets.py`; it is not logged.

---

## 7. Not built yet

- Voice-note transcription (the local STT backend isn't wired in here).
- Media/attachments in or out.
- Multiple allowed chats / group chats.
- Menu-bar integration — the inbound loop is a standalone daemon for now.
