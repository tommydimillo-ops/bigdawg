# CLAUDE.md — Jarvis project instructions

Permanent context for any Claude Code session working on this repo. Kept
concise on purpose — depth lives in `ARCHITECTURE.md`, current state lives
in `HANDOFF.md`, history lives in `CHANGELOG.md`/`SESSION_LOG.md`. Don't
duplicate those here; reference them.

## What Jarvis is

Jarvis (repo name `CampusPilot`, in-product name `Jarvis`) is a personal AI
assistant running locally on the user's (Tommy's) Mac — voice-first native
menu-bar app plus a Streamlit web chat, both backed by one shared agent
core. It can hold a conversation, browse the web, control the screen,
manage reminders/calendar/notes, remember facts and standing rules about
the user, run scheduled daily tasks, and hand specialized work
(research, memory) to dedicated coworker agents.

## Project goals

Build Jarvis out as a modular, model-agnostic personal AI operating
system — not one giant monolithic prompt. Concretely, in priority order
as of this writing:
1. Reliability and safety of what already works (permission gating,
   voice-misfire protection, cost visibility) over adding new surface
   area.
2. Extend coworker agents (`agent/agents/`) toward real capability
   (CodingAgent is currently a stub) without weakening the sandboxing
   Phase 8 built (subprocess isolation, usage limits).
3. Keep cost and behavior observable — the user has hit real, confusing
   API cost surprises before (Phase 8 exists because of this); don't
   regress that visibility when adding features.

See `ROADMAP.md` for the actual prioritized backlog.

## Current architecture (summary — see ARCHITECTURE.md for full detail)

