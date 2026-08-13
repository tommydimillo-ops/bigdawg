"""Regression tests for the tool registry (tools/registry.py) introduced
in the Phase 1 refactor -- this replaced the old three-way split where the
same tools were declared separately in agent/brain.py (schemas),
agent/permissions.py (levels), and agent/executor.py (dispatch). These
tests exercise the real registry (populated by importing tools.schemas,
same as the live app does), not a mock of it.

Run with: python -m unittest tests.test_registry -v
"""
import unittest

import tools.schemas  # noqa: F401 -- populates the registry
from agent.brain import TOOLS
from agent.executor import _run_tool
from tools import registry

# Known consequential tools that must never fire without a live human
# saying yes first -- if this set ever shrinks unexpectedly, that's a
# real safety regression, not just a count changing.
EXPECTED_REQUIRES_CONFIRMATION = {"confirm_login", "send_email"}

# The whole computer_* family must be blocked from unattended execution --
# real, unsupervised screen control is a different risk than an ordinary
# background tool call, even for its "non-consequential" actions.
EXPECTED_NO_UNATTENDED = {
    "computer_see", "computer_locate", "computer_click",
    "computer_type", "computer_press_key", "computer_confirm_action",
}


class TestEveryToolIsRegistered(unittest.TestCase):

    def test_at_least_the_known_tool_count(self):
        # Not a hardcoded exact-45 check -- new tools legitimately get
        # added over time. This just guards against the registry coming
        # back suspiciously empty (e.g. tools.schemas failed to import).
        self.assertGreaterEqual(len(registry.all_names()), 45)

    def test_brain_tools_matches_registry_exactly(self):
        # agent.brain.TOOLS is derived from the registry -- this would
        # only fail if that derivation itself were broken.
        self.assertEqual({t["name"] for t in TOOLS}, set(registry.all_names()))

    def test_full_coverage_check_passes(self):
        registry.check_full_coverage([t["name"] for t in TOOLS])

    def test_registering_a_duplicate_name_fails_loudly(self):
        spec = registry.ToolSpec(
            name="get_system_status",  # already registered
            description="dup",
            input_schema={"type": "object", "properties": {}, "required": []},
            permission_level=0,
            handler=lambda ti: "dup",
        )
        with self.assertRaises(ValueError):
            registry.register(spec)


class TestEveryToolHasAValidSchema(unittest.TestCase):

    def test_every_schema_has_required_fields(self):
        for spec in registry.all_specs():
            with self.subTest(tool=spec.name):
                self.assertTrue(spec.description.strip(), f"{spec.name} has an empty description")
                self.assertIsInstance(spec.input_schema, dict)
                self.assertEqual(spec.input_schema.get("type"), "object")
                self.assertIn("properties", spec.input_schema)
                self.assertIn("required", spec.input_schema)

    def test_required_fields_are_declared_properties(self):
        # A schema that requires a field it never declared would confuse
        # the model into never being able to satisfy it.
        for spec in registry.all_specs():
            with self.subTest(tool=spec.name):
                required = spec.input_schema.get("required", [])
                properties = spec.input_schema.get("properties", {})
                for field in required:
                    self.assertIn(field, properties, f"{spec.name} requires '{field}' but never declares it")

    def test_anthropic_and_openai_schema_shapes(self):
        anthropic = registry.anthropic_schemas()
        openai = registry.openai_schemas()
        self.assertEqual(len(anthropic), len(registry.all_names()))
        self.assertEqual(len(openai), len(registry.all_names()))
        for tool in anthropic:
            self.assertIn("name", tool)
            self.assertIn("description", tool)
            self.assertIn("input_schema", tool)
        for tool in openai:
            self.assertEqual(tool["type"], "function")
            self.assertIn("name", tool["function"])
            self.assertIn("parameters", tool["function"])


class TestEveryToolHasAPermissionLevel(unittest.TestCase):

    def test_every_tool_has_a_valid_level(self):
        for spec in registry.all_specs():
            with self.subTest(tool=spec.name):
                self.assertIn(spec.permission_level, range(6), f"{spec.name} has an out-of-range level")

    def test_permission_label_matches_level(self):
        for spec in registry.all_specs():
            with self.subTest(tool=spec.name):
                label = registry.permission_label(spec.name)
                self.assertTrue(label.startswith(f"L{spec.permission_level} ("))

    def test_unregistered_tool_is_unclassified(self):
        self.assertEqual(registry.permission_label("not_a_real_tool"), "UNCLASSIFIED")
        self.assertIsNone(registry.permission_level("not_a_real_tool"))


class TestEveryHandlerResolves(unittest.TestCase):

    def test_every_handler_is_callable(self):
        for spec in registry.all_specs():
            with self.subTest(tool=spec.name):
                self.assertTrue(callable(spec.handler))

    def test_dispatch_reaches_a_real_read_only_tool(self):
        # Full round trip through registry.dispatch, not just checking the
        # handler reference exists.
        result = registry.dispatch("get_system_status", {})
        self.assertIsInstance(result, str)
        self.assertTrue(result)

    def test_dispatch_unknown_tool_returns_message_not_exception(self):
        result = registry.dispatch("not_a_real_tool", {})
        self.assertEqual(result, "Unknown tool: not_a_real_tool")


class TestConsequentialToolsRequireConfirmation(unittest.TestCase):

    def test_known_consequential_tools_are_flagged(self):
        for name in EXPECTED_REQUIRES_CONFIRMATION:
            with self.subTest(tool=name):
                self.assertTrue(registry.requires_live_confirmation(name))

    def test_ordinary_tools_are_not_flagged(self):
        self.assertFalse(registry.requires_live_confirmation("get_weather"))
        self.assertFalse(registry.requires_live_confirmation("add_reminder"))

    def test_scheduled_source_blocks_confirmation_tools(self):
        for name in EXPECTED_REQUIRES_CONFIRMATION:
            with self.subTest(tool=name):
                result = _run_tool(name, {"site": "x", "to": "x"}, source="scheduled")
                self.assertIn("Skipped", result)


class TestUnattendedExecutionRestrictions(unittest.TestCase):

    def test_computer_use_family_blocked_from_unattended(self):
        for name in EXPECTED_NO_UNATTENDED:
            with self.subTest(tool=name):
                self.assertFalse(registry.unattended_allowed(name))

    def test_ordinary_tools_allowed_unattended(self):
        self.assertTrue(registry.unattended_allowed("get_weather"))
        self.assertTrue(registry.unattended_allowed("add_reminder"))

    def test_scheduled_source_blocks_computer_use(self):
        for name in EXPECTED_NO_UNATTENDED:
            with self.subTest(tool=name):
                result = _run_tool(name, {}, source="scheduled")
                self.assertIn("can't run unattended", result)

    def test_unregistered_tool_defaults_to_unattended_allowed(self):
        # Fails open for lookup purposes (matches permission_level's
        # None-for-unknown behavior) -- actual dispatch of an unknown tool
        # is separately handled by dispatch()'s "Unknown tool" message.
        self.assertTrue(registry.unattended_allowed("not_a_real_tool"))


if __name__ == "__main__":
    unittest.main()
