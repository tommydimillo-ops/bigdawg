"""Tests for tools/schemas/graphify.py -- Graphify G1's four read-only
ToolSpecs, exercised through the real tools.registry.dispatch() path,
matching tests/test_openclaw_tool.py's established pattern. Mocks at the
agent.code_graph boundary (as imported into tools.schemas.graphify) --
agent.code_graph's own internals are covered by tests/test_code_graph.py.
Nothing here touches a real graph file or the `graphify`/`graphifyy`
package.

Run with: python -m unittest tests.test_graphify_tools -v
"""
import json
import unittest
from unittest.mock import patch

import tools.schemas  # noqa: F401 -- populates the registry
from agent.executor import _run_tool
from tools import registry

_TOOL_NAMES = ("code_graph_status", "search_code_graph", "analyze_code_impact", "find_code_path")


class TestToolsAreRegistered(unittest.TestCase):

    def test_all_four_registered(self):
        for name in _TOOL_NAMES:
            with self.subTest(name=name):
                self.assertIn(name, registry.all_names())

    def test_all_four_are_permission_level_zero(self):
        for name in _TOOL_NAMES:
            with self.subTest(name=name):
                self.assertEqual(registry.permission_level(name), 0)

    def test_none_are_side_effect_tools(self):
        side_effect_tools = registry.side_effect_tools()
        for name in _TOOL_NAMES:
            with self.subTest(name=name):
                self.assertNotIn(name, side_effect_tools)

    def test_all_four_unattended_allowed(self):
        for name in _TOOL_NAMES:
            with self.subTest(name=name):
                self.assertTrue(registry.get(name).unattended_allowed)

    def test_none_require_live_confirmation(self):
        for name in _TOOL_NAMES:
            with self.subTest(name=name):
                self.assertFalse(registry.get(name).requires_live_confirmation)

    def test_all_four_parallel_safe(self):
        parallel_safe = registry.parallel_safe_tools()
        for name in _TOOL_NAMES:
            with self.subTest(name=name):
                self.assertIn(name, parallel_safe)

    def test_no_tool_input_schema_accepts_a_graph_path(self):
        # The one hard security requirement from the G1 spec: no caller
        # (model or otherwise) can select an arbitrary graph filesystem
        # path through a Jarvis tool -- agent/code_graph.py always
        # resolves graphify-out/ itself.
        for name in _TOOL_NAMES:
            with self.subTest(name=name):
                properties = registry.get(name).input_schema["properties"]
                for forbidden in ("graph_dir", "graph_path", "path", "cwd", "root"):
                    self.assertNotIn(forbidden, properties)

    def test_no_tool_input_schema_accepts_a_raw_command_or_cli_argument(self):
        for name in _TOOL_NAMES:
            with self.subTest(name=name):
                properties = registry.get(name).input_schema["properties"]
                for forbidden in ("command", "cli_args", "subcommand", "cypher", "raw_query", "flags"):
                    self.assertNotIn(forbidden, properties)

    def test_no_generic_graph_query_tool_is_registered(self):
        names = registry.all_names()
        for forbidden in (
            "run_graphify_command", "graphify_query", "raw_graph_query",
            "execute_graph_command", "graphify_cli", "graphify_exec",
        ):
            self.assertNotIn(forbidden, names)

    def test_code_graph_status_takes_no_input(self):
        schema = registry.get("code_graph_status").input_schema
        self.assertEqual(schema["properties"], {})
        self.assertEqual(schema["required"], [])

    def test_search_code_graph_requires_query(self):
        schema = registry.get("search_code_graph").input_schema
        self.assertEqual(schema["required"], ["query"])

    def test_analyze_code_impact_requires_node_id(self):
        schema = registry.get("analyze_code_impact").input_schema
        self.assertEqual(schema["required"], ["node_id"])

    def test_find_code_path_requires_both_node_ids(self):
        schema = registry.get("find_code_path").input_schema
        self.assertEqual(set(schema["required"]), {"source_node_id", "target_node_id"})


class TestCodeGraphStatusTool(unittest.TestCase):

    @patch("tools.schemas.graphify.get_status")
    def test_dispatch_returns_json_serialized_status(self, mock_status):
        mock_status.return_value = {"available": True, "state": "fresh", "authoritative": False}
        result = registry.dispatch("code_graph_status", {})
        parsed = json.loads(result)
        self.assertEqual(parsed["state"], "fresh")
        self.assertFalse(parsed["authoritative"])

    @patch("tools.schemas.graphify.get_status")
    def test_dispatch_never_raises_even_if_status_is_a_refusal_shape(self, mock_status):
        mock_status.return_value = {"available": False, "state": "unavailable", "authoritative": False}
        result = registry.dispatch("code_graph_status", {})
        self.assertIsInstance(result, str)


class TestSearchCodeGraphTool(unittest.TestCase):

    @patch("tools.schemas.graphify.search")
    def test_dispatch_passes_query_and_max_results(self, mock_search):
        mock_search.return_value = {"ok": True, "results": []}
        registry.dispatch("search_code_graph", {"query": "ModelChoice", "max_results": 5})
        mock_search.assert_called_once_with(query="ModelChoice", max_results=5)

    @patch("tools.schemas.graphify.search")
    def test_dispatch_defaults_max_results_when_omitted(self, mock_search):
        mock_search.return_value = {"ok": True, "results": []}
        registry.dispatch("search_code_graph", {"query": "ModelChoice"})
        mock_search.assert_called_once_with(query="ModelChoice", max_results=10)

    @patch("tools.schemas.graphify.search")
    def test_ambiguous_result_round_trips_cleanly(self, mock_search):
        mock_search.return_value = {"ok": True, "ambiguous": True, "results": [{"id": "a"}, {"id": "b"}]}
        result = registry.dispatch("search_code_graph", {"query": "remember"})
        parsed = json.loads(result)
        self.assertTrue(parsed["ambiguous"])
        self.assertEqual(len(parsed["results"]), 2)

    @patch("tools.schemas.graphify.search")
    def test_stale_refusal_round_trips_cleanly(self, mock_search):
        mock_search.return_value = {"ok": False, "state": "stale", "rebuild_required": True}
        result = registry.dispatch("search_code_graph", {"query": "x"})
        parsed = json.loads(result)
        self.assertFalse(parsed["ok"])
        self.assertEqual(parsed["state"], "stale")


