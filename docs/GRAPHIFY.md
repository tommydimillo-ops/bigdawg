# Graphify (development tooling — not a Jarvis subsystem)

An optional, local, developer-facing structural code graph of this repo.
Evaluated and approved as of **Graphify G0** (2026-08-19), then given
four narrow, read-only Jarvis tools in **Graphify G1** (2026-08-19,
same day). The `graphify`/`graphifyy` package/CLI itself is still **not**
part of Jarvis's runtime and never will be invoked from it — see "G1"
below for exactly what *is* now wired in (a small standard-library
reader over the generated graph *data*, nothing more).

**What Graphify is not**: not Jarvis's source of truth, not a
permission/autonomy authority, not a runtime dependency (the
`graphifyy` package is not in `requirements.txt` and never will be —
G1's reader only parses the JSON `graphify extract` already wrote to
disk), not an orchestrator, not an MCP integration, not a Claude Code
hook. Direct source inspection remains authoritative whenever accuracy
actually matters — see Limitations below, and G1's
`source_verification_required` field, which encodes exactly that rule
into every tool result.

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

## G1 — four narrow, read-only Jarvis tools

`agent/code_graph.py` reads `graphify-out/graph.json` directly with the
standard library (`json`, `os`) — it never imports `graphifyy`, never
invokes the `graphify`/`graphify-mcp` executables, and the only
subprocess call anywhere in the module is a fixed-argv, `shell=False`
`git` invocation (`git rev-parse HEAD` / `git status --porcelain
--untracked-files=no`, both bounded by a 5-second timeout and pinned to
this repo's root) used solely to decide whether the on-disk graph is
still trustworthy. The graph itself is treated purely as **data** that
`graphify extract` already wrote — G1 never re-runs extraction, never
starts a process, never watches anything.

Four `tools/registry.py` ToolSpecs (`tools/schemas/graphify.py`), all
`permission_level=0`, `side_effect=False`, `unattended_allowed=True`,
`parallel_safe=True`, no live confirmation — the same risk class as
`get_system_status`:

- **`code_graph_status`** — availability/freshness report. No input.
- **`search_code_graph`** — deterministic exact/prefix/substring symbol
  search, capped at 20 results, explicit ambiguity reporting (never
  silently resolves a bare name shared by multiple nodes).
- **`analyze_code_impact`** — bounded reverse-dependency traversal from
  an exact `node_id` (max depth 3, max 100 results), direct vs indirect
  and EXTRACTED vs INFERRED preserved.
- **`find_code_path`** — bounded BFS shortest path between two exact
  node IDs (max depth 10), one path returned, or a clean "no path".

None accept a caller-supplied filesystem path, CLI subcommand, or raw
query string of any kind — the graph location is always resolved
internally to `graphify-out/` under the repo root. There is no fifth,
generic "run a graph command" tool.

**Staleness is enforced, not advisory.** `code_graph_status` always
reports the graph's true state (`fresh` / `stale` / `unavailable` /
`invalid`); the other three tools refuse to run any traversal unless
the graph is exactly `fresh` — built at the current git HEAD **and** a
clean tracked working tree (an untracked file, including
`graphify-out/` itself, never counts as "dirty"; only tracked
modifications do). A `stale`/`unavailable`/`invalid` graph makes all
three return the same small structured refusal shape instead of doing
any work. Nothing in G1 auto-rebuilds a stale graph — that stays a
manual, deliberate step (`graphify extract . --code-only` then
`graphify cluster-only . --no-label`, as above).

**Never authoritative.** Every result carries `authoritative: false`
and the same `limitations` list as G0's findings (A/B/C above). A
result touching `tools/registry.py`, `ToolSpec`, `tools/schemas/`,
`agent/autonomy.py`, or anything permission/credential-related
additionally carries `source_verification_required: true` — not a
block, just an explicit signal that direct source reading is mandatory
before acting on that particular conclusion. Impact/path results are
phrased as structural relationships ("structurally related", "not
proof of runtime behavior") — never as a guarantee of actual runtime
effect.

## Not enabled (deliberately, as of G1)

- `graphify-mcp` (a binary bundled with the package) — not started, not
  registered anywhere.
- No Graphify MCP server integration.
- `graphify claude install` / `graphify install` — never run. No
  `CLAUDE.md` section, no `PreToolUse` hook (soft-nudge or `--strict`),
  written by Graphify.
- No `graphify hook install` (post-commit/post-checkout auto-rebuild).
- No `graphify watch` background process.
- No semantic/document/PDF/image extraction (`--code-only` only).
- No automatic graph regeneration of any kind (G1 only reads whatever
  is already on disk; a stale graph is refused, never silently rebuilt).
- No permission/autonomy/routing decision is ever based on graph
  content — `agent/autonomy.py` and `tools/registry.py` are untouched
  and remain the actual enforcement points.
- `graphifyy` is not a CampusPilot runtime dependency and is not in
  `requirements.txt` — G1 parses graph.json with the standard library
  only.
