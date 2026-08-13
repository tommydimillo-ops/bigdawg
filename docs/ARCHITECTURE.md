# CampusPilot / Jarvis — Current Architecture

This describes the system as it actually exists today. It does not describe
planned or hypothetical future modules (vector memory, multi-agent
delegation, a hardware client, etc.) — those are deliberately not built yet.

## Entry points

There are four ways to actually run this:

| Entry point | What it is |
|---|---|
| `streamlit run app.py` | The main web chat UI |
| `python -m ui.menu_bar` | Native macOS menu-bar app (voice, always-on) — this is what the LaunchAgent (`~/Library/LaunchAgents/com.tommy.campuspilot.plist`) actually starts at login |
| `python -m agent.scheduler_daemon` | Standalone scheduled-task runner — **redundant with the menu-bar app's built-in scheduler; don't run both at once** (see the lifecycle note at the top of that file) |
| `python -m tools.manage_logins` / `python -m tools.manage_secrets` | CLI-only credential setup, deliberately kept out of chat |

All of them ultimately call into the same `agent.executor.execute_task` /
`execute_task_stream`.

## Agent execution flow

```
execute_task_stream(request, history, source)
  -> RequestContext.create()         (agent/request_context.py — request_id, timestamp, input)
  -> ExecutionState(...)             (agent/execution_state.py — iteration/tool/timing tracking)
  -> log_event("request_started")
  -> _run_claude_loop_stream(...)    (tries the primary provider first)
       -> model_router.select(attempt=0)  -> Claude
       -> loop up to MAX_TOOL_ITERATIONS (config: max_agent_steps):
            -> call Claude with the full tool list
            -> if it asks for tool(s): _run_tool_batch -> _run_tool (per tool)
                 -> permission/confirmation/unattended checks (tools/registry.py)
                 -> registry.dispatch(name, input) -> the tool's handler
                 -> agent.audit.log_action (security/action log)
                 -> agent.observability.log_event (diagnostics log)
            -> else: return the model's text answer
  -> on failure (and nothing already streamed): fall back to
     _run_openai_loop_stream (model_router.select(attempt=1) -> gpt-5),
     same loop shape
  -> log_event("request_completed" / "request_failed")
```

Both the streaming (used by the UI) and non-streaming (`execute_task`,
used by the scheduler and CLI) paths go through this same code — there is
one agent loop, not two.

## Tool registry

`tools/registry.py` is the single source of truth for every tool: name,
description, JSON schema, permission level, the actual handler function,
and three execution-gating flags (`requires_live_confirmation`,
`unattended_allowed`, `side_effect`, `parallel_safe`). Tools are grouped
by theme into `tools/schemas/*.py` (browsing, logins_and_email,
productivity, system, memory_and_learning, reasoning, scheduling,
computer_use) — each module calls `registry.register(ToolSpec(...))` for
its tools at import time.

`agent/brain.py`'s `TOOLS` list, `agent/permissions.py`'s permission
lookups, and `agent/executor.py`'s dispatch are all *derived* from this
registry — none of them hold their own copy of tool data anymore. See
`docs/ADDING_A_TOOL.md` for how to add a new one.

`agent/research_agent.py` is the one exception: it's a self-contained
sub-agent with its own tiny internal tool loop (2 tools: open_browser,
read_document), invoked as a single tool call from the main registry
(`research_agent`). It doesn't go through the main dispatch — deliberately
kept separate since it has its own lifecycle and system prompt.

## Permission system

