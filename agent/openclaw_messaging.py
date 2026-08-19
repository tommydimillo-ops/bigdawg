"""OpenClaw M2 -- outbound text messaging via the real, direct Gateway
`send` RPC (never `chat.send` -- see agent/openclaw_gateway.py's module
docstring for the verified, primary-source reason `chat.send` is part of
OpenClaw's own agent/session execution surface, a different concern
entirely, and `send` requires `operator.write`, checked via a SEPARATE
device identity from the M1 read-only one).

This module owns the MESSAGING POLICY layer: channel/recipient allowlist
enforcement (Jarvis-side, deterministic, never OpenClaw's own channel-
side name/directory lookups), message validation, idempotency-key
generation, and result normalization. It makes NO transport/auth
decisions of its own -- agent/openclaw_gateway.py owns the connection/
profile/RPC-allowlist layer; this module never opens a socket directly.

M2 FIRST PASS -- TEXT ONLY, EXACT CHANNEL + EXACT TARGET ONLY. No media/
attachments/voice notes/polls/reactions/edits/deletes/embeds/buttons/
files, even though the real `send` RPC schema supports several of these
(see agent/openclaw_gateway.py's module docstring) -- deliberately
unused here. No `account_id`/`thread_id` either: the real `SendParams`
schema marks both `accountId`/`threadId` as optional
(`Type.Optional(Type.String())`), so neither is required for a basic
single-account direct-message path -- they introduce additional routing
dimensions this bridge does not yet independently allowlist, so they are
deliberately absent from the public surface for this first release
rather than accepted and left unvalidated. Add them later, with their
own deterministic authorization, if/when actually needed. No real
messaging channel has been configured or tested against a live external
service as of this writing; every automated test in this pass uses the
same local fake Gateway server pattern established for M1/M1.5, never a
real channel login, and `openclaw_messaging_enabled` defaults to False
with an empty channel/target allowlist, so a fresh install cannot send
anywhere until explicitly configured.

DELIVERY STATUS -- three, not two, and AT MOST ONE TRANSMISSION PER
LOGICAL SEND: `OpenClawTimeout`/`OpenClawUnavailable`/every other
agent.openclaw_gateway error reaching this module means the failure
happened BEFORE the request frame was ever transmitted (auth failed,
Gateway unreachable, timed out waiting for the challenge/hello --
agent/openclaw_gateway.py's `_connect_and_call` only raises the distinct
`OpenClawUncertainDelivery` AFTER the send request frame was actually
written to the socket) -- these are safe to report as a definitive
"failed". `OpenClawUncertainDelivery` means the frame WAS sent but no
trustworthy response arrived -- this module does NOT automatically
retry that, with the same idempotencyKey or otherwise; it is reported
directly as "uncertain" and left there.

WHY NOT RETRY AUTOMATICALLY, EVEN WITH THE SAME KEY (corrected from an
earlier version of this module, which did): the real Gateway's
idempotency/dedupe cache is genuinely real, but it is an in-memory,
single-process, ~5-minute-TTL cache (see agent/openclaw_gateway.py's
module docstring for the exact primary-source citations) -- it does NOT
establish durable exactly-once delivery across a Gateway process
restart. The specific failure scenario this module now avoids: Jarvis
transmits `send`; OpenClaw successfully delivers the external message;
the Gateway process dies or restarts before Jarvis receives the
response; Jarvis observes uncertain delivery; the in-memory dedupe
entry is gone with the dead process; an automatic resend -- even with
the identical idempotencyKey -- could no longer be deduplicated and may
send a real duplicate message. For an external, user-visible side
effect, correctness takes priority over speculative automatic recovery
of an ambiguous outcome. The idempotencyKey is still generated and sent
on every call (protocol correctness and defense-in-depth against the
Gateway's own retry/replay machinery, and useful if a human explicitly
asks Jarvis to try again later within the cache's live window), but
this module itself never uses it as justification for a second network
attempt.
"""
import time
import uuid
from typing import Optional

from agent.observability import log_event, preview
from agent.openclaw_gateway import (
    OpenClawError,
    OpenClawPairingRequired,
    OpenClawUncertainDelivery,
    _send_raw,
)
from agent.request_context import get_current_request_id
from config.settings import settings

# Conservative, channel-neutral cap -- not any specific channel's real
# limit (those vary widely and are channel-plugin concerns), just a
# sanity bound so a runaway/malformed caller can't hand this bridge an
# arbitrarily large payload. Silently truncating a message a user asked
# to send would be worse than refusing it outright, so this rejects
# rather than truncates.
MAX_MESSAGE_LENGTH = 4000


class MessageValidationError(Exception):
    """A definitive, pre-network validation failure -- this module never
    even calls agent.openclaw_gateway._send_raw() for one of these, so it
    is always distinct from (and never confusable with) an
    agent.openclaw_gateway.OpenClawError, which only happens after a
    real network attempt."""


def _parse_allowed_channels() -> frozenset:
    return frozenset(
        entry.strip().lower() for entry in settings.openclaw_allowed_channels.split(",") if entry.strip()
    )


