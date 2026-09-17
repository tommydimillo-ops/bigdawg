# Alexa bridge — an Echo Dot as a fourth entry point

Added September 2026. Alongside `app.py` (Streamlit), `ui/menu_bar.py`
(voice) and `agent/scheduler_daemon.py`, an Echo Dot can now hand tasks
to the same `agent/executor.py` orchestrator.

## Running it

```bash
bash ~/CampusPilot/start_alexa_bridge.sh
```

Prints a tunnel URL and a bridge token. Both go in the Alexa skill's
`index.js` CONFIG block (`BRIDGE_URL`, `BRIDGE_TOKEN`). The URL changes
every restart; the token doesn't.

Leaving that window open is what makes the Dot able to reach Jarvis.
Ctrl-C is the kill switch.

## What the Dot can say

| Phrasing | What happens |
|---|---|
| "Alexa, open Jarvis" | Starts a session |
| Any question | Answered directly by the Anthropic API, inside Alexa's 8s |
| "go and \<task\>", "task: \<task\>", "work on \<x\>", "on my Mac, \<x\>" | Handed to the executor as `source="alexa"` |
| "what happened", "did it work", "status" | Reads back the last finished task |

Task openers are deliberately explicit. "Delete my downloads folder"
stays a *question* — it is not a task opener — because the cost of
misrouting ambient speech into a real agent turn is much higher than the
cost of making you rephrase.

## Why `source="alexa"` is gated the way it is

An Echo is two risky things at once, and `agent/autonomy.py` already had
the reasoning for both:

- **Ambient voice.** It is in `_AMBIENT_VOICE_SOURCES` alongside
  `"voice"`, so `_VOICE_ALWAYS_CONFIRMS` (`add_reminder`,
  `open_browser`, `consult_coworker_agent`) applies. A Dot hears a whole
  room and will transcribe the television.
- **Non-interactive.** It is in `_NON_INTERACTIVE_SOURCES` alongside
  `"scheduled"` and `"agent_worker"`. The skill answers and hangs up, so
  there is no round trip; a CONFIRM verdict could never be answered.

Combined: anything that would confirm becomes DENY. At the default
autonomy level 4 that still leaves reads, writes and code execution
running automatically — the gate is only on the destructive and
misfire-prone set.

**The load-bearing property:** no `(tool, autonomy level)` pair may
return CONFIRM for `source="alexa"`, or the task hangs forever with
nobody able to answer. `tests/test_autonomy.py` asserts this directly.

### Bug found while wiring this up

`should_request_confirmation`'s unregistered-tool guard returned
`Decision.CONFIRM` directly, *before* the non-interactive check ran — so
a scheduled task or agent worker calling an unregistered tool got a
verdict nobody could answer. Both confirm paths now route through
`_confirm_or_deny()`. This affected `"scheduled"` and `"agent_worker"`
before Alexa existed.

## Security

The bridge listens on `127.0.0.1` only; the tunnel is the single ingress.
Every request except `/health` requires `ALEXA_BRIDGE_TOKEN` (Keychain,
via `agent/secrets.py`), compared with `hmac.compare_digest`. Without a
token the daemon refuses to start rather than accepting unauthenticated
instructions — the tunnel URL is public, and `POST /task` is an
instruction to a full agent on this Mac.

One task runs at a time (`_run_lock`); a second arriving mid-task gets a
409 and the skill says so out loud rather than stacking agent turns.

**Still worth knowing:** anyone who can speak near the Dot can start a
task, and browsing tasks expose the agent to prompt injection from page
content on a machine holding your files. The autonomy gating narrows
that blast radius; it does not remove it.

## Files

| File | Role |
|---|---|
| `agent/alexa_bridge.py` | Auth, result store, task runner |
| `agent/alexa_daemon.py` | HTTP listener |
| `start_alexa_bridge.sh` | Token, cloudflared, listener, tunnel |
| `agent/autonomy.py` | `_AMBIENT_VOICE_SOURCES`, alexa in non-interactive, `_confirm_or_deny` |
| `tests/test_autonomy.py` | `TestAlexaSource`, `TestUnregisteredToolRespectsNonInteractiveSources` |
| `tests/test_alexa_bridge.py` | Token/auth, last-result persistence, spoken summary, `run_task`/`start_task` |
| `tests/test_alexa_daemon.py` | The HTTP listener, exercised against a real loopback socket |

`source="alexa"` is also a first-class value in `agent/history_store.py`'s
`_VALID_SOURCES` and `agent/history_capture.py` (fresh session per task,
same reasoning as `"scheduled"` — each Echo task is independent, run with
an empty history list, so there is nothing to group into one
conversation).

Results are delivered over `agent/telegram_bridge.py` and cached at
`~/Library/Application Support/CampusPilot/alexa_last_result.json` (path
redirected in the test suite via `tests/_safety.py`, same as every other
persistent-store constant).
