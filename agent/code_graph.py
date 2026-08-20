"""Read-only reader for the locally-generated Graphify output
(graphify-out/graph.json), backing Jarvis's four narrow G1 code-graph
tools (tools/schemas/graphify.py). See docs/GRAPHIFY.md for the full
picture -- this module is the implementation of that document's "G1"
section.

STRUCTURAL BOUNDARY, ENFORCED BY WHAT THIS FILE DOES NOT DO: it never
imports `graphifyy`, never invokes the `graphify`/`graphify-mcp`
executables, and never shells out to anything except a fixed,
argv-list `git` command (no shell=True, no user-controlled fragments)
used only to determine the current commit and tracked working-tree
cleanliness -- the one piece of "is this graph still trustworthy" that
can't be answered from the graph file alone. Graphify remains external
developer tooling (installed via `uv tool install graphifyy`, never a
CampusPilot runtime dependency); this module treats graphify-out/
purely as DATA, read with the standard library only (json, no graphifyy
import anywhere in this file or its callers).

NEVER AUTHORITATIVE. Every result this module returns carries
`authoritative: False` and a short `limitations` list -- see
`_KNOWN_LIMITATIONS` below, verified during Graphify G0's evaluation
pass (docs/GRAPHIFY.md has the full detail, including how each was
confirmed against real source). Results touching a security-relevant
area of the codebase (tools/registry.py, ToolSpec, tools/schemas/,
agent/autonomy.py, permissions/credentials) additionally carry
`source_verification_required: True` -- a signal to Jarvis that direct
source inspection is mandatory before acting on the conclusion, never a
permission or autonomy decision in itself. Per this project's standing
rule (see CLAUDE.md), a graph built by a third-party static-analysis
tool must never become the enforced safety boundary that
agent/autonomy.py and tools/registry.py already are.

GRAPH SCHEMA (graphify 0.9.47, determined by direct inspection of a
real generated graph.json, not assumed from documentation):
    {
      "directed": bool,          # storage-format flag; edges still
                                  # carry real directional semantics via
                                  # source/target below, which this
                                  # module treats as a directed relation
                                  # (source depends-on/references target)
                                  # -- matching the graphify CLI's own
                                  # default (non---undirected) behavior.
      "nodes": [
        {
          "id": str,              # stable, unique
          "label": str,           # "name()" for functions, "Name" for
                                   # classes, "file.py" for module
                                   # nodes, truncated comment/docstring
                                   # text for file_type=="rationale"
          "_callable": bool,      # optional; present for functions/
                                   # methods/classes
          "_callable_class": bool,# optional; present only for classes
          "community": int,
          "community_name": str,  # "Community N" unless separately
                                   # LLM-labeled (never done by Jarvis)
          "file_type": "code" | "rationale",
          "norm_label": str,      # lowercased label, used for matching
          "source_file": str,     # relative path
          "source_location": str, # "L<line>"
        }, ...
      ],
      "links": [
        {
          "source": str, "target": str,   # node ids
          "relation": str,        # calls|contains|imports|imports_from|
                                   # indirect_call|inherits|method|
                                   # rationale_for|re_exports|references|uses
          "confidence": "EXTRACTED" | "INFERRED",
          "confidence_score": float,
          "context": str | None,
          "source_file": str, "source_location": str, "weight": float,
        }, ...
      ],
      "built_at_commit": str,
    }
Graphify does not embed its own version number in graph.json,
manifest.json, or .graphify_analysis.json (checked directly -- no
"version" key anywhere in any of the three). The only on-disk hint is
the cache directory name (graphify-out/cache/ast/v<VERSION>-s<N>),
read here as a best-effort, informational-only value -- never used to
gate any behavior, since it may be absent (a fresh extraction with no
prior cache) without that meaning anything is wrong.
"""
import json
import os
import subprocess
from typing import Optional

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_DEFAULT_GRAPH_DIR = os.path.join(_PROJECT_ROOT, "graphify-out")
_GRAPH_JSON_NAME = "graph.json"

