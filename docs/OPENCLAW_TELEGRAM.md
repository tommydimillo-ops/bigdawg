# Configuring Telegram as the first real OpenClaw messaging channel

This is the setup runbook for turning on OpenClaw M2's outbound messaging
against a real channel for the first time. Telegram is the intended first
channel. Everything on the **Jarvis side** is already built and tested
against a local fake Gateway — what remains is a credential only you can
create, an OpenClaw-side channel you must stand up and verify, and a few
config values.

Read `ARCHITECTURE.md` §4 / the OpenClaw sections of `HANDOFF.md` and
`ROADMAP.md` first for how the bridge is structured and why.

---

## Scope — what this path does and does not do

- **Outbound, text-only.** Jarvis calls the Gateway's `send` RPC through
  the `send_message_via_openclaw` tool (`tools/schemas/openclaw.py`),
  which is `permission_level=3`, `side_effect=True`,
  `requires_live_confirmation=True` — the same class as `send_email`.
  Every send needs your explicit in-conversation confirmation.
- **No inbound.** Nothing here polls Telegram for incoming messages.
  The `.relay/reference/telegram_bot.py` reference implementation *does*
  poll (`getUpdates`) and is a completely different architecture (a
  standalone bot talking straight to `api.telegram.org`, no OpenClaw).
  Inbound is a separate, larger surface — see `ROADMAP.md`'s "Inbound
  Gmail read/draft tool" entry for the shape that work would take.
- **No media, no `account_id`/`thread_id`.** Deliberately out of M2's
  first release — see `agent/openclaw_messaging.py`'s module docstring.
- **Channel-agnostic by design.** The Jarvis bridge has *no*
  Telegram-specific code and does not need any: a channel is just an
  allowlisted name, a recipient just an allowlisted exact target string.
  That is the whole point of the design — do not add per-channel
  branching to `agent/openclaw_messaging.py`.

---

## The one credential you must create

