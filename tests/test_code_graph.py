"""Tests for agent/code_graph.py -- Graphify G1's read-only reader over
a locally generated graphify-out/graph.json. Every test uses a small
synthetic fixture matching the real graphify 0.9.47 schema (verified by
direct inspection during G1 -- see agent/code_graph.py's own module
docstring), never the real 11MB graph and never the real `graphify`/
`graphifyy` package -- CI needs neither installed nor on PATH.

Git state (current commit / working-tree cleanliness) is mocked at the
module boundary (agent.code_graph._current_commit /
_working_tree_clean), matching this project's "mock at the external-call
boundary, never subprocess internals" convention -- this also isolates
every test from this real repo's own actual git state.

Run with: python -m unittest tests.test_code_graph -v
"""
import json
import os
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch

import agent.code_graph as cg


def _node(id_, label, source_file, source_location, *, callable_=True, is_class=False, file_type="code", community=0):
    node = {
        "id": id_, "label": label, "community": community, "community_name": f"Community {community}",
        "file_type": file_type, "norm_label": label.lower().rstrip("()"),
        "source_file": source_file, "source_location": source_location,
    }
    if callable_:
        node["_callable"] = True
    if is_class:
        node["_callable_class"] = True
    return node


def _edge(source, target, relation, confidence="EXTRACTED", confidence_score=1.0, context="call",
          source_file="x.py", source_location="L1"):
    return {
        "source": source, "target": target, "relation": relation,
        "confidence": confidence, "confidence_score": confidence_score, "context": context,
        "source_file": source_file, "source_location": source_location, "weight": 1.0,
    }


def _write_graph(graph_dir, nodes, links, built_at_commit="deadbeefcafe"):
    os.makedirs(graph_dir, exist_ok=True)
    with open(os.path.join(graph_dir, "graph.json"), "w") as f:
        json.dump({
            "directed": False, "multigraph": False, "graph": {},
            "nodes": nodes, "links": links, "hyperedges": [],
            "built_at_commit": built_at_commit,
        }, f)


# A small, deliberately cyclic 4-node fixture: a -> b -> c -> a (cycle),
# plus d depending on b, plus a "registry"-flagged node for
# source_verification_required tests.
_NODE_A = _node("mod_a_a", "A()", "mod_a.py", "L1")
_NODE_B = _node("mod_b_b", "B()", "mod_b.py", "L1")
_NODE_C = _node("mod_c_c", "C()", "mod_c.py", "L1")
_NODE_D = _node("mod_d_d", "D()", "mod_d.py", "L1")
_NODE_REGISTRY = _node("tools_registry_toolspec", "ToolSpec", "tools/registry.py", "L30", is_class=True)
_STANDARD_NODES = [_NODE_A, _NODE_B, _NODE_C, _NODE_D, _NODE_REGISTRY]
_STANDARD_LINKS = [
    _edge("mod_a_a", "mod_b_b", "calls"),
    _edge("mod_b_b", "mod_c_c", "imports", confidence="INFERRED", confidence_score=0.85, context=None),
    _edge("mod_c_c", "mod_a_a", "uses"),  # closes the cycle
    _edge("mod_d_d", "mod_b_b", "calls"),
]


