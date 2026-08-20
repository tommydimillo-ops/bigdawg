"""Graphify G1 -- four narrow, read-only Jarvis tools over the locally
generated Graphify code graph (agent/code_graph.py). See
docs/GRAPHIFY.md for the full picture.

Deliberately NOT a generic graph-query surface: no raw Cypher-like
input, no caller-selected CLI subcommand, no caller-supplied graph
filesystem path (agent/code_graph.py always resolves graphify-out/
itself), and no path to the `graphify`/`graphify-mcp` executables from
any of these four handlers -- agent/code_graph.py reads graph.json with
the standard library only. All four are permission_level=0 (pure
reads, no side effect, safe unattended, safe in parallel) -- matching
tools/schemas/system.py's get_system_status precedent exactly.

Every result these tools return carries `authoritative: False` and a
`limitations` list (see agent/code_graph.py's `_KNOWN_LIMITATIONS`) --
this graph is supplementary structural intelligence, never a source of
truth for a permission/autonomy/security-boundary decision. A result
touching a security-relevant part of the codebase additionally carries
`source_verification_required: True`, meaning Jarvis must read the real
source before acting on the conclusion, not just consult the graph.
"""
import json

from agent.code_graph import analyze_impact, find_path, get_status, search
from tools.registry import ToolSpec, register


def _code_graph_status(tool_input: dict) -> str:
    return json.dumps(get_status())


def _search_code_graph(tool_input: dict) -> str:
    return json.dumps(search(
        query=tool_input.get("query", ""),
        max_results=tool_input.get("max_results") or 10,
    ))


def _analyze_code_impact(tool_input: dict) -> str:
    return json.dumps(analyze_impact(
        node_id=tool_input.get("node_id", ""),
        max_depth=tool_input.get("max_depth") or 2,
        max_results=tool_input.get("max_results") or 50,
    ))


def _find_code_path(tool_input: dict) -> str:
    return json.dumps(find_path(
        source_node_id=tool_input.get("source_node_id", ""),
        target_node_id=tool_input.get("target_node_id", ""),
        max_depth=tool_input.get("max_depth") or 6,
    ))


register(ToolSpec(
    name="code_graph_status",
    description=(
        "Check whether the local Graphify code-structure graph is "
        "available and fresh (built from the current commit, with a "
        "clean tracked working tree) before relying on it. Always "
        "supplementary -- never a source of truth for permission, "
        "autonomy, or security decisions; direct source reading remains "
        "authoritative. Read-only, no arguments."
    ),
    input_schema={"type": "object", "properties": {}, "required": []},
    permission_level=0,
    handler=_code_graph_status,
    parallel_safe=True,
))

register(ToolSpec(
    name="search_code_graph",
    description=(
        "Search the local Graphify code-structure graph for a symbol, "
        "file, or node by name -- faster than grepping when you need to "
        "find the exact node ID of a function/class/file before calling "
        "analyze_code_impact or find_code_path. Deterministic exact/"
        "prefix/substring matching only, capped results, and explicit "
        "ambiguity reporting if multiple different symbols share the "
        "same bare name (never silently picks one). Refuses to return "
        "results if the graph is stale/unavailable/invalid -- returns a "
        "structured status instead. Supplementary only, never "
        "authoritative; results touching tool-registry/autonomy/"
        "permission/credential code require direct source verification "
        "before acting."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "Symbol name, node ID, or relative file path to search for.",
            },
            "max_results": {
                "type": "integer",
                "description": "Maximum results to return (capped at 20). Defaults to 10.",
            },
        },
        "required": ["query"],
    },
    permission_level=0,
    handler=_search_code_graph,
    parallel_safe=True,
))

register(ToolSpec(
    name="analyze_code_impact",
    description=(
        "Reverse-dependency lookup on the local Graphify code-structure "
        "graph: what structurally depends on (imports/calls/references) "
        "a given node, directly or indirectly. Use search_code_graph "
        "first to resolve a symbol to its exact node_id -- this tool "
        "requires an exact node_id, never a bare/ambiguous name. Bounded "
        "traversal (max depth 3, max 100 results), distinguishes direct "
        "from indirect and EXTRACTED from INFERRED edges. A structural "
        "relationship in the graph, NOT proof of actual runtime impact "
        "and not an exhaustive dependency list -- refuses to run if the "
        "graph is stale/unavailable/invalid. Supplementary only, never "
        "authoritative; results touching tool-registry/autonomy/"
        "permission/credential code require direct source verification "
        "before acting."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "node_id": {
                "type": "string",
                "description": "Exact graph node ID (from search_code_graph) to analyze.",
            },
            "max_depth": {
                "type": "integer",
                "description": "Maximum reverse-traversal depth (capped at 3). Defaults to 2.",
            },
            "max_results": {
                "type": "integer",
                "description": "Maximum results to return (capped at 100). Defaults to 50.",
            },
        },
        "required": ["node_id"],
    },
    permission_level=0,
    handler=_analyze_code_impact,
    parallel_safe=True,
))

register(ToolSpec(
    name="find_code_path",
    description=(
        "Find the shortest structural path between two known nodes in "
        "the local Graphify code-structure graph (e.g. 'how does this "
        "tool handler eventually reach that function'). Use "
        "search_code_graph first to resolve both symbols to exact node "
        "IDs -- this tool requires exact node_ids for both source and "
        "target, never bare/ambiguous names. Bounded, deterministic "
        "breadth-first search (max depth 10), returns one shortest path "
        "with each edge's relation/confidence or a clean 'no path "
        "found'. A structural path in the graph, NOT proof of actual "
        "runtime call order -- refuses to run if the graph is stale/"
        "unavailable/invalid. Supplementary only, never authoritative; "
        "results touching tool-registry/autonomy/permission/credential "
        "code require direct source verification before acting."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "source_node_id": {
                "type": "string",
                "description": "Exact graph node ID (from search_code_graph) to start from.",
            },
            "target_node_id": {
                "type": "string",
                "description": "Exact graph node ID (from search_code_graph) to reach.",
            },
            "max_depth": {
                "type": "integer",
                "description": "Maximum hops to search (capped at 10). Defaults to 6.",
            },
        },
        "required": ["source_node_id", "target_node_id"],
    },
    permission_level=0,
    handler=_find_code_path,
    parallel_safe=True,
))
