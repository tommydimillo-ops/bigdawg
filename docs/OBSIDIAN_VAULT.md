# Obsidian vault integration

An optional, human-readable knowledge layer Jarvis can read, search, and
write to — separate from `agent/memory/` (Jarvis's own structured,
typed memory store, which this doesn't touch or replace). A vault is
just a directory of Markdown files; there's no Obsidian API or server
involved, `agent/obsidian_vault.py` reads/writes the files directly, the
same way Obsidian's own desktop app does.

## Setup

Point Jarvis at a real Obsidian vault (or any folder of Markdown files)
via the `OBSIDIAN_VAULT_PATH` environment variable (`.env` or real env):

```
OBSIDIAN_VAULT_PATH=/Users/you/Documents/YourVault
```

If unset, Jarvis falls back to a project-relative `./JarvisVault`
directory if one already exists there. If neither resolves to a real
directory, the integration is simply unavailable — every tool call
returns a clear "not configured" message, nothing raises or breaks the
rest of Jarvis.

**Never commit vault contents to git.** The default `JarvisVault/` at
the project root is already excluded via `.gitignore`; if you point
`OBSIDIAN_VAULT_PATH` somewhere else, keep it outside the repo entirely.

## Recommended structure

Not enforced by code — a human organizing convention Jarvis is nudged
toward via the tool descriptions, not a technical constraint:

```text
JarvisVault/
├── Memory/          Long-lived personal facts/context worth having in
│                     readable form (distinct from agent/memory/'s own
│                     structured store — this is the human-browsable copy).
├── Knowledge/        Reference material, notes on topics, how-tos.
├── Projects/         Notes tied to a specific project or piece of work.
├── Conversations/    Saved summaries of noteworthy conversations.
├── Agents/           Working notes from coworker agents (research
│                     findings, etc.) once anything writes there.
└── README.md         Whatever you want it to say — Jarvis never touches it.
```

Subfolders are created automatically the first time a note is written
into them — nothing is scaffolded in bulk up front.

## What Jarvis can do

Three tools, all in `tools/schemas/obsidian.py`, calling
`agent/obsidian_vault.py`:

- `read_obsidian_note(path)` — read a note by path.
- `search_obsidian_vault(query)` — deterministic keyword-overlap search
  across every `.md` file in the vault (no embeddings, consistent with
  `agent/memory/`'s own retrieval philosophy).
- `write_obsidian_note(path, content, append=False)` — create or update
  a note. Only used when explicitly asked to save/write something down
  in Obsidian, never proactively (see each tool's own description). Every
  write is checked against `agent/memory/safety.py`'s existing
  credential/injection detection first — refused, not silently redacted,
  if it looks like a secret.

Not wired into automatic system-prompt context injection the way
PATTERN memories are — purely tool-call-driven, so nothing about
`agent/executor.py`'s or `agent/brain.py`'s core loop changes.

## Future extension point

Not built yet, deliberately: a dedicated `ObsidianAgent` (mirroring
`agent/agents/memory.py`, which is a thin `Agent` wrapper around
`agent/memory_agent.py`'s plain functions). `agent/obsidian_vault.py`'s
functions are already shaped for that — a future agent would wrap them
the same way, with no change needed here.