class TestAnalyzeCodeImpactTool(unittest.TestCase):

    @patch("tools.schemas.graphify.analyze_impact")
    def test_dispatch_passes_node_id_and_bounds(self, mock_impact):
        mock_impact.return_value = {"ok": True, "results": []}
        registry.dispatch("analyze_code_impact", {"node_id": "tools_registry_toolspec", "max_depth": 1, "max_results": 10})
        mock_impact.assert_called_once_with(node_id="tools_registry_toolspec", max_depth=1, max_results=10)

    @patch("tools.schemas.graphify.analyze_impact")
    def test_dispatch_defaults_when_optional_fields_omitted(self, mock_impact):
        mock_impact.return_value = {"ok": True, "results": []}
        registry.dispatch("analyze_code_impact", {"node_id": "x"})
        mock_impact.assert_called_once_with(node_id="x", max_depth=2, max_results=50)

    @patch("tools.schemas.graphify.analyze_impact")
    def test_source_verification_required_round_trips_cleanly(self, mock_impact):
        mock_impact.return_value = {"ok": True, "source_verification_required": True, "results": []}
        result = registry.dispatch("analyze_code_impact", {"node_id": "tools_registry_toolspec"})
        parsed = json.loads(result)
        self.assertTrue(parsed["source_verification_required"])


class TestFindCodePathTool(unittest.TestCase):

    @patch("tools.schemas.graphify.find_path")
    def test_dispatch_passes_both_node_ids_and_max_depth(self, mock_path):
        mock_path.return_value = {"ok": True, "found": True, "hops": []}
        registry.dispatch("find_code_path", {"source_node_id": "a", "target_node_id": "b", "max_depth": 4})
        mock_path.assert_called_once_with(source_node_id="a", target_node_id="b", max_depth=4)

    @patch("tools.schemas.graphify.find_path")
    def test_dispatch_defaults_max_depth_when_omitted(self, mock_path):
        mock_path.return_value = {"ok": True, "found": True, "hops": []}
        registry.dispatch("find_code_path", {"source_node_id": "a", "target_node_id": "b"})
        mock_path.assert_called_once_with(source_node_id="a", target_node_id="b", max_depth=6)

    @patch("tools.schemas.graphify.find_path")
    def test_no_path_result_round_trips_cleanly(self, mock_path):
        mock_path.return_value = {"ok": True, "found": False, "hops": []}
        result = registry.dispatch("find_code_path", {"source_node_id": "a", "target_node_id": "z"})
        parsed = json.loads(result)
        self.assertFalse(parsed["found"])


class TestNormalExecutorDispatch(unittest.TestCase):
    """Confirms these four go through the real agent/executor.py
    _run_tool path -- no separate dispatch mechanism -- matching
    tests/test_observability.py's TestRealToolDispatchLogging pattern."""

    @patch("tools.schemas.graphify.get_status")
    def test_code_graph_status_dispatches_through_the_real_executor(self, mock_status):
        mock_status.return_value = {"available": True, "state": "fresh", "authoritative": False}
        result = _run_tool("code_graph_status", {}, source="chat")
        parsed = json.loads(result)
        self.assertEqual(parsed["state"], "fresh")

    @patch("tools.schemas.graphify.search")
    def test_search_code_graph_dispatches_through_the_real_executor(self, mock_search):
        mock_search.return_value = {"ok": True, "results": []}
        result = _run_tool("search_code_graph", {"query": "x"}, source="chat")
        self.assertTrue(json.loads(result)["ok"])

    @patch("tools.schemas.graphify.analyze_impact")
    def test_analyze_code_impact_dispatches_through_the_real_executor(self, mock_impact):
        mock_impact.return_value = {"ok": True, "results": []}
        result = _run_tool("analyze_code_impact", {"node_id": "x"}, source="chat")
        self.assertTrue(json.loads(result)["ok"])

    @patch("tools.schemas.graphify.find_path")
    def test_find_code_path_dispatches_through_the_real_executor(self, mock_path):
        mock_path.return_value = {"ok": True, "found": True, "hops": []}
        result = _run_tool("find_code_path", {"source_node_id": "a", "target_node_id": "b"}, source="chat")
        self.assertTrue(json.loads(result)["ok"])

    @patch("tools.schemas.graphify.get_status")
    def test_read_only_tools_never_trigger_verification_or_confirmation(self, mock_status):
        # permission_level=0 + side_effect=False means _run_tool must
        # never enter the VERIFYING transition path (that's only for
        # registry.side_effect_tools()).
        mock_status.return_value = {"available": True, "state": "fresh", "authoritative": False}
        with patch("agent.executor.verify") as mock_verify:
            _run_tool("code_graph_status", {}, source="chat")
        mock_verify.assert_not_called()


if __name__ == "__main__":
    unittest.main()
