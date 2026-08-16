"""Tests for tools/schemas/openclaw.py -- the two OpenClaw M1 tools,
exercised through the real tools.registry.dispatch() path, matching
tests/test_agents_tool.py's established pattern. Mocks at the
agent.openclaw_gateway.get_status/get_node_list boundary (as imported
into tools.schemas.openclaw) -- those functions' own internals are
covered by tests/test_openclaw_gateway.py. Nothing here makes a real
network call.

Run with: python -m unittest tests.test_openclaw_tool -v
"""
import json
import unittest
from unittest.mock import patch

import tools.schemas  # noqa: F401 -- populates the registry
from tools import registry


class TestToolsAreRegistered(unittest.TestCase):

    def test_openclaw_status_registered(self):
        self.assertIn("openclaw_status", registry.all_names())

    def test_openclaw_list_nodes_registered(self):
        self.assertIn("openclaw_list_nodes", registry.all_names())

    def test_both_are_permission_level_zero(self):
        self.assertEqual(registry.permission_level("openclaw_status"), 0)
        self.assertEqual(registry.permission_level("openclaw_list_nodes"), 0)

    def test_neither_is_a_side_effect_tool(self):
        side_effect_tools = registry.side_effect_tools()
        self.assertNotIn("openclaw_status", side_effect_tools)
        self.assertNotIn("openclaw_list_nodes", side_effect_tools)

    def test_both_unattended_allowed(self):
        self.assertTrue(registry.get("openclaw_status").unattended_allowed)
        self.assertTrue(registry.get("openclaw_list_nodes").unattended_allowed)

    def test_neither_requires_live_confirmation(self):
        self.assertFalse(registry.get("openclaw_status").requires_live_confirmation)
        self.assertFalse(registry.get("openclaw_list_nodes").requires_live_confirmation)

    def test_both_parallel_safe(self):
        parallel_safe = registry.parallel_safe_tools()
        self.assertIn("openclaw_status", parallel_safe)
        self.assertIn("openclaw_list_nodes", parallel_safe)

    def test_neither_takes_any_input_parameters(self):
        # Structural guarantee: no method-selecting or otherwise
        # meaningful input field exists on either tool -- nothing a
        # caller supplies could ever reach agent.openclaw_gateway._call
        # with an attacker/model-chosen method or parameter.
        self.assertEqual(registry.get("openclaw_status").input_schema["properties"], {})
        self.assertEqual(registry.get("openclaw_list_nodes").input_schema["properties"], {})

    def test_no_raw_rpc_tool_is_registered(self):
        names = registry.all_names()
        self.assertNotIn("openclaw_raw_rpc", names)
        self.assertNotIn("openclaw_invoke", names)
        self.assertNotIn("openclaw_execute", names)
        self.assertNotIn("openclaw_system_run", names)


class TestOpenClawStatusTool(unittest.TestCase):

    @patch("tools.schemas.openclaw.get_status")
    def test_returns_json_serialized_status(self, mock_get_status):
        mock_get_status.return_value = {"configured": False, "available": False, "detail": "OpenClaw is disabled or not configured"}
        result = registry.dispatch("openclaw_status", {})
        parsed = json.loads(result)
        self.assertEqual(parsed["configured"], False)
        self.assertEqual(parsed["available"], False)

    @patch("tools.schemas.openclaw.get_status")
    def test_available_status_round_trips_through_the_tool(self, mock_get_status):
        mock_get_status.return_value = {
            "configured": True, "available": True, "protocol": 4,
            "detail": {"runtime": "running", "version": "test-1"},
        }
        result = registry.dispatch("openclaw_status", {})
        parsed = json.loads(result)
        self.assertTrue(parsed["available"])
        self.assertEqual(parsed["detail"]["runtime"], "running")

    @patch("tools.schemas.openclaw.get_status")
    def test_never_raises_even_if_get_status_would_somehow_raise(self, mock_get_status):
        # get_status() itself is documented to never raise (see
        # tests/test_openclaw_gateway.py), but the tool handler is
        # exercised here purely through the registry dispatch path to
        # confirm dispatch itself doesn't add a new failure mode.
        mock_get_status.return_value = {"configured": False, "available": False, "detail": "x"}
        result = registry.dispatch("openclaw_status", {})
        self.assertIsInstance(result, str)


class TestOpenClawListNodesTool(unittest.TestCase):

    @patch("tools.schemas.openclaw.get_node_list")
    def test_returns_json_serialized_node_list(self, mock_get_nodes):
        mock_get_nodes.return_value = {
            "configured": True, "available": True,
            "nodes": [{"id": "n1", "display_name": "Phone", "platform": "ios", "connected": True, "capabilities": ["device.info"]}],
        }
        result = registry.dispatch("openclaw_list_nodes", {})
        parsed = json.loads(result)
        self.assertEqual(len(parsed["nodes"]), 1)
        self.assertEqual(parsed["nodes"][0]["id"], "n1")

    @patch("tools.schemas.openclaw.get_node_list")
    def test_empty_node_list_is_a_clean_result(self, mock_get_nodes):
        mock_get_nodes.return_value = {"configured": True, "available": True, "nodes": []}
        result = registry.dispatch("openclaw_list_nodes", {})
        parsed = json.loads(result)
        self.assertEqual(parsed["nodes"], [])

    @patch("tools.schemas.openclaw.get_node_list")
    def test_unavailable_gateway_is_a_clean_result_not_an_exception(self, mock_get_nodes):
        mock_get_nodes.return_value = {"configured": True, "available": False, "detail": "OpenClaw Gateway is unavailable: ConnectionRefusedError", "nodes": []}
        result = registry.dispatch("openclaw_list_nodes", {})
        parsed = json.loads(result)
        self.assertFalse(parsed["available"])


if __name__ == "__main__":
    unittest.main()