def _parse_allowed_targets() -> frozenset:
    """Returns a set of (channel, target) tuples. Channel is compared
    lowercased (channel names are conventionally lowercase identifiers);
    target is compared exactly as configured, case-sensitive, since many
    real channel target IDs (Discord snowflakes, WhatsApp JIDs) are
    opaque and not safe to case-fold. No wildcards, no regex, no partial
    matching -- see module docstring."""
    pairs = set()
    for entry in settings.openclaw_allowed_targets.split(","):
        entry = entry.strip()
        if not entry or ":" not in entry:
            continue
        channel, _, target = entry.partition(":")
        channel = channel.strip().lower()
        target = target.strip()
        if channel and target:
            pairs.add((channel, target))
    return frozenset(pairs)


def _validate(channel: Optional[str], target: Optional[str], message: Optional[str]) -> None:
    if not settings.openclaw_messaging_enabled:
        raise MessageValidationError("OpenClaw outbound messaging is disabled")
    if not channel or not channel.strip():
        raise MessageValidationError("channel is required")
    if not target or not target.strip():
        raise MessageValidationError("target is required")
    if message is None or not message.strip():
        raise MessageValidationError("message must not be empty or whitespace-only")
    if len(message) > MAX_MESSAGE_LENGTH:
        raise MessageValidationError(
            f"message exceeds the maximum length of {MAX_MESSAGE_LENGTH} characters"
        )

    normalized_channel = channel.strip().lower()
    if normalized_channel not in _parse_allowed_channels():
        raise MessageValidationError(f"channel '{channel}' is not allowlisted")
    if (normalized_channel, target.strip()) not in _parse_allowed_targets():
        raise MessageValidationError(f"target is not allowlisted for channel '{channel}'")


def send_message(channel: Optional[str], target: Optional[str], message: Optional[str]) -> dict:
    """The one entry point tools/schemas/openclaw.py's
    send_message_via_openclaw tool calls. Never raises -- every failure
    mode normalizes to one of the three delivery_status shapes described
    in the module docstring. Makes AT MOST ONE transmission of the
    side-effecting `send` request -- no automatic retry on uncertain
    delivery, see module docstring for why. Safe fields only in both the
    return value and every log_event call: never the Gateway token,
    device token, private key, or full message body (a short preview()
    only)."""
    request_id = get_current_request_id()

    try:
        _validate(channel, target, message)
    except MessageValidationError as error:
        log_event(
            "openclaw_message_send_failed", request_id=request_id, component="openclaw_messaging",
            level="warning", channel=channel, error_category="validation",
        )
        return {"sent": False, "delivery_status": "failed", "error": str(error)}

    normalized_channel = channel.strip().lower()
    normalized_target = target.strip()
    # Generated and sent on the one transmission this call makes --
    # protocol correctness / defense-in-depth against the Gateway's own
    # replay machinery, not a license for this module to resend
    # automatically (see module docstring).
    idempotency_key = str(uuid.uuid4())

    params = {
        "channel": normalized_channel,
        "to": normalized_target,
        "message": message,
        "idempotencyKey": idempotency_key,
    }

    log_event(
        "openclaw_message_send_started", request_id=request_id, component="openclaw_messaging",
        channel=normalized_channel, message_preview=preview(message),
    )
    start = time.time()

    try:
        payload = _send_raw(params)
    except OpenClawPairingRequired as error:
        # Normalized separately from M1's own read-identity pairing (a
        # distinct dict shape from get_status()'s, and only ever
        # returned from THIS function) -- never auto-approved, never
        # reuses the read-only device token.
        log_event(
            "openclaw_message_send_failed", request_id=request_id, component="openclaw_messaging",
            level="warning", channel=normalized_channel, error_category="pairing_required",
        )
        return {
            "sent": False, "delivery_status": "failed", "pairing_required": True,
            "pairing_request_id": error.request_id,
            "error": "OpenClaw messaging device pairing approval is required "
                     "(this identity requests operator.write).",
        }
    except OpenClawUncertainDelivery:
        # The request frame WAS transmitted but no trustworthy response
        # arrived -- never automatically resent, with this idempotencyKey
        # or a new one. See module docstring for why.
        log_event(
            "openclaw_message_send_uncertain", request_id=request_id, component="openclaw_messaging",
            level="warning", channel=normalized_channel,
        )
        return {
            "sent": False, "delivery_status": "uncertain",
            "channel": normalized_channel, "target": normalized_target,
            "error": "Delivery could not be confirmed.",
        }
    except OpenClawError as error:
        # Reached only for a failure BEFORE the request frame was
        # transmitted (see module docstring) -- definitive.
        log_event(
            "openclaw_message_send_failed", request_id=request_id, component="openclaw_messaging",
            level="warning", channel=normalized_channel, error_category="openclaw_error",
        )
        return {"sent": False, "delivery_status": "failed", "error": str(error)}
    else:
        log_event(
            "openclaw_message_send_completed", request_id=request_id, component="openclaw_messaging",
            channel=normalized_channel, duration_seconds=round(time.time() - start, 3),
        )
        return {
            "sent": True, "delivery_status": "confirmed",
            "channel": normalized_channel, "target": normalized_target,
            "message_id": payload.get("messageId"),
        }