Six levels (`tools/registry.py`'s `LEVEL_NAMES`):

| Level | Meaning |
|---|---|
| 0 | read-only |
| 1 | safe local action |
| 2 | modifies files/executes code |
| 3 | external communication |
| 4 | financial |
| 5 | destructive/system |

Beyond the level number, two independent gates apply per tool:
- **`requires_live_confirmation`** — hard-blocked when `source="scheduled"`.
  Currently: `confirm_login`, `send_email`. These finalize an
  already-previewed action (`fill_login`→`confirm_login`,
  `draft_email`→`send_email`) that a human must explicitly approve in
  their own words in chat — there's no code-level state machine detecting
  "approval," it's enforced by system-prompt instructions plus this
  scheduled-run hard block as a backstop.
- **`unattended_allowed=False`** — also hard-blocked when
  `source="scheduled"`, for a broader reason: real unsupervised control of
  whatever's on screen. Currently the whole `computer_*` family.

`agent/permissions.py` is now a thin re-export wrapper over the registry,
kept only so existing callers (`agent/audit.py`, `pages/1_Dashboard.py`)
don't need to change.

## AI provider flow

Two providers, formalized behind `agent/model_router.py`:

- **Primary**: Anthropic Claude (`config.settings.default_model`, currently
  `claude-sonnet-5`)
- **Fallback**: OpenAI (`config.settings.fallback_model`, currently `gpt-5`)
  — only used if a live call to the primary fails mid-request

`model_router.select(attempt)` returns which to use; the actual
try-primary-then-catch-and-fall-back-to-secondary behavior lives in
`agent/executor.py` and is intentionally not "smart" — a real API failure
is the only thing that triggers the fallback. `agent/provider_health.py`
provides cheap (no network call) checks for whether each provider is
configured (a key is present) and initialized (the shared client
constructed without error).

Shared HTTP client config (timeouts, retries, connection pooling, forced
IPv4 to dodge flaky router IPv6 paths) lives in `agent/chat.py`, values
now sourced from `config.settings` instead of hardcoded.

## Memory

Flat JSON key-value store: `database/memory.py` (absolute path at
`~/Library/Application Support/CampusPilot/memory.json`), wrapped by
`agent/memory_agent.py` (`remember`/`recall`, used for the `remember_fact`/
`recall_facts` tools). Three other purpose-built stores sit alongside it:
`agent/lessons.py` (standing rules from user corrections), `agent/patterns.py`
(inferred communication patterns), `agent/conversation_store.py` (chat
history for the Streamlit UI). No episodic/semantic split, no vector
store, no relevance-filtered retrieval — `recall_facts` just returns
everything stored.

## Voice

Two independent paths, deliberately not sharing a TTS implementation:

- **`voice/listen.py` + `voice/speak.py`** (native, menu-bar app): raw
  microphone sampling via `sounddevice` with adaptive volume-threshold
  wake-word detection (no continuous cloud streaming — audio only leaves
  the machine once real speech is detected), Whisper-family transcription
  (`config.settings.transcription_model`), TTS via `speak_natural()`
  (`config.settings.tts_model`, OpenAI, falls back to macOS `say` on
  failure).
- **`agent/tts_control.py`**: `speak_interruptible()`, used by the
  Streamlit multi-device chat's "speak replies aloud" checkbox — plain
  macOS `say`, tracked by PID so a reply from a different device can
  interrupt speech in progress. Different tool for a different job (cheap/
  interruptible vs. higher quality), not a duplicate of the above.

Both read the wake word from `config.settings.wake_word`.

## Computer control

`tools/computer_use.py` — real mouse/keyboard/screen control via
`pyautogui`, gated by macOS Accessibility + Screen Recording permissions
(separate from mic permission). Non-consequential actions (`computer_see`,
`computer_locate`, `computer_click`, `computer_type`, `computer_press_key`)
run immediately; anything that sends/pays/deletes/submits must go through
`computer_confirm_action`, which requires the model to have already
described the action and gotten an explicit "yes" in a later message —
same two-step pattern as login/email. The whole family is blocked from
scheduled/unattended runs regardless. Every action is logged (audit +
observability) and saves its own screenshot for a visual record. See that
file's module docstring for the full safety model.

## Scheduler

Two implementations of the same polling logic — see the "Lifecycle" note
below for why this is a known duplication risk, not by design.

## UI

- `app.py` — Streamlit chat interface, browser-based voice (Web Speech
  API) with a passive/active wake-word mode, multi-device conversation
  sharing.
- `pages/1_Dashboard.py` — live status: tool count, recent activity (from
  the audit log), scheduled tasks, standing rules, remembered facts, tools
  grouped by permission level.
- `ui/menu_bar.py` — native menu-bar app (rumps): status icon reflecting
  current state (idle/listening/thinking/computer-use-active), native
  notifications, click-to-ask, and the background voice + scheduler loops.

## Logging

Two separate, deliberately distinct logs:

- **`agent/audit.py`** — the security/action record: every tool call,
  its input, its result, its permission label. This is what the user
  reviews to answer "what did Jarvis actually do." Unchanged by Phase 2.
- **`agent/observability.py`** — structured (JSON-lines, to stderr)
  application diagnostics: request started/completed/failed, model
  selected, agent iteration, tool selected/started/completed/failed —
  everything correlated by `request_id`. This is what you'd grep through
  to answer "why did that fail" or "how long did that take." Never logs
  secrets; user content is capped to a short preview, not dumped in full.

## Configuration

`config/settings.py` — a single typed, frozen `Settings` dataclass, loaded
once from environment variables (with defaults) via `Settings.load()`.
Covers model selection, agent-loop limits, scheduler timing, voice
settings, API timeouts/retries, and debug mode. Secrets (API keys) are
explicitly NOT here — those stay in `agent/secrets.py`, which checks the
macOS Keychain first and falls back to `.env`.

## Known lifecycle risks (found during the Phase 2 review, not fixed —
documented per that review's scope)

1. **Duplicate scheduler**: `agent/scheduler_daemon.py` (standalone) and
   `ui/menu_bar.py`'s built-in scheduler loop both poll and execute the
   same scheduled tasks independently. Running both at once means every
   scheduled task fires twice. Don't run the standalone daemon while the
   menu-bar app (or its LaunchAgent) is active.
2. **Playwright profile contention**: `tools/browser.py` keeps one
   module-level Playwright browser context per *process*. The Streamlit
   app and the menu-bar app are separate processes that both import this
   module and point at the same on-disk Chrome profile
   (`~/Library/Application Support/CampusPilot/chrome-profile`). If both
   try to drive the browser at the same moment, they're two independent
   Chrome instances contending for one profile directory. Not currently
   locked or coordinated.