**A Telegram Bot API token**, from [@BotFather](https://t.me/BotFather)
(`/newbot`), plus **your own numeric chat ID**.

To get your chat ID: message your new bot once from your own Telegram
account, then open
`https://api.telegram.org/bot<TOKEN>/getUpdates` and read
`result[0].message.chat.id` (a bare integer for a direct chat; a
`-100…` value for a group/supergroup).

That numeric chat ID is the **only** recipient that should ever be
allowlisted. A Telegram bot token is effectively public — anyone who
finds the bot's username can message it — so an owner-only allowlist is
load-bearing, not optional. This mirrors the reference bot's
`TELEGRAM_OWNER_CHAT_ID` whitelist, which is the one instinct from that
file worth carrying over.

Nobody can create this token on your behalf, and no standing
authorization substitutes for it. Once you have it, the token itself is
configured **on the OpenClaw side** (next section), never in this repo —
Jarvis never sees the Telegram token.

---

## OpenClaw-side prerequisites (your responsibility to verify)

The Jarvis bridge sends `{channel: "telegram", to: <chat id>, message,
idempotencyKey}` to the Gateway's `send` RPC. For that to deliver a real
Telegram message, the **OpenClaw Gateway itself** must:

1. Be running and reachable at `OPENCLAW_GATEWAY_URL`
   (default `ws://127.0.0.1:18789`).
2. Have a working **Telegram channel integration** configured with your
   bot token, such that `send` with `channel: "telegram"` routes to it.

**Open question you must resolve against current OpenClaw docs:** whether
OpenClaw's Telegram support ships as a first-party channel or as a
plugin. `ROADMAP.md`'s standing constraint for every OpenClaw milestone
is **no third-party OpenClaw plugin dependency** (OpenClaw plugins run
with full host privileges, no sandboxing — confirmed in the M0 audit).
If Telegram support is only available as a third-party plugin, that
constraint is not automatically waived by "set up Telegram" — stop and
decide explicitly. This repo cannot verify OpenClaw's current channel
model from here; check it the same way the M1/M1.5 work verified the
device-auth flow (against the real, running Gateway and its published
packages).

---

## Jarvis-side configuration

All four are environment variables (or `.env`), read by
`config/settings.py`. None is a secret except the Gateway token.

| Variable | Value | Read by |
|---|---|---|
| `OPENCLAW_ENABLED` | `true` | `settings.openclaw_enabled` |
| `OPENCLAW_MESSAGING_ENABLED` | `true` | `settings.openclaw_messaging_enabled` |
| `OPENCLAW_ALLOWED_CHANNELS` | `telegram` | `settings.openclaw_allowed_channels` |
| `OPENCLAW_ALLOWED_TARGETS` | `telegram:<your numeric chat id>` | `settings.openclaw_allowed_targets` |

`OPENCLAW_ALLOWED_TARGETS` is `channel:target` pairs, comma-separated,
exact match only — no wildcards, no name resolution. `telegram:*` does
**not** mean "any Telegram chat"; it allowlists the literal string `*`.

### Messaging device identity + pairing

Outbound messaging uses a **separate** Ed25519 device identity from M1's
read-only one — `OPENCLAW_MESSAGE_DEVICE_PRIVATE_KEY` /
`OPENCLAW_MESSAGE_DEVICE_TOKEN` (via `agent/secrets.py` / Keychain),
never the read identity's keys. It requests `operator.write` and is
confined to exactly the `send` RPC.

First real send will come back `pairing_required` with a request id.
Approve it as a human on the Gateway host
(`openclaw devices approve <requestId>`) — Jarvis never auto-approves a
pairing. After approval the Gateway issues
`OPENCLAW_MESSAGE_DEVICE_TOKEN` and subsequent sends reuse it.

The shared bootstrap token `OPENCLAW_GATEWAY_TOKEN` must be present for
the first pairing handshake (same one M1 already uses).

---

## Verify the config without sending anything

`openclaw_status` (the read-only M1 tool, `permission_level=0`) now
carries a `messaging` block from
`agent.openclaw_messaging.messaging_config_summary()` — pure, no network,
no send:

```json
{
  "configured": true,
  "available": true,
  "messaging": {
    "enabled": true,
    "allowed_channels": ["telegram"],
    "targets_per_channel": {"telegram": 1},
    "message_device_token_present": false,
    "bootstrap_token_present": true,
    "config_complete": true,
    "blocking": []
  }
}
```

`config_complete` is `true` only when messaging is enabled, at least one
channel is allowlisted, every allowlisted channel has at least one
target, and a credential is present. `blocking` lists what is still
missing in plain language. Raw target IDs are never included — only
per-channel counts.

---

## The first real send

1. Confirm `openclaw_status`'s `messaging.config_complete` is `true`.
2. Confirm `messaging.available` / the Gateway is reachable.
3. Ask Jarvis to send a short test message to yourself. It will surface
   the exact channel, target, and body for your confirmation (level-3
   live confirmation), then call `send` **once**.
4. If the result is `pairing_required`, approve on the Gateway host and
   retry.
5. A `delivery_status: "uncertain"` result means the frame was
   transmitted but no trustworthy response came back. Jarvis does **not**
   auto-retry that (an in-memory Gateway dedupe cache does not survive a
   Gateway restart, so a resend is not provably safe). Check Telegram
   directly before asking Jarvis to try again.

---

## Security properties that stay true

- Separate `operator.write` device identity from M1's read identity;
  `send` is the only RPC this identity can reach through Jarvis.
- Deterministic Jarvis-side channel + exact-target allowlist — a model
  cannot pick an arbitrary recipient, only an already-configured one.
- At most one transmission per logical send; no automatic retry on an
  uncertain outcome.
- `agent/verification.py` parses the send result directly, so an
  uncertain/failed delivery is never reported as confirmed success.
- Nothing here gives OpenClaw any authority over Jarvis — no
  model-routing input, no OpenClaw-initiated tool execution, no shared
  secret/memory store, no `node.invoke`.
