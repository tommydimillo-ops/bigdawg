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

## Known incremental-extraction limitation (verified — Graphify G1.1)

`graphify extract . --code-only` (the rebuild command above) normally runs
**incrementally**: it reads its on-disk manifest/AST cache
(`graphify-out/cache/`) and only re-parses files that changed since the
last extraction, merging the result into the existing graph rather than
re-parsing everything.

A controlled audit (two isolated local clones, one reproducing the exact
incremental transition, one a clean extraction with no prior cache at
the same commit) confirmed a real, narrow completeness gap in Graphify
0.9.47's incremental mode: when a **newly-added or newly-changed**
Python file imports a **named symbol** from an **old/unchanged (cached)**
module, the direct per-symbol `imports` edge to that symbol can be
silently missing — even though every function/class/method node is
still extracted correctly and every other structural relation
(`contains`, `calls`, `inherits`, `method`, ...) is unaffected. Confirmed
concretely for this repo: after adding `tools/schemas/graphify.py`
(which imports `ToolSpec`/`register` from the already-existing
`tools/registry.py`), an incremental rebuild produced the coarse
`tools_schemas_graphify --imports_from--> tools_registry` edge but
**not** the finer `tools_schemas_graphify --imports--> tools_registry_toolspec`
/`tools_registry_register` edges that a clean full extraction produces
at the identical commit. A second, independent instance of the same
pattern was confirmed for `tests/test_graphify_tools.py` importing
`_run_tool` from the unchanged `agent/executor.py`. Four unrelated,
pre-existing `tools/schemas/*.py` modules were checked as a control and
showed zero difference between incremental and full extraction,
confirming the gap is specific to newly-added files referencing
unchanged ones, not a general graph-wide inconsistency.

Likely mechanism (from reading, not modifying, the installed
`graphify` package source): incremental extraction has a documented
"unchanged corpus" context-sharing mechanism that gives several
call-resolution passes (direct calls, indirect calls, member-call
resolution) visibility into files outside the current incremental
batch, specifically so a changed caller can still bind to an unchanged
callee. The plain `from X import Y` symbol-binding pass that produces
direct `imports`-relation edges is not among the passes wired into that
mechanism — its lookup table is built only from the files in the
current incremental batch, so a symbol defined only in an unchanged
file is invisible to it.

**Important terminology clarification.** `code_graph_status`'s `fresh`
state (`agent/code_graph.py`) means exactly two things: the graph's
`built_at_commit` equals the current git HEAD, and the tracked working
tree is clean. **It does not, and cannot, guarantee**:
- that every Graphify static-analysis edge is complete or correct, or
- that the graph was produced by a clean full extraction rather than an
  incremental one.

A graph can be simultaneously `fresh` (by the definition above) and
have the exact narrow gap described here. This is not a new/different
guarantee that needs to be added to `agent/code_graph.py` — the module
already marks every result `authoritative: false` with the known
`limitations` list, precisely because Graphify's output, however it was
produced, is never trusted as ground truth. This section exists so a
human (or a future session) understands *why* `fresh` doesn't mean
"structurally complete," not to imply the staleness check itself is
broken.

### Recommended precision rebuild workflow

The two-command rebuild in "Rebuilding the graph" above is fine for
everyday use and is what `fresh` actually validates against. When
precision on cross-file import edges specifically matters (e.g. right
after adding a new file that imports from existing modules, or before
relying heavily on `analyze_code_impact`/`find_code_path` results),
use this safer, verified full-rebuild sequence instead of trusting the
default incremental merge:

1. Ensure the tracked repository is clean (`git status`).
2. Move the existing `graphify-out/` to a temporary backup **outside**
   the repository (e.g. `mv graphify-out /tmp/graphify-out-backup-$(date +%s)`)
   — never `rm -rf graphify-out` blindly; keep the old graph until the
   new one is validated.
3. Run the normal two commands from a directory with no `graphify-out/`
   present:
   ```bash
   graphify extract . --code-only
   graphify cluster-only . --no-label
   ```
4. Validate before trusting the result:
   - `built_at_commit` equals current `git rev-parse HEAD`
   - `code_graph_status` reports `fresh`
   - a secret/private-data scan of the generated output is clean
   - `graphify-out/` still appears in `git status` as ignored, not
     tracked or staged
   - the tracked repository is still clean
5. Only after that validation passes, delete the temporary backup from
   step 2.

**On `--force`**: Graphify 0.9.47's `graphify extract --force` is
documented as skipping "the incremental manifest gate and semantic
cache reads." This audit did not establish whether `--force` also
bypasses the on-disk AST cache (`graphify-out/cache/ast/`) — the
documented scope names the manifest gate and semantic cache
specifically, not the AST cache. Until that's separately verified,
**a genuinely empty `graphify-out/` directory (steps 2-3 above) remains
the verified, unambiguous way to get a clean full extraction** — don't
substitute `--force` for it based on the flag name alone.

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

**D. Incremental extraction can miss cross-file import edges to
unchanged files** — see "Known incremental-extraction limitation"
below for the full, separately-verified detail (a different kind of
finding than A-C: about the rebuild *process*, not Graphify's
structural-analysis accuracy in general).

**Because of A, B, and D specifically, Graphify must never be trusted
alone for**: which ToolSpec registers which handler, permission-level or
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