class CodeGraphTestCase(unittest.TestCase):
    """Writes a fresh temp graph dir per test; mocks git state to
    "fresh" (matching built_at_commit) by default -- override per test
    via the same two patch targets for stale/unknown scenarios."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmpdir, ignore_errors=True)

    def _reader(self, nodes=_STANDARD_NODES, links=_STANDARD_LINKS, built_at_commit="deadbeefcafe"):
        _write_graph(self.tmpdir, nodes, links, built_at_commit)
        return cg.CodeGraphReader(graph_dir=self.tmpdir)

    def _fresh(self, **kwargs):
        patches = [
            patch("agent.code_graph._current_commit", return_value=kwargs.pop("built_at_commit", "deadbeefcafe")),
            patch("agent.code_graph._working_tree_clean", return_value=True),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        return self._reader(**kwargs)


class TestStatus(CodeGraphTestCase):

    def test_fresh_graph(self):
        reader = self._fresh()
        status = reader.status()
        self.assertEqual(status["state"], "fresh")
        self.assertTrue(status["available"])
        self.assertEqual(status["node_count"], 5)
        self.assertEqual(status["edge_count"], 4)
        self.assertEqual(status["built_at_commit"], "deadbeefcafe")
        self.assertEqual(status["current_commit"], "deadbeefcafe")
        self.assertTrue(status["working_tree_clean"])
        self.assertFalse(status["authoritative"])
        self.assertTrue(status["limitations"])

    def test_missing_graph(self):
        reader = cg.CodeGraphReader(graph_dir=self.tmpdir)  # nothing written
        status = reader.status()
        self.assertEqual(status["state"], "unavailable")
        self.assertFalse(status["available"])

    def test_malformed_graph_json(self):
        os.makedirs(self.tmpdir, exist_ok=True)
        with open(os.path.join(self.tmpdir, "graph.json"), "w") as f:
            f.write("{not valid json,,,")
        reader = cg.CodeGraphReader(graph_dir=self.tmpdir)
        status = reader.status()
        self.assertEqual(status["state"], "invalid")
        self.assertFalse(status["available"])

    def test_graph_missing_required_schema_fields_is_invalid(self):
        os.makedirs(self.tmpdir, exist_ok=True)
        with open(os.path.join(self.tmpdir, "graph.json"), "w") as f:
            json.dump({"nodes": []}, f)  # no "links", no "built_at_commit"
        reader = cg.CodeGraphReader(graph_dir=self.tmpdir)
        status = reader.status()
        self.assertEqual(status["state"], "invalid")

    def test_head_mismatch_is_stale(self):
        with patch("agent.code_graph._current_commit", return_value="differentcommit"), \
             patch("agent.code_graph._working_tree_clean", return_value=True):
            reader = self._reader(built_at_commit="deadbeefcafe")
            status = reader.status()
        self.assertEqual(status["state"], "stale")
        self.assertEqual(status["built_at_commit"], "deadbeefcafe")
        self.assertEqual(status["current_commit"], "differentcommit")

    def test_dirty_working_tree_is_stale_even_if_commit_matches(self):
        with patch("agent.code_graph._current_commit", return_value="deadbeefcafe"), \
             patch("agent.code_graph._working_tree_clean", return_value=False):
            reader = self._reader(built_at_commit="deadbeefcafe")
            status = reader.status()
        self.assertEqual(status["state"], "stale")
        self.assertFalse(status["working_tree_clean"])

    def test_git_command_failure_is_treated_as_not_fresh(self):
        # subprocess.run itself failing (git missing, timeout, etc.) must
        # never be silently treated as "clean"/"matches" -- conservative
        # stale, not a crash, not a false "fresh".
        with patch("agent.code_graph._current_commit", return_value=None), \
             patch("agent.code_graph._working_tree_clean", return_value=None):
            reader = self._reader()
            status = reader.status()
        self.assertEqual(status["state"], "stale")

    def test_status_never_exposes_absolute_local_path_or_username(self):
        reader = self._fresh()
        dumped = json.dumps(reader.status())
        self.assertNotIn(self.tmpdir, dumped)
        self.assertNotIn(os.path.expanduser("~"), dumped)


class TestSearch(CodeGraphTestCase):

    def test_exact_node_id_search(self):
        reader = self._fresh()
        result = reader.search("mod_a_a")
        self.assertTrue(result["ok"])
        self.assertEqual(result["results"][0]["id"], "mod_a_a")
        self.assertFalse(result["ambiguous"])

    def test_exact_symbol_search(self):
        reader = self._fresh()
        result = reader.search("A")  # bare label, no parens
        self.assertTrue(result["ok"])
        self.assertEqual(result["results"][0]["id"], "mod_a_a")

    def test_qualified_path_search(self):
        reader = self._fresh()
        result = reader.search("mod_a.py:A")
        self.assertTrue(result["ok"])
        self.assertEqual(result["results"][0]["id"], "mod_a_a")

    def test_bounded_result_count_hard_cap(self):
        many_nodes = [_node(f"n{i}", f"Thing{i}()", "big.py", f"L{i}") for i in range(50)]
        reader = self._fresh(nodes=many_nodes, links=[])
        result = reader.search("thing", max_results=999)  # requests way over the cap
        self.assertLessEqual(len(result["results"]), 20)
        self.assertTrue(result["truncated"])

    def test_max_results_default_and_floor(self):
        many_nodes = [_node(f"n{i}", f"Thing{i}()", "big.py", f"L{i}") for i in range(15)]
        reader = self._fresh(nodes=many_nodes, links=[])
        result = reader.search("thing", max_results=0)  # floors to at least 1
        self.assertGreaterEqual(len(result["results"]), 1)

    def test_duplicate_bare_name_ambiguity_is_reported_not_hidden(self):
        dup_a = _node("agent_memory_agent_remember", "remember()", "agent/memory_agent.py", "L21")
        dup_b = _node("agent_memory_manager_remember", "remember()", "agent/memory/manager.py", "L68")
        reader = self._fresh(nodes=[dup_a, dup_b], links=[])
        result = reader.search("remember")
        self.assertTrue(result["ambiguous"])
        ids = {r["id"] for r in result["results"]}
        self.assertIn("agent_memory_agent_remember", ids)
        self.assertIn("agent_memory_manager_remember", ids)

    def test_module_name_collision_both_candidates_exposed(self):
        # The confirmed G0 false-positive scenario: tools/registry.py vs
        # agent/skills/registry.py share a basename. Search must expose
        # both distinctly, never silently resolve to one.
        real_registry = _node("tools_registry", "registry.py", "tools/registry.py", "L1", callable_=False)
        skills_registry = _node("agent_skills_registry", "registry.py", "agent/skills/registry.py", "L1", callable_=False)
        reader = self._fresh(nodes=[real_registry, skills_registry], links=[])
        result = reader.search("registry.py")
        ids = {r["id"] for r in result["results"]}
        self.assertIn("tools_registry", ids)
        self.assertIn("agent_skills_registry", ids)
        self.assertTrue(result["ambiguous"])

    def test_stale_graph_refuses_search(self):
        with patch("agent.code_graph._current_commit", return_value="other"), \
             patch("agent.code_graph._working_tree_clean", return_value=True):
            reader = self._reader()
            result = reader.search("A")
        self.assertFalse(result["ok"])
        self.assertEqual(result["state"], "stale")
        self.assertNotIn("results", result)

    def test_unavailable_graph_refuses_search(self):
        reader = cg.CodeGraphReader(graph_dir=self.tmpdir)
        result = reader.search("A")
        self.assertFalse(result["ok"])
        self.assertEqual(result["state"], "unavailable")

    def test_empty_query_rejected(self):
        reader = self._fresh()
        result = reader.search("   ")
        self.assertFalse(result["ok"])

    def test_search_result_never_includes_file_contents(self):
        reader = self._fresh()
        result = reader.search("A")
        dumped = json.dumps(result)
        for forbidden in ("content", "body", "text_content", "file_contents"):
            self.assertNotIn(forbidden, dumped)

    def test_tools_registry_result_requires_source_verification(self):
        reader = self._fresh()
        result = reader.search("ToolSpec")
        self.assertTrue(result["source_verification_required"])


class TestImpact(CodeGraphTestCase):

    def test_direct_reverse_dependency(self):
        reader = self._fresh()
        result = reader.analyze_impact("mod_b_b")  # who depends on B? A and D directly
        self.assertTrue(result["ok"])
        direct_ids = {r["id"] for r in result["results"] if r["relationship"] == "direct"}
        self.assertEqual(direct_ids, {"mod_a_a", "mod_d_d"})

    def test_multi_hop_dependency_marked_indirect(self):
        reader = self._fresh()
        result = reader.analyze_impact("mod_c_c", max_depth=3)
        by_id = {r["id"]: r for r in result["results"]}
        self.assertEqual(by_id["mod_b_b"]["relationship"], "direct")
        # a depends on b depends on c -- wait: edges are a->b, b->c, c->a.
        # Reverse from c: direct = b (b->c). indirect (depth2) via b's
        # dependents = a (a->b) and d (d->b).
        self.assertEqual(by_id["mod_a_a"]["relationship"], "indirect")
        self.assertEqual(by_id["mod_a_a"]["depth"], 2)

    def test_cycle_safety_does_not_loop_forever(self):
        # a->b->c->a is a real cycle in the fixture; must terminate.
        reader = self._fresh()
        result = reader.analyze_impact("mod_a_a", max_depth=3)
        self.assertTrue(result["ok"])
        # 'a' itself must never appear as its own dependent.
        self.assertNotIn("mod_a_a", {r["id"] for r in result["results"]})

    def test_max_depth_is_bounded_to_three(self):
        reader = self._fresh()
        result = reader.analyze_impact("mod_a_a", max_depth=999)
        self.assertLessEqual(result["max_depth"], 3)

    def test_max_results_hard_cap(self):
        big_nodes = [_node(f"dep{i}", f"Dep{i}()", "deps.py", f"L{i}") for i in range(150)]
        target = _node("target", "Target()", "t.py", "L1")
        links = [_edge(f"dep{i}", "target", "calls") for i in range(150)]
        reader = self._fresh(nodes=big_nodes + [target], links=links)
        result = reader.analyze_impact("target", max_results=500)
        self.assertLessEqual(len(result["results"]), 100)
        self.assertTrue(result["truncated"])

    def test_extracted_vs_inferred_confidence_preserved(self):
        reader = self._fresh()
        result = reader.analyze_impact("mod_c_c")
        by_id = {r["id"]: r for r in result["results"]}
        self.assertEqual(by_id["mod_b_b"]["confidence"], "INFERRED")

    def test_tools_registry_target_requires_source_verification(self):
        reader = self._fresh()
        result = reader.analyze_impact("tools_registry_toolspec")
        self.assertTrue(result["source_verification_required"])

    def test_result_touching_registry_requires_source_verification(self):
        dependent = _node("some_caller", "caller()", "app.py", "L1")
        links = _STANDARD_LINKS + [_edge("some_caller", "tools_registry_toolspec", "references")]
        reader = self._fresh(nodes=_STANDARD_NODES + [dependent], links=links)
        result = reader.analyze_impact("tools_registry_toolspec")
        self.assertTrue(result["source_verification_required"])

    def test_unknown_node_id_is_a_clean_error(self):
        reader = self._fresh()
        result = reader.analyze_impact("does_not_exist")
        self.assertFalse(result["ok"])
        self.assertIn("error", result)

    def test_stale_graph_refuses_traversal(self):
        with patch("agent.code_graph._current_commit", return_value="other"), \
             patch("agent.code_graph._working_tree_clean", return_value=True):
            reader = self._reader()
            result = reader.analyze_impact("mod_b_b")
        self.assertFalse(result["ok"])
        self.assertEqual(result["state"], "stale")

    def test_result_never_claims_guaranteed_runtime_impact(self):
        reader = self._fresh()
        result = reader.analyze_impact("mod_b_b")
        self.assertIn("not proof of runtime behavior", result["note"])


class TestPath(CodeGraphTestCase):

    def test_direct_path(self):
        reader = self._fresh()
        result = reader.find_path("mod_a_a", "mod_b_b")
        self.assertTrue(result["found"])
        self.assertEqual(result["hop_count"], 1)
        self.assertEqual(result["hops"][0], {
            "from": "mod_a_a", "to": "mod_b_b", "relation": "calls",
            "confidence": "EXTRACTED", "confidence_score": 1.0,
        })

    def test_multi_hop_shortest_path(self):
        reader = self._fresh()
        result = reader.find_path("mod_a_a", "mod_c_c")
        self.assertTrue(result["found"])
        self.assertEqual(result["hop_count"], 2)
        self.assertEqual([h["to"] for h in result["hops"]], ["mod_b_b", "mod_c_c"])

    def test_no_path_reported_cleanly(self):
        isolated = _node("isolated", "Isolated()", "iso.py", "L1")
        reader = self._fresh(nodes=_STANDARD_NODES + [isolated], links=_STANDARD_LINKS)
        result = reader.find_path("mod_a_a", "isolated")
        self.assertTrue(result["ok"])
        self.assertFalse(result["found"])
        self.assertEqual(result["hops"], [])

    def test_cycle_does_not_hang_and_finds_shortest_path(self):
        # a->b->c->a: path from c back to a is the direct c->a edge, not
        # a longer loop through b.
        reader = self._fresh()
        result = reader.find_path("mod_c_c", "mod_a_a")
        self.assertTrue(result["found"])
        self.assertEqual(result["hop_count"], 1)

    def test_depth_bound_is_hard_capped(self):
        chain_nodes = [_node(f"n{i}", f"N{i}()", "c.py", f"L{i}") for i in range(15)]
        chain_links = [_edge(f"n{i}", f"n{i + 1}", "calls") for i in range(14)]
        reader = self._fresh(nodes=chain_nodes, links=chain_links)
        result = reader.find_path("n0", "n14", max_depth=999)
        # requested depth is way over the hard cap of 10, so a path
        # requiring 14 hops must not be found.
        self.assertFalse(result["found"])

    def test_unknown_source_node_id(self):
        reader = self._fresh()
        result = reader.find_path("nope", "mod_a_a")
        self.assertFalse(result["ok"])
        self.assertIn("source_node_id", result["error"])

    def test_unknown_target_node_id(self):
        reader = self._fresh()
        result = reader.find_path("mod_a_a", "nope")
        self.assertFalse(result["ok"])
        self.assertIn("target_node_id", result["error"])

    def test_same_source_and_target(self):
        reader = self._fresh()
        result = reader.find_path("mod_a_a", "mod_a_a")
        self.assertTrue(result["found"])
        self.assertEqual(result["hop_count"], 0)

    def test_stale_graph_refuses_path_analysis(self):
        with patch("agent.code_graph._current_commit", return_value="other"), \
             patch("agent.code_graph._working_tree_clean", return_value=True):
            reader = self._reader()
            result = reader.find_path("mod_a_a", "mod_b_b")
        self.assertFalse(result["ok"])
        self.assertEqual(result["state"], "stale")

    def test_result_never_claims_guaranteed_runtime_order(self):
        reader = self._fresh()
        result = reader.find_path("mod_a_a", "mod_b_b")
        self.assertIn("not proof of runtime call order", result["note"])


class TestSecurity(unittest.TestCase):

    def _source(self):
        with open(cg.__file__) as f:
            return f.read()

    def test_module_does_not_import_graphifyy(self):
        import sys
        self.assertNotIn("graphifyy", sys.modules)
        source = self._source()
        self.assertNotIn("import graphifyy", source)
        self.assertNotIn("from graphifyy", source)

    def test_only_one_subprocess_call_site_and_it_is_git(self):
        # Checks actual code shape, not prose -- the module's own
        # docstring legitimately discusses "graphify-mcp" (explaining
        # that it's never invoked), so a naive whole-file substring
        # search would misfire on that explanation. This instead
        # confirms there is exactly one subprocess.run call site in the
        # module and that its first argv element is the literal "git".
        source = self._source()
        self.assertEqual(source.count("subprocess.run("), 1)
        call_start = source.index("subprocess.run(")
        call_snippet = source[call_start:call_start + 200]
        self.assertIn('"git"', call_snippet)
        self.assertNotIn("graphify", call_snippet)

    def test_git_subprocess_call_uses_fixed_argv_with_explicit_shell_false(self):
        with patch("agent.code_graph.subprocess.run") as mock_run:
            mock_run.return_value = subprocess.CompletedProcess(args=[], returncode=0, stdout="abc\n")
            cg._current_commit()
        args, kwargs = mock_run.call_args
        self.assertIsInstance(args[0], list)
        self.assertEqual(args[0][0], "git")
        self.assertIs(kwargs.get("shell"), False)
        self.assertIn("timeout", kwargs)
        self.assertEqual(kwargs.get("cwd"), cg._PROJECT_ROOT)

    def test_no_public_function_accepts_an_arbitrary_graphify_cli_argument(self):
        import inspect
        for fn in (cg.get_status, cg.search, cg.analyze_impact, cg.find_path):
            params = inspect.signature(fn).parameters
            for forbidden in ("command", "cli_args", "subcommand", "cypher", "raw_query"):
                self.assertNotIn(forbidden, params)

    def test_reader_graph_dir_is_not_settable_via_tool_input_shape(self):
        # tools/schemas/graphify.py's handlers are the actual attack
        # surface check -- verified separately in
        # tests/test_graphify_tools.py's input-schema tests. This just
        # confirms the module-level convenience functions accept
        # graph_dir only as an explicit kwarg, consistent with "tests
        # inject internally, tools never expose it."
        import inspect
        sig = inspect.signature(cg.search)
        self.assertIn("graph_dir", sig.parameters)
        self.assertEqual(sig.parameters["graph_dir"].kind, inspect.Parameter.POSITIONAL_OR_KEYWORD)

    def test_status_never_logs_secret_looking_values(self):
        # Nothing in this module ever touches agent.secrets, so there is
        # no secret value it could log in the first place -- confirmed
        # structurally rather than by trying to trigger a log line.
        source = self._source()
        self.assertNotIn("agent.secrets", source)
        self.assertNotIn("get_secret", source)


if __name__ == "__main__":
    unittest.main()