_GIT_TIMEOUT_SECONDS = 5

# Verified during Graphify G0's evaluation (docs/GRAPHIFY.md has the
# full evidence trail for each) -- surfaced on every result so a
# consumer of this module's output (Jarvis's own model loop) is never
# left assuming the graph is more reliable than it actually is.
_KNOWN_LIMITATIONS = (
    "Same-basename module collisions (e.g. tools/registry.py vs "
    "agent/skills/registry.py) can produce a false-positive edge.",
    "register(ToolSpec(..., handler=...)) registration wiring is not "
    "reliably captured as a graph edge.",
    "Ambiguous bare symbol names can match an unintended node with the "
    "same name elsewhere -- prefer exact node IDs once known.",
)

# Deterministic, keyword/path-based -- consistent with this project's
# standing rule that routing/classification logic here is plain logic,
# never a model call (see CLAUDE.md). Intentionally broad (over-flagging
# is safe; under-flagging defeats the point) -- a path PREFIX match on
# source_file, or a keyword appearing in the source_file path or node
# label, is enough to require verification before acting.
_CRITICAL_PATH_PREFIXES = (
    "tools/registry.py",
    "tools/schemas/",
    "agent/autonomy.py",
)
_CRITICAL_KEYWORDS = ("toolspec", "permission", "autonomy", "secret", "credential")


def _is_critical(node: dict) -> bool:
    source_file = (node.get("source_file") or "").lower()
    label = (node.get("label") or "").lower()
    if any(source_file.startswith(prefix.lower()) for prefix in _CRITICAL_PATH_PREFIXES):
        return True
    return any(keyword in source_file or keyword in label for keyword in _CRITICAL_KEYWORDS)


def _node_kind(node: dict) -> str:
    if node.get("file_type") == "rationale":
        return "rationale"
    if node.get("_callable_class"):
        return "class"
    if node.get("_callable"):
        return "function"
    return "module"