One orchestrator (`agent/executor.py`'s `execute_task_stream`), called by
three entry points (`app.py` Streamlit, `ui/menu_bar.py` native voice app,
`agent/scheduler_daemon.py`). Claude primary / OpenAI fallback
(`agent/model_router.py`, currently static, not task-routed). 53 tools in
one registry (`tools/registry.py`), permission-leveled 0-5. Structured
memory (`agent/memory/`). Skills = data-only prompt-context bundles
(`agent/skills/`, `SKILL.md` files). Coworker agents run in real,
timeout-killable OS subprocesses (`agent/agents/`, Phase 7/8). No
database server — flat JSON files under
`~/Library/Application Support/CampusPilot/`. No MCP integration exists.

## Major components and responsibilities

| Component | Responsibility |
|---|---|
| `agent/executor.py` | The orchestrator — the only thing that actually dispatches a tool call |
| `agent/brain.py` | System prompt + tool list assembly |
| `agent/model_router.py` | Which provider/model for this attempt (static today) |
| `agent/autonomy.py` | Confirmation-required-or-not decision (never which tools are *allowed* — that's `tools/registry.py`) |
| `tools/registry.py` | Single source of truth for every tool: schema, permission level, handler |
| `agent/memory/` | Structured, typed, superseding personal memory store |
| `agent/skills/` | Data-only prompt-context bundles, matched by keyword overlap |
| `agent/agents/` | Coworker agents (research, memory, coding-stub, qa) + subprocess-isolated execution |
| `agent/usage.py` | Per-call token/cost accounting + per-request safety limits |
| `agent/planner.py` | Structured multi-step plans for complex requests (never executes) |
| `voice/listen.py` + `voice/speak.py` | Native mic capture, wake-word detection, sentence-chunked TTS |
| `agent/quiet_mode.py` | Indefinite/timed suppression (quiet/sleep/off), shared across UIs |
| `pages/1_Dashboard.py` | Live status + cost dashboard (Streamlit page) |

## Agent architecture

See `ARCHITECTURE.md` §4. Short version: coworker agents are a
*delegation* mechanism (main loop hands off a task via one tool call),
not a second copy of the main loop. Real execution is subprocess-isolated
with a genuine OS-level timeout kill. `MAX_AGENT_DEPTH = 1` — an agent
can never consult another agent. Only `research` and `memory` do real
work today; `coding` and `qa` (mostly) are deferred/narrow.

## Model routing strategy

**Implemented today**: static — Claude primary, OpenAI fallback only on a
live failure. Separate fixed models for vision/transcription/TTS/planning
(each a hardcoded per-call-site assignment, not a routed decision).

**Intended, NOT yet implemented**: task-based routing — a strong coding
model for software engineering, a strong reasoning model for planning,
a vision-capable model for visual understanding, a voice-capable model
for speech, a cheap/fast model for simple classification/routing/
subtasks. `agent/model_router.py`'s `select()` already accepts an unused
`context` parameter reserved for this. **Do not hardcode task-based
routing logic unless you're actually implementing this properly** — if
you're just documenting intent, it belongs in `ROADMAP.md`, not baked
into `select()`'s body as a half-finished branch.

## Memory architecture

See `ARCHITECTURE.md` §6. One typed `Memory` model, one manager
(`agent/memory/manager.py`), file-backed, same-subject supersession,
deterministic relevance retrieval (no embeddings/vector search — this is
intentional, not a gap to fill casually). `agent/lessons.py`/
`agent/patterns.py`/`agent/memory_agent.py` are thin backward-compatible
wrappers over it, not separate stores.

## Skills/plugins architecture

See `ARCHITECTURE.md` §8. Skills are data (`SKILL.md`: frontmatter +
Markdown instructions), never code — no execution path from a skill file
to Python exists or should ever exist. There is no separate "plugin"
system; skills are the extension point. A skill attaching to a request
changes prompt context only — it can never itself bypass
`tools.registry`/`agent.autonomy`.

## MCP / tool integrations

No MCP client/server exists. The tool-integration point is
`tools/registry.py` — see `docs/ADDING_A_TOOL.md` for the exact steps to
add one. If MCP integration is ever built, it should register tools
*through* this same registry, not around it (same principle
`agent/cowork_gateway.py`'s docstring states for a future Cowork
integration).

## Important coding conventions

- **No comments explaining WHAT code does** — names should do that.
  Comments here explain WHY: a non-obvious constraint, a workaround for a
  specific bug, a decision that would otherwise look arbitrary. This
  codebase's existing comments are almost all load-bearing "why" —
  preserve that density and style, don't strip them or add "what"
  comments.
- **Mock at the external-call boundary in tests** — the real Anthropic/
  OpenAI client, `subprocess.run`, a real network/file-system call.
  Never mock internal application logic just to make a test pass.
- **Deterministic, non-LLM decision engines for routing/permission
  logic** — `agent/autonomy.py`, `agent/delegation.py`,
  `agent/skills/router.py`, `agent/agents/router.py` are all plain
  keyword/threshold logic, on purpose. Never let a model call decide a
  permission or routing outcome.
- **File-backed stores**: tmp-file-then-`os.replace`, `fcntl.flock` for
  read-modify-write cycles multiple processes touch. Follow the existing
  pattern (`agent/execution_history.py`, `agent/usage.py`) rather than
  inventing a new persistence style.
- **One registry per registerable thing** (`tools/registry.py`,
  `agent/skills/registry.py`, `agent/agents/manager.py`'s `_REGISTRY`) —
  register once at import time via a side-effect import
  (`import tools.schemas`), never a growing if/elif chain.
- **Additive, non-invasive integration** — extend via a new optional
  parameter or a new module before rewriting an existing core loop.
  `agent/executor.py` in particular has been extended by nearly every
  phase without its core loop shape changing.
- **A tool handler's signature is `Callable[[dict], str]`** — deliberately
  narrow. Don't widen it to pass extra context through; use the
  `agent/request_context.py` contextvar pattern instead (see how
  `consult_coworker_agent` recovers the current `request_id`).

## Important security rules

- **Never commit, log, or write into any of these Markdown files** an
  API key, password, OAuth token, or other credential. Secrets live in
  the macOS Keychain via `agent/secrets.py`, with `.env` as fallback —
  never in `config/settings.py` (that module is explicitly non-secret
  config only).
- **Never let a model's own judgment be the enforced safety boundary.**
  A skill's instructions, a coworker agent's task text, or anything in a
  system prompt can say whatever it wants — the actual gate is always
  code (`tools.registry` permission levels, `agent.autonomy`'s decision,
  the hard `requires_live_confirmation`/`unattended_allowed` flags).
- **Voice input is not automatically trustworthy.** A live incident
  (background TV audio transcribed as commands) led to
  `add_reminder`/`open_browser`/`consult_coworker_agent` always
  requiring confirmation when `source="voice"`, regardless of autonomy
  level. If you add a new tool with a real-world side effect that could
  plausibly be triggered by a misheard wake word, consider whether it
  needs the same treatment (`agent/autonomy.py`'s
  `_VOICE_ALWAYS_CONFIRMS`).
- **Never log secrets or full user content** in
  `agent/observability.py` — preview/truncate, as the existing pattern
  does.
- **A coworker agent's subprocess (`agent/agents/worker.py`) writes only
  one JSON line to stdout** — don't add a `print()` anywhere on that
  path; it would corrupt the parent's parse of the result.

## Environment / setup

- Python venv at `.venv/`; `pip install -r requirements.txt`.
- API keys: `python -m tools.manage_secrets` (Keychain) or `.env`
  (`ANTHROPIC_API_KEY`, `OPENAI_API_KEY`).
- Saved logins for `fill_login`/`confirm_login`: `python -m
  tools.manage_logins add` — CLI only, never via chat.
- Run the Streamlit chat: `streamlit run app.py`.
- Run the native menu-bar app: `open CampusPilotAgent.app` (a py2app
  alias-mode bundle — symlinks to this project's live source, so code
  changes take effect on the next restart without rebuilding) — or
  `python -m ui.menu_bar` directly, though that loses the app's own TCC
  identity for microphone/Speech permissions.
- Rebuild the app bundle after a structural change (new dependency, new
  entry-point behavior): `python setup_app.py py2app -A`.
- **Don't run `agent/scheduler_daemon.py` at the same time as the
  menu-bar app** — both poll and execute the same scheduled tasks
  independently; running both fires every scheduled task twice.

## How to test

```bash
python -m unittest discover -s tests -t . -v      # full suite
python -m unittest tests.test_name -v             # one module
```

**The `-t .` flag is required, not optional.** Without it, `discover`
imports every test file as a bare top-level module instead of as a
`tests.*` submodule, which means `tests/__init__.py` — and everything it
installs — never runs. A real run under the old, `-t`-less command wrote
into six real files under the live
`~/Library/Application Support/CampusPilot`, including the real
`memory.json` and a real macOS Keychain entry (Phase 9 reliability audit,
2026-08-23). Running a test file directly as a script
(`python tests/test_x.py`) has the same gap and is not a
safety-guaranteed invocation either. Always use one of the two commands
above.

1417 tests as of this writing, all passing. `tests/__init__.py` +
`tests/_safety.py` install a package-level safety bootstrap before any
test module is imported: a disposable per-process temp directory that
every production persistent-store path constant is redirected into, an
external-network firewall at the stdlib `socket` layer (loopback only;
everything else raises `tests._safety.ExternalNetworkBlocked` before DNS
or a real connection), a secondary `httpx` tripwire, and
browser/computer-use tripwires. Full detail: `ARCHITECTURE.md` §18. This
does **not** replace per-file `setUp`/`tearDown` redirects — keep adding
those for any new file-backed store (a new `SOMETHING_FILE` module-level
path) **and** add it to `tests/_safety.py`'s central redirect list; a
past gap here (executor-integration tests isolating
`HISTORY_FILE`/`STATE_FILE` but not the newer `USAGE_FILE`) caused real
test-artifact pollution of the live user's actual usage history before
the central bootstrap existed. If a function defaults a path parameter
directly to a module-level constant (`def f(path=SOME_FILE)`), that
default is bound at function-definition time and will NOT pick up a
later redirect of `SOME_FILE` — this bit `agent/history_store.py` and
`agent/personal_context.py` for real during the S1 pass; the fix is
always to default to `None` and read the constant inside the function
body instead.

For a live (non-mocked, real API) smoke test, prefer the cheapest real
path that proves the thing works — e.g. an unregistered-agent-name
subprocess call proves the worker/subprocess plumbing without needing a
real model call. Be mindful of real API cost; this project's whole
Phase 8 exists because of a real, confusing cost overrun. For a real
Keychain smoke test specifically, use `python -m
tools.keychain_smoke_test` (opt-in, never run by the canonical suite or
CI, uses its own service namespace and synthetic credentials only).

## Rules for modifying existing architecture

1. **Do not rewrite a working subsystem to add one feature.** Extend it.
   Every phase in this project's history (see `CHANGELOG.md`) added to
   `agent/executor.py`'s loop without changing its fundamental shape.
2. **Preserve existing tests; add new ones for new behavior.** Run the
   full suite before considering a change done.
3. **New tools go through `tools/registry.py`** — never a special-cased
   dispatch path. Exception already accepted: `agent/research_agent.py`'s
   own tiny internal loop (2 tools, not registered in the main registry,
   documented as a deliberate, narrow exception).
4. **New coworker-agent capability**: real execution must go through
   `agent/agents/manager.py`'s `execute_agent()` (subprocess-isolated),
   not by calling an agent's `.execute()` directly from a tool handler —
   that was a real bug found and fixed in Phase 8 (the live tool path had
   no timeout protection at all until this was corrected).
5. **Before adding a new persistent store**, check whether an existing
   one already fits (`agent/memory/` for personal facts,
   `agent/execution_history.py` for past-request metadata,
   `agent/usage.py` for cost). Most "I need to remember X" needs already
   have a home.
6. **Live app changes need a restart to take effect** — the running
   `CampusPilotAgent.app`/Streamlit process doesn't hot-reload. Restart
   and verify the log (`logs/menubar.err.log`) shows a clean startup
   before considering a fix deployed.

---

## NEW SESSION PROTOCOL

Whenever a new Claude Code session begins on this project:

1. Read this file (`CLAUDE.md`).
2. Read `HANDOFF.md`.
3. Inspect the current repository state (don't assume the file tree
   matches what `HANDOFF.md`/`ARCHITECTURE.md` describe — verify).
4. Check `git status` (this is a git repo) — uncommitted changes from a
   prior session are a real, common state here; don't assume a clean
   tree.
5. Determine whether `HANDOFF.md` matches the actual code. If there's a
   discrepancy, **trust the actual code** and update the documentation
   to match — don't silently proceed on stale assumptions, and don't
   silently leave the docs wrong either.
6. Do not assume any previous conversational context exists beyond what's
   written in these files.
7. Before making a major change, state your understanding of the current
   state in a sentence or two — this is a checkpoint for the user to
   correct you, not a formality.
8. Continue from the current project state. Do not rebuild or rewrite an
   existing working system to accomplish a new task — see "Rules for
   modifying existing architecture" above.

## SESSION END PROTOCOL

Before a substantial Claude Code session on this project ends:

1. Update `HANDOFF.md` — current status, what changed, what's next.
2. Update `CHANGELOG.md` if meaningful changes were made (not every
   trivial fix needs an entry — use judgment matching that file's
   existing granularity).
3. Update `SESSION_LOG.md` with this session's entry.
4. Update `ARCHITECTURE.md` if the actual architecture changed (a new
   subsystem, a changed execution flow) — not for a bug fix that doesn't
   change the shape of anything.
5. Update `ROADMAP.md` if something moved between Completed/In Progress/
   Next/Future.
6. Record any unresolved bugs/issues in `HANDOFF.md`.
7. Record exactly what should happen next in `HANDOFF.md` — specific
   enough that a fresh session with no other context could start
   immediately.
8. Verify the documentation actually matches the code before finishing —
   re-read the diff of what changed this session against what you wrote.
9. **Never write secrets, API keys, passwords, tokens, or credentials
   into any of these files.**
