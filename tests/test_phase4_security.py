"""Security-focused integration tests for Phase 4 -- these exercise the
real agent.executor._run_tool funnel point (not just the pure decision
functions in isolation), because the actual guarantee that matters is
"autonomy can't make a dangerous thing happen," not just "the decision
function returns the right enum value."

Run with: python -m unittest tests.test_phase4_security -v
"""
import ast
import inspect
import unittest

import tools.schemas  # noqa: F401 -- populates the registry
from agent.executor import _run_tool
from agent.request_context import RequestContext
from tools import registry


class TestAutonomyCannotBypassHardGates(unittest.TestCase):
    """The hard gates (requires_live_confirmation, unattended_allowed) are
    checked before autonomy in _run_tool and don't take autonomy_level as
    input at all -- these tests confirm that ordering holds even at the
    most permissive autonomy level."""

    def test_confirm_login_still_blocked_unattended_at_max_autonomy(self):
        ctx = RequestContext.create("x", source="scheduled")
        ctx.autonomy_level = 4
        result = _run_tool("confirm_login", {"site": "test"}, source="scheduled", context=ctx)
        self.assertIn("Skipped", result)
        self.assertIn("live conversation", result)

    def test_send_email_still_blocked_unattended_at_max_autonomy(self):
        ctx = RequestContext.create("x", source="scheduled")
        ctx.autonomy_level = 4
        result = _run_tool("send_email", {"to": "test@example.com"}, source="scheduled", context=ctx)
        self.assertIn("Skipped", result)

    def test_computer_use_family_still_blocked_unattended_at_max_autonomy(self):
        ctx = RequestContext.create("x", source="scheduled")
        ctx.autonomy_level = 4
        for tool_name in ("computer_see", "computer_click", "computer_confirm_action"):
            with self.subTest(tool=tool_name):
                result = _run_tool(tool_name, {"x": 0, "y": 0}, source="scheduled", context=ctx)
                self.assertIn("Skipped", result)

    def test_hard_gates_apply_regardless_of_autonomy_level(self):
        for level in range(5):
            with self.subTest(autonomy_level=level):
                ctx = RequestContext.create("x", source="scheduled")
                ctx.autonomy_level = level
                result = _run_tool("confirm_login", {"site": "test"}, source="scheduled", context=ctx)
                self.assertIn("Skipped", result)


class TestComputerConfirmActionAlwaysConfirms(unittest.TestCase):
    """computer_confirm_action is permission_level 5 -- at every defined
    autonomy level (0-4), the threshold never reaches 5, so this must
    always require confirmation in live chat too, not just when
    unattended."""

    def test_always_requires_confirmation_in_chat_at_every_autonomy_level(self):
        for level in range(5):
            with self.subTest(autonomy_level=level):
                ctx = RequestContext.create("x", source="chat")
                ctx.autonomy_level = level
                result = _run_tool(
                    "computer_confirm_action",
                    {"description": f"test action level {level}"},
                    source="chat", context=ctx,
                )
                self.assertIn("OK first", result)


class TestModelCannotGrantItselfPermission(unittest.TestCase):
    """The model can only ever request a tool by name/input -- there's no
    parameter, field, or code path by which a tool call can specify or
    override its own permission level or autonomy outcome."""

    def test_dispatch_ignores_any_extra_input_fields(self):
        # Even if a tool_input dict smuggled in something like
        # {"permission_level": 0} or {"autonomy_level": 4}, dispatch()
        # only ever passes recognized fields to the real handler --
        # registry.py's handlers are fixed lambdas/functions with a
        # specific, hardcoded signature per tool, not driven by
        # arbitrary input keys.
        result = registry.dispatch("get_weather", {
            "location": "Tampa",
            "permission_level": 0,
            "autonomy_level": 99,
            "bypass_confirmation": True,
        })
        # Didn't raise, and (more importantly) get_weather's handler
        # signature only ever reads "location" -- the extra keys are
        # simply never looked at, by construction.
        self.assertIsInstance(result, str)

    def test_tool_input_cannot_change_its_own_permission_level(self):
        level_with_junk = registry.permission_level("computer_confirm_action")
        # permission_level() takes only a tool NAME -- there's no
        # tool_input parameter for it to read in the first place.
        self.assertEqual(level_with_junk, 5)


class TestPlannerCannotExecuteTools(unittest.TestCase):
    """Structural checks, not just behavioral ones -- confirms the
    planner module has no way to dispatch a tool even if its own logic
    had a bug, because it never imports the machinery that would let it."""

    def test_planner_module_does_not_import_executor(self):
        import agent.planner as planner_module
        source = inspect.getsource(planner_module)
        self.assertNotIn("import agent.executor", source)
        self.assertNotIn("from agent.executor", source)

    def test_planner_module_has_no_dispatch_call_anywhere(self):
        # create_plan() does read tools.registry.all_names() (just to
        # list real tool names in its prompt, for accurate required_tools
        # guesses) -- read-only, and no relation to executing anything.
        # This checks the actual execution primitive, dispatch(), is
        # never called anywhere in the module.
        import agent.planner as planner_module
        tree = ast.parse(inspect.getsource(planner_module))
        dispatch_calls = [
            node for node in ast.walk(tree)
            if isinstance(node, ast.Attribute) and node.attr == "dispatch"
        ]
        self.assertEqual(dispatch_calls, [])


class TestDestructiveToolsRemainProtected(unittest.TestCase):

    def test_computer_confirm_action_requires_prior_description_confirmation(self):
        # Calling it fresh (no prior request_confirmation) must not
        # execute, regardless of autonomy.
        ctx = RequestContext.create("x", source="chat")
        ctx.autonomy_level = 4
        result = _run_tool(
            "computer_confirm_action",
            {"description": "delete something important"},
            source="chat", context=ctx,
        )
        self.assertNotIn("Confirmed action executed", result)

    def test_permission_level_5_never_auto_allowed_at_any_defined_level(self):
        from agent.autonomy import Decision, should_request_confirmation
        for level in range(5):
            with self.subTest(autonomy_level=level):
                self.assertNotEqual(
                    should_request_confirmation("computer_confirm_action", level),
                    Decision.ALLOW,
                )


if __name__ == "__main__":
    unittest.main()