def _run_git(args: list) -> Optional[str]:
    """Fixed argv, no shell, bounded timeout, cwd pinned to this repo.
    Returns stripped stdout on a clean exit, None on ANY failure
    (missing git, non-zero exit, timeout) -- callers must treat None as
    "could not be determined", never silently as success/clean."""
    try:
        result = subprocess.run(
            ["git", *args],
            cwd=_PROJECT_ROOT,
            capture_output=True,
            text=True,
            timeout=_GIT_TIMEOUT_SECONDS,
            shell=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    return result.stdout.strip()


def _current_commit() -> Optional[str]:
    return _run_git(["rev-parse", "HEAD"])


def _working_tree_clean() -> Optional[bool]:
    # --untracked-files=no is deliberate: an untracked file (including
    # graphify-out/ itself, which is gitignored, or any other
    # in-progress scratch file) never existed as far as the graph's own
    # built_at_commit is concerned, so it must not count as "dirty"
    # here -- only tracked modifications can make the graph stale.
    output = _run_git(["status", "--porcelain", "--untracked-files=no"])
    if output is None:
        return None
    return output == ""


def _detect_graphify_version(graph_dir: str) -> Optional[str]:
    cache_ast_dir = os.path.join(graph_dir, "cache", "ast")
    try:
        entries = os.listdir(cache_ast_dir)
    except OSError:
        return None
    for entry in entries:
        if entry.startswith("v"):
            return entry.split("-")[0].lstrip("v")
    return None


class CodeGraphReader:
    """One instance per call is intentional -- this always re-reads
    graph.json and re-runs the git checks fresh rather than caching
    across calls, trading a few milliseconds for never risking a
    stale-within-stale bug. `graph_dir` defaults to the real
    graphify-out/ location; tests pass a temporary directory here to
    isolate from the real repo's graph (git itself is still the real
    repo's git -- tests mock subprocess.run for that, matching this
    project's standing "mock at the external-call boundary" convention
    rather than building a second injectable git abstraction)."""

    def __init__(self, graph_dir: Optional[str] = None):
        self.graph_dir = graph_dir or _DEFAULT_GRAPH_DIR
        self._graph_json_path = os.path.join(self.graph_dir, _GRAPH_JSON_NAME)

    def _load_graph(self):
        """Returns (data, error_reason). data is None unless the file
        exists, parses as JSON, and has the minimal shape this module
        depends on."""
        if not os.path.isfile(self._graph_json_path):
            return None, "graph_missing"
        try:
            with open(self._graph_json_path, "r") as f:
                data = json.load(f)
        except (OSError, json.JSONDecodeError):
            return None, "graph_unreadable_or_malformed"
        if not isinstance(data, dict):
            return None, "graph_schema_unexpected"
        for key, expected_type in (("nodes", list), ("links", list), ("built_at_commit", str)):
            if not isinstance(data.get(key), expected_type):
                return None, "graph_schema_unexpected"
        return data, None

    def status(self) -> dict:
        data, load_error = self._load_graph()

        base = {
            "available": data is not None,
            "authoritative": False,
            "limitations": list(_KNOWN_LIMITATIONS),
        }

        if load_error == "graph_missing":
            base.update(state="unavailable", reason="No graph found at graphify-out/. Run "
                        "`graphify extract . --code-only` (see docs/GRAPHIFY.md) to generate one.")
            return base
        if load_error is not None:
            base.update(state="invalid", reason=f"Graph file could not be safely interpreted ({load_error}).")
            return base

        built_at_commit = data["built_at_commit"]
        current_commit = _current_commit()
        working_tree_clean = _working_tree_clean()

        base.update(
            built_at_commit=built_at_commit,
            current_commit=current_commit,
            working_tree_clean=working_tree_clean,
            node_count=len(data["nodes"]),
            edge_count=len(data["links"]),
            graphify_version=_detect_graphify_version(self.graph_dir),
        )

        if current_commit is None or working_tree_clean is None:
            base.update(state="stale", reason="Could not determine current git HEAD/working-tree "
                        "state, so freshness cannot be confirmed; treated conservatively as not fresh.")
            return base
        if built_at_commit != current_commit:
            base.update(state="stale", reason="Graph was built at a different commit than current HEAD.")
            return base
        if not working_tree_clean:
            base.update(state="stale", reason="Tracked working-tree files have uncommitted "
                        "modifications since the graph was built.")
            return base

        base.update(state="fresh", reason=None)
        return base

    def _fresh_graph_or_refusal(self):
        """Returns (data, None) if fresh, or (None, refusal_dict) with
        the exact structured-refusal shape callers should return
        as-is."""
        status = self.status()
        if status["state"] == "fresh":
            data, _ = self._load_graph()
            return data, None
        refusal = {
            "ok": False,
            "state": status["state"],
            "built_at_commit": status.get("built_at_commit"),
            "current_commit": status.get("current_commit"),
            "reason": status.get("reason"),
            "rebuild_required": status["state"] in ("stale", "unavailable"),
            "authoritative": False,
            "limitations": list(_KNOWN_LIMITATIONS),
        }
        return None, refusal

    # -- search --------------------------------------------------------

    def search(self, query: str, max_results: int = 10) -> dict:
        max_results = max(1, min(int(max_results), 20))
        query = (query or "").strip()[:200]
        data, refusal = self._fresh_graph_or_refusal()
        if refusal is not None:
            return refusal
        if not query:
            return {"ok": False, "error": "query must not be empty", "authoritative": False}

        query_lower = query.lower()
        scored = []
        for node in data["nodes"]:
            node_id = node["id"]
            label = node.get("label") or ""
            norm_label = node.get("norm_label") or ""
            bare_label = label[:-2] if label.endswith("()") else label

            score = 0
            if node_id == query:
                score = 100
            elif norm_label == query_lower or bare_label.lower() == query_lower:
                score = 90
            elif f"{node.get('source_file')}:{bare_label}".lower() == query_lower:
                score = 80
            elif norm_label.startswith(query_lower):
                score = 50
            elif query_lower in norm_label or query_lower in (node.get("source_file") or "").lower():
                score = 20
            if score:
                scored.append((score, node))

        scored.sort(key=lambda pair: (-pair[0], pair[1].get("source_file", ""), pair[1].get("source_location", "")))

        exact_tier_matches = [n for s, n in scored if s >= 90]
        distinct_exact_ids = {n["id"] for n in exact_tier_matches}
        ambiguous = len(distinct_exact_ids) > 1

        truncated = len(scored) > max_results
        results = [
            {
                "id": n["id"],
                "label": n.get("label"),
                "node_kind": _node_kind(n),
                "source_file": n.get("source_file"),
                "source_location": n.get("source_location"),
                "file_type": n.get("file_type"),
                "community": n.get("community"),
            }
            for _, n in scored[:max_results]
        ]

        return {
            "ok": True,
            "query": query,
            "results": results,
            "result_count": len(results),
            "truncated": truncated,
            "ambiguous": ambiguous,
            "source_verification_required": any(_is_critical(n) for _, n in scored[:max_results]),
            "authoritative": False,
            "limitations": list(_KNOWN_LIMITATIONS),
        }

    # -- impact ----------------------------------------------------------

    def analyze_impact(self, node_id: str, max_depth: int = 2, max_results: int = 50) -> dict:
        max_depth = max(1, min(int(max_depth), 3))
        max_results = max(1, min(int(max_results), 100))
        data, refusal = self._fresh_graph_or_refusal()
        if refusal is not None:
            return refusal

        nodes_by_id = {n["id"]: n for n in data["nodes"]}
        if node_id not in nodes_by_id:
            return {"ok": False, "error": f"unknown node_id: {node_id!r}", "authoritative": False}

        # Reverse adjacency: for edge source->target ("source depends on
        # target"), what's affected by changing target is every source
        # that points at it -- so we index edges by target.
        reverse_adjacency: dict = {}
        for edge in data["links"]:
            reverse_adjacency.setdefault(edge["target"], []).append(edge)

        visited = {node_id}
        frontier = [node_id]
        found = []  # (depth, edge, node)
        for depth in range(1, max_depth + 1):
            next_frontier = []
            for current in frontier:
                for edge in reverse_adjacency.get(current, []):
                    source_id = edge["source"]
                    if source_id in visited:
                        continue
                    visited.add(source_id)
                    next_frontier.append(source_id)
                    source_node = nodes_by_id.get(source_id)
                    if source_node is not None:
                        found.append((depth, edge, source_node))
            frontier = next_frontier
            if not frontier:
                break

        found.sort(key=lambda item: (item[0], item[1]["confidence"] != "EXTRACTED", item[2]["id"]))
        truncated = len(found) > max_results
        found = found[:max_results]

        results = [
            {
                "id": node["id"],
                "label": node.get("label"),
                "node_kind": _node_kind(node),
                "source_file": node.get("source_file"),
                "source_location": node.get("source_location"),
                "depth": depth,
                "relationship": "direct" if depth == 1 else "indirect",
                "relation": edge["relation"],
                "confidence": edge["confidence"],
                "confidence_score": edge.get("confidence_score"),
            }
            for depth, edge, node in found
        ]

        target_node = nodes_by_id[node_id]
        verification_required = _is_critical(target_node) or any(
            _is_critical(node) for _, _, node in found
        )

        return {
            "ok": True,
            "node_id": node_id,
            "max_depth": max_depth,
            "results": results,
            "result_count": len(results),
            "truncated": truncated,
            "note": "Structural graph relationship only -- not proof of runtime behavior or an "
                    "exhaustive dependency list.",
            "source_verification_required": verification_required,
            "authoritative": False,
            "limitations": list(_KNOWN_LIMITATIONS),
        }

    # -- path --------------------------------------------------------------

    def find_path(self, source_node_id: str, target_node_id: str, max_depth: int = 6) -> dict:
        max_depth = max(1, min(int(max_depth), 10))
        data, refusal = self._fresh_graph_or_refusal()
        if refusal is not None:
            return refusal

        nodes_by_id = {n["id"]: n for n in data["nodes"]}
        for label, node_id in (("source_node_id", source_node_id), ("target_node_id", target_node_id)):
            if node_id not in nodes_by_id:
                return {"ok": False, "error": f"unknown {label}: {node_id!r}", "authoritative": False}

        if source_node_id == target_node_id:
            return {
                "ok": True, "found": True, "hops": [], "hop_count": 0,
                "note": "source and target are the same node.",
                "source_verification_required": _is_critical(nodes_by_id[source_node_id]),
                "authoritative": False, "limitations": list(_KNOWN_LIMITATIONS),
            }

        forward_adjacency: dict = {}
        for edge in data["links"]:
            forward_adjacency.setdefault(edge["source"], []).append(edge)

        # BFS shortest path, cycle-safe via `visited`/`came_from` marked
        # at enqueue time -- one shortest path returned, not every path.
        came_from = {}  # node_id -> edge used to reach it
        visited = {source_node_id}
        frontier = [(source_node_id, 0)]
        found_target = False
        while frontier and not found_target:
            next_frontier = []
            for current, depth in frontier:
                if depth >= max_depth:
                    continue
                for edge in forward_adjacency.get(current, []):
                    nxt = edge["target"]
                    if nxt in visited:
                        continue
                    visited.add(nxt)
                    came_from[nxt] = edge
                    if nxt == target_node_id:
                        found_target = True
                        break
                    next_frontier.append((nxt, depth + 1))
                if found_target:
                    break
            frontier = next_frontier

        if not found_target:
            return {
                "ok": True, "found": False, "hops": [], "hop_count": 0,
                "reason": f"No directed structural path within {max_depth} hops.",
                "source_verification_required": _is_critical(nodes_by_id[source_node_id])
                or _is_critical(nodes_by_id[target_node_id]),
                "authoritative": False, "limitations": list(_KNOWN_LIMITATIONS),
            }

        # Walk back from target to source via came_from.
        hops = []
        cursor = target_node_id
        touched_nodes = [nodes_by_id[target_node_id]]
        while cursor != source_node_id:
            edge = came_from[cursor]
            hops.append({
                "from": edge["source"], "to": edge["target"],
                "relation": edge["relation"], "confidence": edge["confidence"],
                "confidence_score": edge.get("confidence_score"),
            })
            cursor = edge["source"]
            touched_nodes.append(nodes_by_id[cursor])
        hops.reverse()

        return {
            "ok": True,
            "found": True,
            "hops": hops,
            "hop_count": len(hops),
            "note": "Structural graph path only -- not proof of runtime call order.",
            "source_verification_required": any(_is_critical(n) for n in touched_nodes),
            "authoritative": False,
            "limitations": list(_KNOWN_LIMITATIONS),
        }


def get_status(graph_dir: Optional[str] = None) -> dict:
    return CodeGraphReader(graph_dir).status()


def search(query: str, max_results: int = 10, graph_dir: Optional[str] = None) -> dict:
    return CodeGraphReader(graph_dir).search(query, max_results)


def analyze_impact(node_id: str, max_depth: int = 2, max_results: int = 50, graph_dir: Optional[str] = None) -> dict:
    return CodeGraphReader(graph_dir).analyze_impact(node_id, max_depth, max_results)


def find_path(source_node_id: str, target_node_id: str, max_depth: int = 6, graph_dir: Optional[str] = None) -> dict:
    return CodeGraphReader(graph_dir).find_path(source_node_id, target_node_id, max_depth)
