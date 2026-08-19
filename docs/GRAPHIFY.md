# Graphify (development tooling — not a Jarvis subsystem)

An optional, local, developer-facing structural code graph of this repo,
used by a Claude Code session working on Jarvis to answer "what calls
what" / "what depends on X" faster than repeated grep/Read round-trips.
Evaluated and approved as of **Graphify G0** (2026-08-19). It is **not**
part of Jarvis's runtime — nothing in `agent/`, `tools/`, or any other
Jarvis source path depends on it, imports it, or calls out to it.

**What Graphify is not**: not Jarvis's source of truth, not a
permission/autonomy authority, not a runtime dependency, not an
orchestrator, not (yet) an MCP integration, not (yet) a Claude Code
hook. Direct source inspection remains authoritative whenever accuracy
actually matters — see Limitations below.

## Package and installation

- Package: `graphifyy` (PyPI, double-y) — GitHub: `Graphify-Labs/graphify`.
- CLI command it installs: `graphify`.
- Currently validated version: **0.9.47**.
- Installed via `uv tool install graphifyy` — an isolated tool
  environment (`~/.local/share/uv/tools/graphifyy/`), **not**
  `~/CampusPilot/.venv`. Not in `requirements.txt`. Nothing about
  CampusPilot's own runtime dependency set changes because of this.
- Requires Python 3.10+; `uv`/`pipx` are the recommended isolation
  methods so the package's own dependencies (tree-sitter, networkx,
  numpy, rapidfuzz, ...) never mix with Jarvis's runtime environment.

## Rebuilding the graph

From the repo root, with the isolated `graphify` CLI on `PATH`:

```bash
graphify extract . --code-only
graphify cluster-only . --no-label
```

- `--code-only` restricts extraction to local, deterministic tree-sitter
  AST parsing of code files only — no LLM/API call, no network access,
  no API key needed. This is deliberate: Jarvis's own docs/notes/PDFs
  are never worth the cost or the data-handling question of a semantic
  extraction pass for a pure code-structure graph.
- `cluster-only . --no-label` regenerates `GRAPH_REPORT.md` (community
  detection, god-node ranking, surprising-connection detection) without
  invoking `cluster-only`'s *default* behavior of naming communities via
  an LLM backend. `--no-label` keeps that step local too.
- Together these are the only two commands ever run against this repo.
  Nothing else (`graphify install`, `graphify hook install`, `watch`,
  MCP serving) has been run — see "Not enabled" below.

## Output

Written to `graphify-out/` at the repo root: `graph.json` (the full
queryable graph), `graph.html` (interactive force-directed
visualization), `GRAPH_REPORT.md` (god nodes, surprising connections,
per-community node lists), `manifest.json` (portable, relative-path-keyed
re-extraction cache), and a small local `cache/` (AST cache for faster
re-extraction).

**`graphify-out/` is gitignored, not committed.** Regenerating it is
free (local, ~12 seconds, zero API cost), and this repo is public — a
committed graph would publish a detailed, algorithmically-ranked map of
exactly where this project's permission/autonomy/credential-separation
logic lives (god nodes, cross-community bridges), which is a real if
modest concern for a security-conscious personal assistant, on top of
being a large (3-4MB), wholesale-rewritten, low-diff-signal blob on
every future extraction. Revisit this decision if the project ever
becomes a multi-developer repo.

**Staleness**: `graph.json` embeds `built_at_commit` (the git HEAD it
was built from). Compare against `git rev-parse HEAD` before trusting
the graph reflects current code; if they differ, rebuild.

**Ignore behavior**: Graphify reads `.gitignore` and `.git/info/exclude`
automatically; nothing in this repo needed a separate `.graphifyignore`
as of G0 — `.gitignore` alone already excluded every sensitive/generated
path present (`.env`, `.venv/`, `__pycache__/`, `JarvisVault/`, `logs/`,
build artifacts). Verified directly: zero secrets, zero ignored/private
paths, and zero meaningful absolute-path leakage appeared in any
generated file during G0's validation pass.

## Verified limitations (read before trusting a query)

**A. Same-basename/module-name collision.** At least one confirmed
false-positive impact edge: a query for what depends on `tools/registry.py`
included `pages/1_Dashboard.py`, attributing it to the wrong import —
the actual import at that location is `from agent.skills.registry import
list_skills` (a different `registry.py`, in a different package).
Graphify appears to resolve same-basename modules ambiguously in at
least this case. Confirmed specific to the name collision, not a
general line-tracking bug (a parallel query against `agent/autonomy.py`,
which has no basename collision, resolved correctly).

**B. `register(ToolSpec(..., handler=...))` wiring is not reliably
modeled.** No edge anywhere in the graph represents the literal
mechanism that wires every one of Jarvis's tools together — the
constructor-keyword-argument pattern `register(ToolSpec(name=...,
handler=some_function, ...))`. This is missing for all 14
`tools/schemas/*.py` modules, not just one. (By contrast,
`agent/verification.py`'s dict-literal `_VERIFIERS = {"name": fn}`
registration pattern for that same tool *was* correctly picked up as an
inferred edge — the gap is specific to constructor-kwarg registration,
not registration patterns generally.)

**C. Ambiguous short/bare symbol names** can resolve to an unrelated
same-named symbol elsewhere in the codebase (e.g. a bare `explain
"speak"` query matched an unrelated wrapper method rather than the
intended `voice/speak.py` module) rather than erroring — though Graphify
does correctly refuse to guess and asks for disambiguation when two
candidates are equally strong (confirmed with `remember`, which
genuinely exists in both `agent/memory_agent.py` and
`agent/memory/manager.py`). Prefer fully-qualified paths or exact node
IDs (from a prior `explain`/`path` result) over single bare words.

**Because of A and B specifically, Graphify must never be trusted alone
for**: which ToolSpec registers which handler, permission-level or
autonomy decisions, or any credential/security-boundary question. Those
questions are answered by `tools/registry.py`, `agent/autonomy.py`, and
direct source reading — Graphify is a navigation aid on top of that, not
a replacement for it.

## Not enabled (deliberately, as of G0)

- `graphify-mcp` (a binary bundled with the package) — not started, not
  registered anywhere.
- No Graphify MCP server integration.
- `graphify claude install` / `graphify install` — never run. No
  `CLAUDE.md` section, no `PreToolUse` hook (soft-nudge or `--strict`),
  written by Graphify.
- No `graphify hook install` (post-commit/post-checkout auto-rebuild).
- No `graphify watch` background process.
- No semantic/document/PDF/image extraction (`--code-only` only).
- No Jarvis runtime integration: no ToolSpec registered, no
  `agent/executor.py` change, no coworker agent, no change to model
  routing/autonomy/permissions/OpenClaw/Obsidian.

A future **G1** may expose a narrow, read-only Jarvis `ToolSpec` over
this graph (through `tools/registry.py`, like every other tool — never
a parallel dispatch path), but that is a separate, not-yet-approved
decision. Nothing in G0 assumes or depends on G1 happening.
